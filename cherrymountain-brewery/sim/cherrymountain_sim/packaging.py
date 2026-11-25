"""Packaging federate: bottling line and keg washer/filler (DESIGN.md §6.3, §9.3).

A SimPy model stepped at 1 s inside the federate. Each machine (rinser,
filler/crowner, date coder, labeler, case packer, keg washer/filler) has a
:class:`~cherrymountain_sim.packml.PackMLMachine`, a nominal rate,
exponential jam intervals (MTBF) and repair times (MTTR), and accumulation
buffers between machines. A machine with no bottle upstream is starved and
one with a full buffer downstream is blocked; either one suspends it, and it
unsuspends when the cause clears. A jam holds it (an internal stop) until it
is cleared. The filler takes BBT-202's pressure through its product valve
and holds on a low bowl (DESIGN.md §6.6 #7).

The PLC (plc-packaging) commands each machine through its ``cmd`` register
(PackTags CntrlCmd, applied when the value changes), its mode, and its speed.
Until the PLC writes, a built-in operator runs the packaging shift
(``shift_start`` to ``shift_end`` on weekdays), clears jams, and recovers from
power loss with legal PackML commands only (Abort, Clear, Reset, Start).

**Fill level and net contents** (DESIGN.md §18A). Each bottle's fill is drawn
from a normal distribution around the filler's target. The fill-level
inspector rejects a bottle whose fill is more than the NIST Handbook 133
maximum allowable variation (0.5 fl oz for a 12 fl oz package) below the
labeled contents; those rejects are the filler's ``ProdDefectiveCount``. The
federate keeps the lot statistics a net-contents check uses (count, mean,
standard deviation, rejects) and the quarterly barrel-equivalent variance
that 27 CFR 25.142(d) holds to 0.5 %, and it states packaged beer in TTB
barrels the way 27 CFR 25.158 does (a 24 x 12 fl oz case is 0.07258 barrel).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Generator
from typing import Any, ClassVar, Literal

import simpy
from pydantic import BaseModel, Field

from cherrymountain_sim.federate import (
    Federate,
    FederateConfig,
    Publication,
    ScenarioEvent,
    SimClock,
    Value,
)
from cherrymountain_sim.packml import (
    Command,
    IllegalTransition,
    Mode,
    PackMLMachine,
    State,
)

LOG: logging.Logger = logging.getLogger("cherrymountain_sim.packaging")

#: Stop reasons (PackTags StopReason.ID).
STOP_NONE: int = 0
STOP_JAM: int = 10
STOP_SUPPLY: int = 20
STOP_STARVED: int = 30
STOP_BLOCKED: int = 40
STOP_OPERATOR: int = 50
#: Bottles per standard US beer barrel (3,968 oz / 12 oz).
BOTTLES_PER_BBL: float = 3968.0 / 12.0
#: Labeled net contents of a bottle, fl oz.
LABELED_FLOZ: float = 12.0
#: NIST Handbook 133 (2026), Appendix A, Table 2-6: the maximum allowable
#: variation for a package labeled more than 11.75 to 17.00 fl oz is 0.5 fl oz.
MAV_FLOZ: float = 0.5
#: 27 CFR 25.158(a): barrel equivalent of a case of 24 twelve-ounce bottles.
BBL_PER_CASE_24X12: float = 0.07258
#: 27 CFR 25.142(d): over a three-month period the barrel equivalent of the
#: bottles filled may not vary from the labeled equivalent by more than 0.5 %.
TTB_NET_CONTENTS_TOLERANCE_PCT: float = 0.5
#: Half-barrel kegs (27 CFR 25.156(a): a keg over 2 % above its rated capacity
#: is taxed on the actual quantity; the model fills to rating).
BBL_PER_KEG: float = 0.5
#: Filler bowl pressure below which the filler holds, psia.
LOW_BOWL_PSIA: float = 18.0
#: Seconds the filler runs on what its bowl holds while the product valve is
#: shut, before it holds on a low bowl. plc-packaging opens the valve only once
#: it reads the filler in Execute, a scan and a poll later; with no allowance
#: the filler held before the valve ever opened, and the PLC then kept the
#: valve shut because the filler was not executing.
BOWL_RESERVE_S: float = 10.0
BOTTLE_LINE: tuple[str, ...] = ("rinser", "filler", "coder", "labeler", "packer")


class MachineSpec(BaseModel):
    """Nominal rate and reliability of one machine."""

    rate_bpm: float
    mtbf_min: float
    mttr_min: float
    reject: float = 0.0
    kw: float = 2.0


class FillSpec(BaseModel):
    """The filler's fill-volume distribution (fl oz). The target sits above the
    label so the lot averages at least the labeled quantity (Handbook 133),
    but not so far above that the quarterly barrel equivalent drifts past the
    0.5 % TTB tolerance (27 CFR 25.142(d)); 12.04 fl oz is 0.33 % over."""

    target_floz: float = 12.04
    sigma_floz: float = 0.20


class FillRecord(BaseModel):
    """Net-contents statistics of the bottles filled (a running lot)."""

    count: int = 0
    sum_floz: float = 0.0
    sumsq_floz: float = 0.0
    under_mav: int = 0
    quarter: str = ""
    quarter_bottles: int = 0
    quarter_sum_floz: float = 0.0

    @property
    def mean_floz(self) -> float:
        """Mean fill of the accepted bottles."""
        return self.sum_floz / self.count if self.count else 0.0

    @property
    def std_floz(self) -> float:
        """Sample standard deviation of the accepted fills."""
        if self.count < 2:
            return 0.0
        var = (self.sumsq_floz - self.sum_floz**2 / self.count) / (self.count - 1)
        return max(var, 0.0) ** 0.5

    @property
    def quarter_variance_pct(self) -> float:
        """Actual against labeled barrel equivalent this quarter (25.142(d))."""
        labeled = self.quarter_bottles * LABELED_FLOZ
        return 100.0 * (self.quarter_sum_floz - labeled) / labeled if labeled else 0.0


SPECS: dict[str, MachineSpec] = {
    "rinser": MachineSpec(rate_bpm=34, mtbf_min=240, mttr_min=3, kw=2.0),
    # The filler's fill-level rejects come from the fill model; `reject` here
    # covers its other defects (crowns, breakage).
    "filler": MachineSpec(rate_bpm=30, mtbf_min=180, mttr_min=4, reject=0.002, kw=7.0),
    "coder": MachineSpec(rate_bpm=36, mtbf_min=480, mttr_min=2, kw=1.0),
    "labeler": MachineSpec(rate_bpm=33, mtbf_min=90, mttr_min=5, reject=0.003, kw=3.0),
    "packer": MachineSpec(rate_bpm=34, mtbf_min=150, mttr_min=4, kw=6.0),
    "kegger": MachineSpec(
        rate_bpm=1 / 3, mtbf_min=600, mttr_min=10, reject=0.01, kw=1.5
    ),
}
#: Accumulation between machines (bottles).
BUFFER_CAPACITY: int = 120
#: Seconds a machine waits starved or blocked before it suspends.
SUSPEND_AFTER_S: float = 5.0
#: Seconds the line operator holds a button of the panel down. plc-packaging
#: reads the panel about once a second (its Modbus poll of rio-packaging).
PANEL_PRESS_S: float = 3.0
#: Seconds between two presses: the machines' states take a few seconds to show
#: what the last one did (PLC scan, Modbus write, PackML transition, poll).
PANEL_PAUSE_S: float = 5.0
#: States a line command gets a machine out of, and the command that does.
_LINE_STEPS: tuple[tuple[frozenset[State], Command], ...] = (
    (frozenset({State.ABORTED}), Command.CLEAR),
    (frozenset({State.STOPPED, State.COMPLETE}), Command.RESET),
    (frozenset({State.IDLE}), Command.START),
)
_AT_REST: frozenset[State] = frozenset(
    {State.STOPPED, State.STOPPING, State.ABORTED, State.ABORTING}
)


class PackagingSettings(BaseModel):
    """Packaging sim settings (helics.json ``sim``)."""

    autopilot: Literal["auto", "on", "off"] = "auto"
    seed: int = 0
    shift_start: str = "06:00"
    shift_end: str = "14:30"
    shift_weekdays: list[int] = [0, 1, 2, 3, 4]
    #: Seconds the line operator takes to notice a line that stopped during
    #: the shift and start it again at the panel. 0: the operator only starts
    #: the line at the start of the shift and stops it at the end.
    operator_react_s: float = Field(default=120.0, ge=0.0)
    fill: FillSpec = FillSpec()


class Machine:
    """One machine: PackML state, counters, jam state, SimPy process."""

    name: str
    spec: MachineSpec
    pml: PackMLMachine
    processed: int
    defective: int
    stop_reason: int
    jammed_until: float
    next_jam: float
    speed_sp: float
    wait_s: float
    #: Seconds the filler has run with its product valve shut.
    dry_s: float
    last_cmd: int
    last_mode: int

    def __init__(self, name: str, spec: MachineSpec, rng: random.Random) -> None:
        self.name = name
        self.spec = spec
        self.pml = PackMLMachine(name=name)
        self.processed = 0
        self.defective = 0
        self.stop_reason = STOP_OPERATOR
        self.jammed_until = -1.0
        self.next_jam = rng.expovariate(1.0 / (spec.mtbf_min * 60))
        self.speed_sp = spec.rate_bpm
        self.wait_s = 0.0
        self.dry_s = 0.0
        self.last_cmd = 0
        self.last_mode = int(Mode.PRODUCTION)

    @property
    def executing(self) -> bool:
        """In Execute."""
        return self.pml.state == State.EXECUTE

    def apply(self, cmd: Command) -> bool:
        """Apply a command if it is legal now; returns whether it was."""
        try:
            self.pml.command(cmd)
        except IllegalTransition:
            return False
        if cmd in (Command.STOP, Command.ABORT):
            self.stop_reason = STOP_OPERATOR
        return True


class PackagingFederate(Federate):
    """The packaging federate."""

    name: ClassVar[str] = "packaging"
    settings: PackagingSettings
    rng: random.Random
    env: simpy.Environment
    machines: dict[str, Machine]
    buffers: list[simpy.Container]
    cases: int
    kegs: int
    bottles_total: int
    shift_bottles: int
    product_open: bool
    heater: bool
    shift_days: set[str]
    fill: FillRecord
    #: The code of the panel button the line operator holds down (0: none).
    panel_cmd: int
    panel_release_t: float
    panel_next_t: float
    panel_presses: int
    #: The operator has a shift to end: set when one starts, cleared once the
    #: line is at rest after it.
    shift_open: bool
    #: The line has run this shift, so a stop now is one to notice, not the
    #: start of the shift.
    shift_up: bool
    #: When the line was first seen needing a command (None: it needs none).
    line_down_since: float | None

    def __init__(self, cfg: FederateConfig, clock: SimClock | None = None) -> None:
        super().__init__(cfg, clock)
        self.settings = PackagingSettings.model_validate(cfg.sim)
        self.rng = random.Random(self.settings.seed)
        self.fill = FillRecord()
        self.env = simpy.Environment()
        self.machines = {
            n: Machine(n, SPECS[n], self.rng) for n in (*BOTTLE_LINE, "kegger")
        }
        # buffers[i] feeds BOTTLE_LINE[i]; the depalletizer supplies glass freely.
        self.buffers = [
            simpy.Container(self.env, capacity=BUFFER_CAPACITY, init=0)
            for _ in range(len(BOTTLE_LINE) + 1)
        ]
        self.cases = 0
        self.kegs = 0
        self.bottles_total = 0
        self.shift_bottles = 0
        self.product_open = False
        self.heater = False
        self.shift_days = set()
        self.panel_cmd = 0
        self.panel_release_t = 0.0
        self.panel_next_t = 0.0
        self.panel_presses = 0
        self.shift_open = False
        self.shift_up = False
        self.line_down_since = None
        for i, name in enumerate(BOTTLE_LINE):
            self.env.process(self._run(self.machines[name], i))
        self.env.process(self._kegger())

    # -- authority and commands --------------------------------------------------------

    @property
    def autopilot(self) -> bool:
        """True while the built-in operator runs the shift."""
        mode = self.settings.autopilot
        return mode == "on" or (mode == "auto" and not self.plc_driving)

    def on_write(self, device: str, point: str, value: Any) -> None:
        """PackML commands act on change; speed and valves are levels."""
        m = self.machines.get("kegger" if device == "kegger" else device)
        if point == "product_valve":
            self.product_open = bool(value)
        elif point == "heater":
            self.heater = bool(value)
        elif m is None:
            return  # conveyor run commands: the conveyors follow the machines
        elif point == "cmd":
            if int(value) != m.last_cmd and int(value) in Command._value2member_map_:
                m.last_cmd = int(value)
                if int(value):
                    m.apply(Command(int(value)))
        elif point == "mode_cmd":
            if int(value) != m.last_mode and int(value) in Mode._value2member_map_:
                try:
                    m.pml.set_mode(Mode(int(value)))
                    m.last_mode = int(value)
                except IllegalTransition:
                    pass
        elif point == "speed_sp_bpm":
            m.speed_sp = min(max(float(value), 0.0), m.spec.rate_bpm * 1.1)

    def on_event(self, event: ScenarioEvent) -> None:
        """``jam``: jam a machine for ``duration`` seconds."""
        super().on_event(event)
        if event.type == "jam":
            name = str(event.params.get("machine", "labeler"))
            name = {"capper": "filler", "depalletizer": "rinser"}.get(name, name)
            m = self.machines.get(name)
            if m is not None:
                self._jam(m, float(event.params.get("duration", 300.0)))

    def _jam(self, m: Machine, seconds: float) -> None:
        m.jammed_until = self.env.now + seconds
        m.stop_reason = STOP_JAM
        if m.executing:
            m.apply(Command.HOLD)

    # -- SimPy processes ---------------------------------------------------------------

    def _run(self, m: Machine, i: int) -> Generator[simpy.Event, Any, None]:
        upstream = self.buffers[i]
        downstream = self.buffers[i + 1]
        while True:
            if not m.executing or m.speed_sp <= 0:
                yield self.env.timeout(1.0)
                continue
            if self.env.now >= m.next_jam:
                m.next_jam = self.env.now + self.rng.expovariate(
                    1.0 / (m.spec.mtbf_min * 60)
                )
                repair = self.rng.expovariate(1.0 / (m.spec.mttr_min * 60))
                self._jam(m, repair)
                continue
            starved = i > 0 and upstream.level < 1
            blocked = (
                i < len(BOTTLE_LINE) - 1 and downstream.level >= downstream.capacity
            )
            if m.name == "filler" and self.bowl_psia < LOW_BOWL_PSIA:
                # A shut product valve leaves what the bowl holds; a low
                # supply behind an open valve holds the filler at once.
                m.dry_s += 1.0
                if self.product_open or m.dry_s >= BOWL_RESERVE_S:
                    m.stop_reason = STOP_SUPPLY
                    m.apply(Command.HOLD)
                    continue
                yield self.env.timeout(1.0)
                continue
            m.dry_s = 0.0
            if starved or blocked:
                m.wait_s += 1.0
                if m.wait_s >= SUSPEND_AFTER_S:
                    m.stop_reason = STOP_STARVED if starved else STOP_BLOCKED
                    m.apply(Command.SUSPEND)
                yield self.env.timeout(1.0)
                continue
            m.wait_s = 0.0
            m.stop_reason = STOP_NONE
            yield self.env.timeout(60.0 / m.speed_sp)
            if not m.executing:
                continue
            if i > 0:
                yield upstream.get(1)
            m.processed += 1
            if self.rng.random() < m.spec.reject or (
                m.name == "filler" and not self._fill_accepted()
            ):
                m.defective += 1
                continue  # rejected bottles leave the line
            if m.name == "packer":
                self.bottles_total += 1
                self.shift_bottles += 1
                if self.bottles_total % 24 == 0:
                    self.cases += 1
            else:
                yield downstream.put(1)

    def _fill_accepted(self) -> bool:
        """Fill one bottle; the fill-level inspector rejects an unreasonable
        minus error (more than the MAV under the labeled contents)."""
        f = self.fill
        spec = self.settings.fill
        fill = self.rng.gauss(spec.target_floz, spec.sigma_floz)
        quarter = f"{self.now.year}Q{(self.now.month - 1) // 3 + 1}"
        if quarter != f.quarter:
            f.quarter, f.quarter_bottles, f.quarter_sum_floz = quarter, 0, 0.0
        if fill < LABELED_FLOZ - MAV_FLOZ:
            f.under_mav += 1
            return False
        f.count += 1
        f.sum_floz += fill
        f.sumsq_floz += fill * fill
        f.quarter_bottles += 1
        f.quarter_sum_floz += fill
        return True

    def _kegger(self) -> Generator[simpy.Event, Any, None]:
        m = self.machines["kegger"]
        while True:
            if not m.executing:
                yield self.env.timeout(1.0)
                continue
            m.stop_reason = STOP_NONE  # it runs: whatever stopped it last is over
            yield self.env.timeout(60.0 / m.spec.rate_bpm)
            if m.executing:
                m.processed += 1
                if self.rng.random() < m.spec.reject:
                    m.defective += 1
                else:
                    self.kegs += 1

    # -- the operator ------------------------------------------------------------------

    def _shift(self) -> bool:
        """True during the shift. The first time in a day's shift, the shift's
        bottle count starts again."""
        s = self.settings
        now = self.now
        start = tuple(int(x) for x in s.shift_start.split(":"))
        end = tuple(int(x) for x in s.shift_end.split(":"))
        in_shift = (
            now.weekday() in s.shift_weekdays and start <= (now.hour, now.minute) < end
        )
        day = now.date().isoformat()
        if in_shift and day not in self.shift_days:
            self.shift_days.add(day)
            self.shift_bottles = 0
            self.shift_open = True
            self.shift_up = False
            self.line_down_since = None
        return in_shift

    @property
    def panel_operator(self) -> bool:
        """True while the line operator works the shift at the panel: the PLC
        drives the line, so the built-in operator does not command the machines
        (:attr:`autopilot`), and somebody has to start the line."""
        return self.settings.autopilot == "auto" and self.plc_driving

    def _line_wants(self) -> Command | None:
        """The line command that moves the line toward running, if it needs one.
        A Held or Suspended machine needs none: a jam and a low bowl clear
        themselves (:meth:`_recover`), and a commanded Hold is somebody's choice."""
        states = {m.pml.state for m in self.machines.values()}
        return next((cmd for at, cmd in _LINE_STEPS if states & at), None)

    def _press(self, cmd: Command) -> None:
        self.panel_cmd = int(cmd)
        self.panel_release_t = self.t + PANEL_PRESS_S
        self.panel_next_t = self.panel_release_t + PANEL_PAUSE_S
        self.panel_presses += 1
        LOG.info("packaging: the line operator presses %s at the panel", cmd.name.title())

    def _panel(self, powered: bool) -> None:
        """The line operator at the panel, while the PLC drives the line: start
        the line when the shift starts, stop it when the shift ends, and start
        it again when it has stood stopped for ``operator_react_s`` during the
        shift (after a power cut, or a Stop nobody at the line asked for)."""
        if self.panel_cmd and self.t >= self.panel_release_t:
            self.panel_cmd = 0
        in_shift = self._shift()
        if self.panel_cmd or self.t < self.panel_next_t:
            return
        if not in_shift:
            if not self.shift_open:
                return
            if all(m.pml.state in _AT_REST for m in self.machines.values()):
                self.shift_open = False
            else:
                self._press(Command.STOP)
            return
        want = self._line_wants() if powered else None
        if want is None:
            self.line_down_since = None
            self.shift_up = self.shift_up or (
                powered and any(m.executing for m in self.machines.values())
            )
            return
        if self.line_down_since is None:
            self.line_down_since = self.t
        react = self.settings.operator_react_s
        if not self.shift_up or (0 < react <= self.t - self.line_down_since):
            self._press(want)

    def _operator(self) -> None:
        in_shift = self._shift()
        powered = self.input("power/bus.pkg.energized", 1.0) >= 0.5
        self.product_open = in_shift and powered
        self.heater = in_shift and powered
        for m in self.machines.values():
            st = m.pml.state
            if not in_shift or not powered:
                if st not in (
                    State.STOPPED,
                    State.STOPPING,
                    State.ABORTED,
                    State.ABORTING,
                ):
                    m.apply(Command.STOP)
                continue
            if st == State.ABORTED:
                m.apply(Command.CLEAR)
            elif st in (State.STOPPED, State.COMPLETE):
                m.apply(Command.RESET)
            elif st == State.IDLE:
                m.apply(Command.START)

    def _recover(self) -> None:
        """What each machine and the people at the line do themselves, whoever
        commands the line (the autopilot or plc-packaging, which only relays
        the operator's line command): a starved or blocked machine unsuspends
        once it has a bottle to take or room to put one (its own sensors,
        PackML's Suspended), the line operator clears a jam and unholds the
        machine once the jam's time is up, and the filler unholds from a low
        bowl once the supply is back. A Hold or Suspend that was commanded (no
        such stop reason) stays."""
        last = len(BOTTLE_LINE) - 1
        for m in self.machines.values():
            st = m.pml.state
            if (
                st == State.SUSPENDED
                and m.stop_reason in (STOP_STARVED, STOP_BLOCKED)
                and m.name in BOTTLE_LINE
            ):
                i = BOTTLE_LINE.index(m.name)
                starved = i > 0 and self.buffers[i].level < 1
                blocked = i < last and self.buffers[i + 1].level >= self.buffers[i + 1].capacity
                if not (starved or blocked):
                    m.apply(Command.UNSUSPEND)
            elif st != State.HELD or self.env.now < m.jammed_until:
                continue
            elif m.stop_reason == STOP_JAM or (
                m.stop_reason == STOP_SUPPLY and self.supply_psia >= LOW_BOWL_PSIA
            ):
                m.dry_s = 0.0
                m.apply(Command.UNHOLD)

    # -- dynamics ----------------------------------------------------------------------

    @property
    def supply_psia(self) -> float:
        """Pressure at the filler's product valve: the beer line from the bright
        tank on line, which is at 0 when no tank is (the cellar closed the
        outlet of an empty tank, or the tank ran dry)."""
        return max(self.input("cellar/beer.supply_psia", 25.0) - 1.0, 0.0)

    @property
    def bowl_psia(self) -> float:
        """Filler bowl pressure: the supply through the open product valve."""
        return self.supply_psia if self.product_open else 0.0

    def advance(self, wall_dt_s: float) -> None:
        """Run the line ``wall_dt_s`` seconds."""
        powered = self.input("power/bus.pkg.energized", 1.0) >= 0.5
        if not powered:
            for m in self.machines.values():
                if m.pml.state not in (State.ABORTED, State.ABORTING):
                    m.apply(Command.ABORT)
        if self.autopilot:
            self._operator()
            self.panel_cmd = 0
        elif self.panel_operator:
            self._panel(powered)
        if powered:
            self._recover()
        for m in self.machines.values():
            m.pml.step(wall_dt_s)
        self.env.run(until=self.env.now + wall_dt_s)

    # -- publications ------------------------------------------------------------------

    def point_value(self, pub: Publication) -> Value:
        """A rio-packaging point."""
        device, point = pub.device or "", pub.point or ""
        if device.startswith("conv"):
            i = int(device[-1])
            buf = self.buffers[min(i, len(self.buffers) - 1)]
            match point:
                case "accum_eye":
                    return buf.level > 0.8 * buf.capacity
                case "jam_eye":
                    return False
                case "run":
                    return any(m.executing for m in self.machines.values())
        if device == "linepanel":
            return float(self.panel_cmd)
        m = self.machines[device]
        match point:
            case "state":
                return float(int(m.pml.state))
            case "mode":
                return float(int(m.pml.mode))
            case "speed_bpm":
                return m.speed_sp if m.executing and m.stop_reason == STOP_NONE else 0.0
            case "processed":
                return float(m.processed % 65536)
            case "defective":
                return float(m.defective % 65536)
            case "stop_reason":
                return float(m.stop_reason)
            case "jammed":
                return self.env.now < m.jammed_until
            case "bowl_psia":
                return self.bowl_psia
            case "low_bowl":
                return self.product_open and self.bowl_psia < LOW_BOWL_PSIA
            case "product_valve":
                return self.product_open
            case "wash_temp_f":
                return 160.0 if self.heater else 70.0
            case "heater":
                return self.heater
            case "cmd":
                return float(m.last_cmd)
            case "mode_cmd":
                return float(m.last_mode)
            case "speed_sp_bpm":
                return m.speed_sp
        raise KeyError(f"no model value for {pub.key}")

    def output_value(self, name: str) -> Value:
        """Outputs to utilities, the cellar, power, and sim-ctl."""
        filler = self.machines["filler"]
        bpm = (
            filler.speed_sp
            if filler.executing and filler.stop_reason == STOP_NONE
            else 0.0
        )
        kegging = self.machines["kegger"].executing
        running = sum(1 for n in BOTTLE_LINE if self.machines[n].executing)
        match name:
            case "co2.draw_slpm":
                return 0.25 * bpm + (2.0 if kegging else 0.0)
            case "air.demand_scfm":
                return 8.0 + 3.0 * running
            case "beer.draw_gpm":
                return bpm * 12.0 / 128.0 + (15.5 / 3.0 if kegging else 0.0)
            case "line.state":
                return float(int(filler.pml.state))
            case "line.bottles":
                return float(self.shift_bottles)
            case "bottles.total":
                return float(self.bottles_total)
            case "kegs.total":
                return float(self.kegs)
            # Compliance figures for the KPI API (DESIGN.md §18A).
            case "ttb.bbl":
                ttb = self.ttb()
                return float(ttb["bbl_bottled"]) + float(ttb["bbl_kegged"])
            case "fill.mean_floz":
                return self.fill.mean_floz
            case "fill.under_mav":
                return float(self.fill.under_mav)
            case "load.line.kw":
                kw = sum(
                    self.machines[n].spec.kw
                    for n in BOTTLE_LINE
                    if self.machines[n].executing
                )
                return kw + (4.0 if running else 0.5)
            case "load.kegwasher.kw":
                return (12.0 if self.heater else 0.0) + (1.5 if kegging else 0.0)
        raise KeyError(f"no output {name}")

    def oee(self) -> dict[str, float]:
        """Shift availability, performance, quality (filler as the bottleneck)."""
        f = self.machines["filler"]
        execute = sum(
            v for k, v in f.pml.state_time_s.items() if k.endswith(".EXECUTE")
        )
        planned = execute + sum(
            v
            for k, v in f.pml.state_time_s.items()
            if k.endswith((".HELD", ".HOLDING", ".SUSPENDED", ".SUSPENDING"))
        )
        ideal = execute / 60.0 * f.spec.rate_bpm
        availability = execute / planned if planned else 0.0
        performance = f.processed / ideal if ideal else 0.0
        quality = 1.0 - f.defective / f.processed if f.processed else 0.0
        return {
            "availability": availability,
            "performance": min(performance, 1.0),
            "quality": quality,
            "oee": availability * min(performance, 1.0) * quality,
        }

    def ttb(self) -> dict[str, float | str]:
        """Packaged beer in TTB barrels (27 CFR 25.158 cases, 25.156 kegs) and
        the net-contents variance of 27 CFR 25.142(d)."""
        f = self.fill
        return {
            "bbl_bottled": round(self.cases * BBL_PER_CASE_24X12, 5),
            "bbl_kegged": round(self.kegs * BBL_PER_KEG, 5),
            "quarter": f.quarter,
            "net_contents_variance_pct": round(f.quarter_variance_pct, 3),
            "net_contents_tolerance_pct": TTB_NET_CONTENTS_TOLERANCE_PCT,
        }

    def truth(self) -> dict[str, Any]:
        """Published values plus counts, OEE, every machine's state, the fill
        statistics, and the TTB barrel figures."""
        doc = super().truth()
        f = self.fill
        doc["packaging"] = {
            "bottles": self.bottles_total,
            "cases": self.cases,
            "kegs": self.kegs,
            "bbl": self.bottles_total / BOTTLES_PER_BBL + self.kegs * BBL_PER_KEG,
            "oee": self.oee(),
            "states": {n: m.pml.state.name for n, m in self.machines.items()},
            # who commands the line: the built-in operator, the line operator
            # at the panel (through the PLC), or nobody in the simulation
            "operator": "autopilot" if self.autopilot else "panel" if self.panel_operator else "none",
            "panel_presses": self.panel_presses,
        }
        doc["fill"] = {
            "labeled_floz": LABELED_FLOZ,
            "mav_floz": MAV_FLOZ,
            "count": f.count,
            "mean_floz": round(f.mean_floz, 3),
            "std_floz": round(f.std_floz, 3),
            "under_mav_rejects": f.under_mav,
        }
        doc["ttb"] = self.ttb()
        return doc
