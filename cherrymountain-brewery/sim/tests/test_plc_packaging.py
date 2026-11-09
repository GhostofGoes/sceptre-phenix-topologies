"""The packaging federate with a PLC in the loop.

plc-packaging relays the operator's line command to every machine for one
scan and opens the filler's product valve while the filler executes
(``plc/plc-packaging/plc-packaging.st``). These tests run the federate against
a Python port of that logic, with an operator who resets and starts the line.
"""

from __future__ import annotations

import json
from pathlib import Path

from cherrymountain_sim.federate import HelicsConfig, ScenarioEvent, Value, build
from cherrymountain_sim.packaging import STOP_JAM, PackagingFederate
from cherrymountain_sim.packml import Command, State

FULL: Path = Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"
MACHINES: tuple[str, ...] = ("rinser", "filler", "coder", "labeler", "packer", "kegger")
LINE_SPEED_BPM: float = 30.0


class Plc:
    """plc-packaging.st, one call of :meth:`scan` per 1 s federate step."""

    line_cmd: int
    keg_heat: bool

    def __init__(self) -> None:
        self.line_cmd = 0
        self.keg_heat = False

    def scan(self, values: dict[str, Value]) -> dict[str, Value]:
        """One scan: read the field values, return every output."""

        def state(machine: str) -> int:
            return int(float(values[f"packaging/{machine}.state"]))

        cmd, self.line_cmd = self.line_cmd, 0
        out: dict[str, Value] = {}
        for machine in MACHINES:
            out[f"{machine}.cmd"] = float(cmd)
            out[f"{machine}.mode_cmd"] = 1.0
        for machine in MACHINES[:5]:
            out[f"{machine}.speed_sp_bpm"] = LINE_SPEED_BPM
        feeds = {"conv1": "rinser", "conv2": "coder", "conv3": "packer", "conv4": "packer"}
        for conveyor, machine in feeds.items():
            jam = bool(values[f"packaging/{conveyor}.jam_eye"])
            out[f"{conveyor}.run"] = state(machine) == State.EXECUTE and not jam
        out["filler.product_valve"] = state("filler") == State.EXECUTE
        wash_f = float(values["packaging/kegger.wash_temp_f"])
        if wash_f < 165.0:
            self.keg_heat = True
        elif wash_f >= 175.0:
            self.keg_heat = False
        out["kegger.heater"] = self.keg_heat
        return out


class Rig:
    """The packaging federate and the PLC."""

    fed: PackagingFederate
    plc: Plc

    def __init__(self) -> None:
        fed = build(HelicsConfig.load(FULL), "packaging")
        assert isinstance(fed, PackagingFederate)
        self.fed = fed
        self.plc = Plc()

    def scan(self, seconds: int = 1) -> None:
        """PLC scans and federate steps, as rio-packaging relays them."""
        for _ in range(seconds):
            writes = self.plc.scan(self.fed.values())
            message = json.dumps([{"tag": tag, "value": value} for tag, value in writes.items()])
            assert len(self.fed.handle_message(message)) == len(writes)
            self.fed.step(1.0)

    def start_line(self) -> None:
        """The operator's Reset, then Start, from the HMI."""
        self.scan(5)
        self.plc.line_cmd = int(Command.RESET)
        self.scan(6)
        self.plc.line_cmd = int(Command.START)
        self.scan(6)

    def state(self, machine: str) -> State:
        """A machine's PackML state."""
        return self.fed.machines[machine].pml.state


def test_the_filler_runs_when_the_plc_opens_its_product_valve() -> None:
    """plc-packaging opens the product valve only once it reads the filler in
    Execute. The filler used to hold on a low bowl in its first second of
    Execute, and the PLC then kept the valve shut because the filler was not
    executing: no bottle was ever filled with a PLC in control."""
    rig = Rig()
    rig.start_line()
    assert not rig.fed.autopilot
    assert rig.state("filler") == State.EXECUTE
    assert rig.fed.product_open
    rig.scan(1800)
    assert rig.fed.bottles_total > 500
    assert rig.fed.machines["filler"].processed > 600


def test_a_low_supply_holds_the_filler_and_it_recovers() -> None:
    """Behind an open valve a low supply holds the filler at once; it unholds
    when BBT-202's pressure is back, although the PLC closed the valve
    meanwhile."""
    rig = Rig()
    rig.start_line()
    rig.scan(60)
    rig.fed.apply_input("cellar/bbt202.pressure_psia", 12.0)
    rig.scan(10)
    assert rig.state("filler") == State.HELD
    assert not rig.fed.product_open
    rig.scan(120)
    assert rig.state("filler") == State.HELD
    rig.fed.apply_input("cellar/bbt202.pressure_psia", 25.0)
    rig.scan(20)
    assert rig.state("filler") == State.EXECUTE
    assert rig.fed.product_open


def test_a_jam_clears_after_its_time_with_a_plc_in_control() -> None:
    """A ``jam`` event jams a machine for ``duration`` seconds. With a PLC in
    control nothing unheld the machine afterwards, and its neighbors stayed
    suspended: the line stopped for good at the first jam."""
    rig = Rig()
    rig.start_line()
    rig.scan(600)
    # No random jams during the test: only the event's.
    for machine in rig.fed.machines.values():
        machine.next_jam = float("inf")
    before = rig.fed.bottles_total
    assert before > 100
    event = ScenarioEvent(type="jam", params={"machine": "labeler", "duration": 120})
    assert rig.fed.dispatch_event(event)
    rig.scan(60)
    labeler = rig.fed.machines["labeler"]
    assert rig.state("labeler") == State.HELD and labeler.stop_reason == STOP_JAM
    assert rig.state("packer") == State.SUSPENDED  # starved behind the jam
    held_at = rig.fed.bottles_total
    rig.scan(50)
    assert rig.fed.bottles_total == held_at
    rig.scan(600)
    assert all(rig.state(m) == State.EXECUTE for m in MACHINES)
    assert rig.fed.bottles_total > held_at + 200


def test_a_commanded_hold_stays_held() -> None:
    """Only a jam or a low bowl is recovered from: a Hold the operator
    commanded stays until the operator's Unhold."""
    rig = Rig()
    rig.start_line()
    rig.scan(120)
    rig.plc.line_cmd = int(Command.HOLD)
    rig.scan(10)
    assert all(rig.state(m) == State.HELD for m in MACHINES)
    rig.scan(300)
    assert all(rig.state(m) == State.HELD for m in MACHINES)
    rig.plc.line_cmd = int(Command.UNHOLD)
    rig.scan(10)
    assert rig.state("filler") == State.EXECUTE
