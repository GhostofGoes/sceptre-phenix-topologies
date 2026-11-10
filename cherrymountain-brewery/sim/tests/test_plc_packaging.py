"""The packaging federate with a PLC in the loop.

plc-packaging relays the operator's line command to every machine for one
scan and opens the filler's product valve while the filler executes
(``plc/plc-packaging/plc-packaging.st``). The command comes from the HMI or
from the operator panel at the filler, where the federate's line operator
starts and stops the shift. These tests run the federate against a Python port
of that logic, and the shift again against the program itself, compiled
(``st_engine.py``, where its toolchain is installed).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import st_engine

from cherrymountain_sim.federate import HelicsConfig, ScenarioEvent, SimClock, Value, build
from cherrymountain_sim.packaging import (
    PANEL_PAUSE_S,
    PANEL_PRESS_S,
    STOP_JAM,
    PackagingFederate,
    PackagingSettings,
)
from cherrymountain_sim.packml import Command, State
from cherrymountain_sim.plcloop import PlcLink

FULL: Path = Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"
MACHINES: tuple[str, ...] = ("rinser", "filler", "coder", "labeler", "packer", "kegger")
LINE_SPEED_BPM: float = 30.0


class Plc:
    """plc-packaging.st, one call of :meth:`scan` per 1 s federate step."""

    line_cmd: int
    panel_last: int
    keg_heat: bool

    def __init__(self) -> None:
        self.line_cmd = 0
        self.panel_last = 0
        self.keg_heat = False

    def scan(self, values: dict[str, Value]) -> dict[str, Value]:
        """One scan: read the field values, return every output."""

        def state(machine: str) -> int:
            return int(float(values[f"packaging/{machine}.state"]))

        panel = int(float(values["packaging/linepanel.cmd"]))
        if panel != self.panel_last:
            self.panel_last = panel
            if 1 <= panel <= 9 and self.line_cmd == 0:
                self.line_cmd = panel
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

    def __init__(self, start: str | None = None, **settings: float | str) -> None:
        cfg = HelicsConfig.load(FULL)
        if start is None:
            fed = build(cfg, "packaging")
        else:
            fed = PackagingFederate(
                cfg.federates["packaging"], SimClock(start=dt.datetime.fromisoformat(start))
            )
        assert isinstance(fed, PackagingFederate)
        if settings:
            fed.settings = PackagingSettings.model_validate(
                fed.settings.model_dump() | settings
            )
        self.fed = fed
        self.plc = Plc()
        self.pressed: list[int] = []  # the panel's codes, one entry per press

    def scan(self, seconds: int = 1) -> None:
        """PLC scans and federate steps, as rio-packaging relays them."""
        for _ in range(seconds):
            writes = self.plc.scan(self.fed.values())
            message = json.dumps([{"tag": tag, "value": value} for tag, value in writes.items()])
            assert len(self.fed.handle_message(message)) == len(writes)
            before = self.fed.panel_cmd
            self.fed.step(1.0)
            if self.fed.panel_cmd and self.fed.panel_cmd != before:
                self.pressed.append(self.fed.panel_cmd)

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
    rig.fed.apply_input("cellar/beer.supply_psia", 12.0)
    rig.scan(10)
    assert rig.state("filler") == State.HELD
    assert not rig.fed.product_open
    rig.scan(120)
    assert rig.state("filler") == State.HELD
    rig.fed.apply_input("cellar/beer.supply_psia", 25.0)
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


# ---------------------------------------------------------------- the shift at the panel

#: A Tuesday. The shift is 06:00 to 14:30, Monday to Friday.
BEFORE_SHIFT: str = "2026-07-14T05:58:00"
IN_SHIFT: str = "2026-07-14T09:00:00"
BEFORE_END: str = "2026-07-14T14:28:00"
SATURDAY: str = "2026-07-18T05:58:00"
RESET, START, STOP, CLEAR = (int(c) for c in (Command.RESET, Command.START, Command.STOP, Command.CLEAR))


def test_the_line_operator_starts_the_shift_at_the_panel() -> None:
    """With plc-packaging in control the built-in operator stands down, and the
    PLC only relays a command: nothing started the line. Now the line operator
    presses Reset and Start at the panel when the shift starts."""
    rig = Rig(BEFORE_SHIFT)
    rig.scan(100)
    assert not rig.fed.autopilot and rig.fed.panel_operator
    assert all(rig.state(m) == State.STOPPED for m in MACHINES)
    assert rig.pressed == []  # 05:59:40, not yet
    rig.scan(60)
    assert rig.pressed == [RESET, START]
    assert all(rig.state(m) == State.EXECUTE for m in MACHINES)
    assert rig.fed.panel_cmd == 0  # the button is up again
    rig.scan(1800)
    assert rig.pressed == [RESET, START]  # a running line needs no operator
    assert rig.fed.bottles_total > 500 and rig.fed.shift_bottles == rig.fed.bottles_total
    truth = rig.fed.truth()["packaging"]
    assert (truth["operator"], truth["panel_presses"]) == ("panel", 2)


def test_a_button_is_held_long_enough_for_the_plc_and_then_let_go() -> None:
    rig = Rig(BEFORE_SHIFT)
    held: list[int] = []
    for _ in range(150):
        rig.scan(1)
        held.append(rig.fed.panel_cmd)
    runs = [list(run) for run in "".join(str(c) for c in held).replace("0", " ").split()]
    assert [len(run) for run in runs] == [PANEL_PRESS_S, PANEL_PRESS_S]
    first_up = held.index(RESET) + int(PANEL_PRESS_S)
    assert held[first_up : first_up + int(PANEL_PAUSE_S)] == [0] * int(PANEL_PAUSE_S)
    assert float(rig.fed.values()["packaging/linepanel.cmd"]) == 0.0


def test_the_line_operator_stops_the_line_when_the_shift_ends() -> None:
    rig = Rig(BEFORE_END)
    rig.scan(60)
    assert rig.pressed == [RESET, START]  # the PLC took over during the shift
    assert rig.state("filler") == State.EXECUTE
    rig.scan(120)
    assert rig.pressed == [RESET, START, STOP]
    assert all(rig.state(m) == State.STOPPED for m in MACHINES)
    rig.scan(3600)
    assert rig.pressed == [RESET, START, STOP]  # and the line stays stopped


def test_nobody_starts_the_line_on_a_day_without_a_shift() -> None:
    rig = Rig(SATURDAY)
    rig.scan(600)
    assert rig.pressed == []
    assert all(rig.state(m) == State.STOPPED for m in MACHINES)


def test_a_line_started_from_the_hmi_outside_the_shift_is_left_running() -> None:
    """The operator ends the shift they started: overtime started from the HMI
    is not theirs to stop."""
    rig = Rig("2026-07-14T16:00:00")
    rig.start_line()
    rig.scan(900)
    assert rig.pressed == []
    assert rig.state("filler") == State.EXECUTE


def test_a_line_stopped_during_the_shift_is_started_again_after_a_while() -> None:
    """A Stop from the HMI (or from an attacker's write) stops the line. The
    line operator notices after ``operator_react_s`` and starts it again."""
    rig = Rig(IN_SHIFT)
    rig.scan(60)
    assert rig.state("filler") == State.EXECUTE
    rig.plc.line_cmd = STOP
    rig.scan(20)
    assert all(rig.state(m) == State.STOPPED for m in MACHINES)
    rig.scan(90)
    assert rig.pressed == [RESET, START]  # 110 s after the stop: not noticed yet
    rig.scan(40)
    assert rig.pressed == [RESET, START, RESET, START]
    assert all(rig.state(m) == State.EXECUTE for m in MACHINES)


def test_with_no_reaction_time_the_operator_only_starts_and_ends_the_shift() -> None:
    rig = Rig(IN_SHIFT, operator_react_s=0)
    rig.scan(60)
    rig.plc.line_cmd = STOP
    rig.scan(1800)
    assert rig.pressed == [RESET, START]
    assert all(rig.state(m) == State.STOPPED for m in MACHINES)


def test_after_a_power_cut_the_operator_clears_resets_and_starts_the_line() -> None:
    rig = Rig(IN_SHIFT)
    rig.scan(60)
    for machine in rig.fed.machines.values():
        machine.next_jam = float("inf")  # no random jam while the line starts again
    rig.fed.apply_input("power/bus.pkg.energized", 0.0)
    rig.scan(600)
    assert all(rig.state(m) == State.ABORTED for m in MACHINES)
    assert rig.pressed == [RESET, START]  # nothing to press without power
    rig.fed.apply_input("power/bus.pkg.energized", 1.0)
    rig.scan(100)
    assert rig.pressed == [RESET, START]  # the reaction time runs from the power's return
    rig.scan(60)
    assert rig.pressed == [RESET, START, CLEAR, RESET, START]
    assert all(rig.state(m) == State.EXECUTE for m in MACHINES)


def test_a_hold_commanded_during_the_shift_is_not_the_operators_to_undo() -> None:
    rig = Rig(IN_SHIFT)
    rig.scan(60)
    rig.plc.line_cmd = int(Command.HOLD)
    rig.scan(900)
    assert all(rig.state(m) == State.HELD for m in MACHINES)
    assert rig.pressed == [RESET, START]


def test_the_built_in_operator_does_not_use_the_panel() -> None:
    """Without a PLC the built-in operator commands the machines itself, and
    with ``autopilot: off`` nobody in the simulation runs the line."""
    fed = build(HelicsConfig.load(FULL), "packaging")
    assert isinstance(fed, PackagingFederate)
    fed.clock = SimClock(start=dt.datetime.fromisoformat(BEFORE_SHIFT))
    for _ in range(300):
        fed.step(1.0)
    assert fed.autopilot and fed.machines["filler"].pml.state == State.EXECUTE
    assert (fed.panel_presses, fed.truth()["packaging"]["operator"]) == (0, "autopilot")
    rig = Rig(BEFORE_SHIFT, autopilot="off")
    rig.scan(600)
    assert rig.pressed == [] and rig.fed.truth()["packaging"]["operator"] == "none"
    assert all(rig.state(m) == State.STOPPED for m in MACHINES)


# ---------------------------------------------------------------- the program itself


@pytest.fixture(scope="module")
def st_packaging(tmp_path_factory: pytest.TempPathFactory) -> st_engine.CompiledPlc:
    """plc-packaging's ST compiled by strucpp and g++ (skips without the toolchain)."""
    gap = st_engine.toolchain_gap()
    if gap:
        pytest.skip(f"compiled ST unavailable: {gap}")
    return st_engine.CompiledPlc.build(
        "plc-packaging", "full", tmp_path_factory.mktemp("plc-packaging")
    )


class CompiledRig:
    """The packaging federate and the compiled program, joined by plantgen's tag
    table: one scan per federate second (``plcloop.py``)."""

    def __init__(self, plc: st_engine.CompiledPlc, start: str) -> None:
        cfg = HelicsConfig.load(FULL)
        self.fed = PackagingFederate(
            cfg.federates["packaging"], SimClock(start=dt.datetime.fromisoformat(start))
        )
        self.plc = plc.fresh()
        self.link = PlcLink("plc-packaging", self.plc, FULL.parent / "tags.json")
        self.t = 0

    def scan(self, seconds: int = 1) -> None:
        for _ in range(seconds):
            self.link.scan(self.fed.values(), {"packaging": self.fed}, float(self.t))
            self.fed.step(1.0)
            self.t += 1

    def states(self) -> set[State]:
        return {m.pml.state for m in self.fed.machines.values()}


def test_the_compiled_program_takes_the_panels_command(st_packaging: st_engine.CompiledPlc) -> None:
    rig = CompiledRig(st_packaging, BEFORE_SHIFT)
    assert any(p.var == "PKG_PANEL_HS01_PV" and p.direction == "in" for p in rig.link.points)
    rig.scan(100)
    assert rig.states() == {State.STOPPED} and rig.fed.panel_presses == 0
    rig.scan(80)
    assert rig.states() == {State.EXECUTE}
    assert rig.fed.panel_presses == 2
    assert rig.plc.get("PKG_FILLER_XV01_CMD") == 1 and rig.plc.get("PKG_LINE_XS01_RUN") == 1
    rig.scan(900)
    assert rig.fed.bottles_total > 200


def test_a_held_button_is_one_command_and_the_hmi_goes_first(st_packaging: st_engine.CompiledPlc) -> None:
    plc = st_packaging.fresh()
    given: list[int] = []

    def scan(panel: int, hmi: int = 0) -> None:
        plc.set("PKG_PANEL_HS01_PV", panel)
        if hmi:
            plc.set("PKG_LINE_PACKML_CMD", hmi)
        plc.scan(len(given) * 100_000_000)
        given.append(plc.get("PKG_FILLER_PACKML_CMD"))

    for _ in range(3):
        scan(0)
    for _ in range(40):  # the button stays down four seconds
        scan(START)
    for _ in range(10):
        scan(0)
    # one command, on the machines for CMD_SCANS scans and one more, then gone
    assert given == [0] * 3 + [START] * 6 + [0] * 44
    assert plc.get("PKG_LINE_PACKML_CMD") == 0
    given.clear()
    scan(STOP, hmi=int(Command.HOLD))  # both in one scan
    assert given == [int(Command.HOLD)]
    for _ in range(10):
        scan(STOP)
    assert STOP not in given  # the press was spent
    scan(0)
    scan(13)  # not a PackML command
    scan(0)
    assert given[-2:] == [0, 0]
