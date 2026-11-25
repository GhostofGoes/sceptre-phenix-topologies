"""The PLC-in-the-loop link of the offline federation (``cherrymountain_sim.plcloop``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cherrymountain_sim.federate import HelicsConfig
from cherrymountain_sim.local import LocalFederation
from cherrymountain_sim.plcloop import PlcLink, PlcPoint

GENERATED: Path = Path(__file__).resolve().parents[2] / "plant" / "generated"


class RecordingPlc:
    """An engine that records what it is given and answers from a table."""

    inputs: dict[str, int]
    answers: dict[str, int]
    scans: list[int]

    def __init__(self, answers: dict[str, int]) -> None:
        self.inputs = {}
        self.answers = answers
        self.scans = []

    def set(self, name: str, value: int | bool) -> None:
        self.inputs[name] = int(value)

    def get(self, name: str) -> int:
        return self.answers.get(name, 0)

    def scan(self, time_ns: int) -> None:
        self.scans.append(time_ns)


def test_tag_directions_and_scaling_come_from_the_tag_table() -> None:
    """Field points the PLC reads are inputs (also a read-write point that another
    host writes, like the utility's BESS dispatch); points it writes are outputs;
    memory tags (no field point) are not exchanged."""
    link = PlcLink("plc-power", RecordingPlc({}), GENERATED / "full" / "tags.json")
    by_var = {p.var: p for p in link.points}
    assert by_var["PWR_GEN_KI01_PV"].direction == "in" and not by_var["PWR_GEN_KI01_PV"].signed
    assert by_var["PWR_BESS_JT01_PV"].signed and by_var["PWR_BESS_JT01_PV"].scaling == 1
    assert by_var["PWR_BESS_JC02_SP"].direction == "in"  # written by rtu-der
    assert by_var["PWR_BESS_JC01_SP"].direction == "out"
    assert by_var["PWR_GEN_HS01_CMD"].target == "gen-ats.gen_start"
    assert not any(v.startswith("PWR_DMD_") for v in by_var)  # PLC memory
    assert not any("_ALM_" in v for v in by_var)


def test_register_conversions() -> None:
    """Analog values times 10^scaling, rounded, held to the register's range;
    signed registers keep their sign; bits are 0 and 1; and back."""
    unsigned = PlcPoint(var="V", direction="in", analog=True, signed=False, scaling=1, key="k", federate="f", target="d.p")
    signed = unsigned.model_copy(update={"signed": True})
    bit = unsigned.model_copy(update={"analog": False})
    assert unsigned.to_register(12.34) == 123 and unsigned.to_register(-5.0) == 0
    assert unsigned.to_register(1e6) == 65535
    assert signed.to_register(-12.34) == -123 and signed.to_register(1e6) == 32767
    assert signed.to_register(-1e6) == -32768
    assert bit.to_register(True) == 1 and bit.to_register(0.0) == 0
    assert signed.from_register(-123) == pytest.approx(-12.3) and bit.from_register(1) is True


def test_a_step_reads_published_values_scans_and_writes_outputs() -> None:
    """After the federation has published, each step the PLC reads the previous
    step's values, scans at the federation time, and its outputs reach the federate
    before it steps; nothing is scanned before the first publication."""
    full = HelicsConfig.load(GENERATED / "full" / "helics.json")
    plc = RecordingPlc({"PWR_GEN_HS01_CMD": 1, "PWR_BESS_JC01_SP": -250})
    lf = LocalFederation(full, ["power"], [PlcLink("plc-power", plc, GENERATED / "full" / "tags.json")])
    lf.step(60.0)
    assert plc.scans == []  # the first step publishes
    lf.step(60.0)
    assert plc.scans == [60 * 10**9]
    assert plc.inputs["PWR_GEN_KI01_PV"] == 270 + 1  # 04:31 after the first step
    assert plc.inputs["PWR_GEN_KI03_PV"] == 7
    assert 600 <= plc.inputs["PWR_BESS_EY01_PV"] <= 610  # 60 %, charging overnight
    power = lf.feds["power"]
    assert power.commands["gen-ats.gen_start"] is True
    assert power.commands["bess-pcs.p_sp_kw"] == pytest.approx(-25.0)
