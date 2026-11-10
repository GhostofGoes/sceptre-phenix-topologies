"""plc-utilities' CIP sequence, in the program itself.

The full and lite plants' program (``plc/plc-utilities/plc-utilities.st``) is
compiled by strucpp and g++ (``st_engine.py``) and scanned here with its inputs
set by hand. The tests skip where the toolchain is not installed.
"""

from __future__ import annotations

import pytest
import st_engine

SCAN_NS: int = 100_000_000  # the task interval: 100 ms
START, HOLD, ABORT, RESUME = 1, 2, 3, 0
STEP_S: int = 300  # each wash step (CIP_STEP_TIME)
PROVE_S: int = 30  # the return flow switch must make within this (CIP_FLOW_PROVE)
DONE: int = 7


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> st_engine.CompiledPlc:
    gap = st_engine.toolchain_gap()
    if gap:
        pytest.skip(f"compiled ST unavailable: {gap}")
    return st_engine.CompiledPlc.build("plc-utilities", "full", tmp_path_factory.mktemp("plc-utilities"))


class Bench:
    """A cold-started plc-utilities whose inputs the test sets."""

    def __init__(self, template: st_engine.CompiledPlc) -> None:
        self.plc = template.fresh()
        self.ns = 0
        self.pump_stops = 0
        self.scan(3)

    def scan(self, scans: int = 1, **inputs: int) -> None:
        for name, value in inputs.items():
            self.plc.set(name, value)
        for _ in range(scans):
            before = self.plc.get("UTL_CIP_HS02_CMD")
            self.plc.scan(self.ns)
            self.ns += SCAN_NS
            if before and not self.plc.get("UTL_CIP_HS02_CMD"):
                self.pump_stops += 1

    def seconds(self, s: float, **inputs: int) -> None:
        self.scan(round(s * 10), **inputs)

    def line_up(self, circuit: int = 2, flow: bool = True) -> None:
        """Swing the panel onto a circuit (2 is FV-101), with the cellar's permits,
        and say whether the return flow switch makes while the pumps run."""
        self.scan(
            2,
            UTL_CIP_XC01_CIRCUIT=circuit,
            **{f"UTL_CIP_ZS0{circuit}_PV": 1},
            UTL_CIP_XS04_PV=1,  # the cellar permits CIP
            **{f"UTL_CIP_XS{circuit + 3:02d}_PV": 1},  # the vessel is not in entry
            UTL_CIP_FS01_PV=int(flow),
        )

    def command(self, value: int) -> None:
        self.scan(2, UTL_CIP_XC01_CMD=value)

    @property
    def step(self) -> int:
        return self.plc.get("UTL_CIP_XS01_STEP")

    @property
    def pumps(self) -> tuple[int, int]:
        return self.plc.get("UTL_CIP_HS01_CMD"), self.plc.get("UTL_CIP_HS02_CMD")

    @property
    def flow_lost(self) -> tuple[int, int]:
        """The held-for-no-flow state and its alarm bit."""
        return self.plc.get("UTL_CIP_XA01_PV"), self.plc.get("UTL_CIP_XA01_ALM_STATE")


def test_a_wash_with_return_flow_runs_its_six_steps(template: st_engine.CompiledPlc) -> None:
    b = Bench(template)
    b.line_up()
    assert b.plc.get("UTL_CIP_XS02_ROUTE_OK") == 1
    b.command(START)
    assert b.step == 1 and b.pumps == (1, 1)
    seen: list[int] = [b.step]
    for _ in range(6 * STEP_S + 20):
        b.seconds(1)
        if b.step != seen[-1]:
            seen.append(b.step)
    assert seen == [1, 2, 3, 4, 5, 6, DONE]
    assert b.pumps == (0, 0) and b.pump_stops == 1 and b.flow_lost == (0, 0)


def test_a_wash_with_no_return_flow_is_held_and_stays_held(template: st_engine.CompiledPlc) -> None:
    """The hold used to release itself: it stopped the return pump, which made its
    own condition false, so the pumps started again every 30 s for good, the step
    never advanced, and nothing alarmed."""
    b = Bench(template)
    b.line_up(flow=False)
    b.command(START)
    b.seconds(PROVE_S - 2)
    assert b.pumps == (1, 1) and b.flow_lost == (0, 0)  # the switch still has time to make
    b.seconds(4)
    assert b.pumps == (0, 0) and b.flow_lost == (1, 1)
    b.seconds(2 * STEP_S)
    assert b.pumps == (0, 0) and b.flow_lost == (1, 1) and b.step == 1
    assert b.pump_stops == 1
    # the flow switch making now changes nothing: the pumps are off
    b.seconds(60, UTL_CIP_FS01_PV=1)
    assert b.pumps == (0, 0) and b.flow_lost == (1, 1)


def test_hold_and_resume_try_a_held_wash_again(template: st_engine.CompiledPlc) -> None:
    b = Bench(template)
    b.line_up(flow=False)
    b.command(START)
    b.seconds(PROVE_S + 5)
    assert b.flow_lost == (1, 1)
    b.command(HOLD)  # the operator has seen it and found the closed valve
    assert b.flow_lost == (0, 0) and b.pumps == (0, 0)
    b.plc.set("UTL_CIP_FS01_PV", 1)
    b.command(RESUME)
    assert b.pumps == (1, 1)
    b.seconds(STEP_S + 5)
    assert b.step == 2 and b.flow_lost == (0, 0)
    # and a resume that still has no flow is held again
    b.command(HOLD)
    b.plc.set("UTL_CIP_FS01_PV", 0)
    b.command(RESUME)
    b.seconds(PROVE_S + 5)
    assert b.pumps == (0, 0) and b.flow_lost == (1, 1) and b.step == 2


def test_an_abort_ends_a_held_wash(template: st_engine.CompiledPlc) -> None:
    b = Bench(template)
    b.line_up(flow=False)
    b.command(START)
    b.seconds(PROVE_S + 5)
    assert b.flow_lost == (1, 1)
    b.command(ABORT)
    assert b.step == 0 and b.pumps == (0, 0) and b.flow_lost == (0, 0)


def test_flow_lost_in_the_middle_of_a_wash_holds_it(template: st_engine.CompiledPlc) -> None:
    b = Bench(template)
    b.line_up()
    b.command(START)
    b.seconds(STEP_S + 60)
    assert b.step == 2 and b.plc.get("UTL_CIP_XV01_CMD") == 1  # caustic
    b.seconds(PROVE_S - 5, UTL_CIP_FS01_PV=0)
    assert b.pumps == (1, 1)
    # a switch that chatters for less than the proving time is not a lost flow
    b.seconds(10, UTL_CIP_FS01_PV=1)
    b.seconds(PROVE_S - 5, UTL_CIP_FS01_PV=0)
    assert b.pumps == (1, 1) and b.flow_lost == (0, 0)
    b.seconds(10)
    assert b.pumps == (0, 0) and b.flow_lost == (1, 1)
    assert b.plc.get("UTL_CIP_XV01_CMD") == 0 and b.plc.get("UTL_CIP_TV01_OUT") == 0


@pytest.mark.parametrize("circuit", [8, 9, 9999, 65535])
def test_a_number_that_is_no_circuit_selects_none(template: st_engine.CompiledPlc, circuit: int) -> None:
    """The selector used to be capped at 7, so 8 washed BBT-202 when its panel
    was on."""
    b = Bench(template)
    b.scan(2, UTL_CIP_ZS07_PV=1, UTL_CIP_XS04_PV=1, UTL_CIP_XS10_PV=1, UTL_CIP_FS01_PV=1)
    b.scan(2, UTL_CIP_XC01_CIRCUIT=circuit)
    assert b.plc.get("UTL_CIP_XC01_CIRCUIT") == 0
    assert b.plc.get("UTL_CIP_XS02_ROUTE_OK") == 0
    b.command(START)
    assert b.step == 0 and b.pumps == (0, 0)
    b.scan(2, UTL_CIP_XC01_CIRCUIT=7)  # the real circuit still works
    b.command(START)
    assert b.step == 1 and b.pumps == (1, 1)


def test_a_start_without_a_route_is_dropped(template: st_engine.CompiledPlc) -> None:
    b = Bench(template)
    b.scan(2, UTL_CIP_XC01_CIRCUIT=2, UTL_CIP_XS04_PV=1, UTL_CIP_XS05_PV=1)  # the panel is not on it
    b.command(START)
    assert b.step == 0 and b.plc.get("UTL_CIP_XC01_CMD") == 0
    b.scan(20, UTL_CIP_ZS02_PV=1)  # swinging the panel later must not start the wash
    assert b.step == 0
