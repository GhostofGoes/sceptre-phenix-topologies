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
"""

from __future__ import annotations

import random
from collections.abc import Generator
from typing import Any, ClassVar, Literal

import simpy
from pydantic import BaseModel

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

#: Stop reasons (PackTags StopReason.ID).
STOP_NONE: int = 0
STOP_JAM: int = 10
STOP_SUPPLY: int = 20
STOP_STARVED: int = 30
STOP_BLOCKED: int = 40
STOP_OPERATOR: int = 50
#: Bottles per standard US beer barrel (3,968 oz / 12 oz).
BOTTLES_PER_BBL: float = 3968.0 / 12.0
#: Filler bowl pressure below which the filler holds, psia.
LOW_BOWL_PSIA: float = 18.0
BOTTLE_LINE: tuple[str, ...] = ("rinser", "filler", "coder", "labeler", "packer")


class MachineSpec(BaseModel):
    """Nominal rate and reliability of one machine."""

    rate_bpm: float
    mtbf_min: float
    mttr_min: float
    reject: float = 0.0
    kw: float = 2.0


SPECS: dict[str, MachineSpec] = {
    "rinser": MachineSpec(rate_bpm=34, mtbf_min=240, mttr_min=3, kw=2.0),
    "filler": MachineSpec(rate_bpm=30, mtbf_min=180, mttr_min=4, reject=0.005, kw=7.0),
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


class PackagingSettings(BaseModel):
    """Packaging sim settings (helics.json ``sim``)."""

    autopilot: Literal["auto", "on", "off"] = "auto"
    seed: int = 0
    shift_start: str = "06:00"
    shift_end: str = "14:30"
    shift_weekdays: list[int] = [0, 1, 2, 3, 4]


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

    def __init__(self, cfg: FederateConfig, clock: SimClock | None = None) -> None:
        super().__init__(cfg, clock)
        self.settings = PackagingSettings.model_validate(cfg.sim)
        self.rng = random.Random(self.settings.seed)
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
                m.stop_reason = STOP_SUPPLY
                m.apply(Command.HOLD)
                continue
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
            if self.rng.random() < m.spec.reject:
                m.defective += 1
                continue  # rejected bottles leave the line
            if m.name == "packer":
                self.bottles_total += 1
                self.shift_bottles += 1
                if self.bottles_total % 24 == 0:
                    self.cases += 1
            else:
                yield downstream.put(1)

    def _kegger(self) -> Generator[simpy.Event, Any, None]:
        m = self.machines["kegger"]
        while True:
            if not m.executing:
                yield self.env.timeout(1.0)
                continue
            yield self.env.timeout(60.0 / m.spec.rate_bpm)
            if m.executing:
                m.processed += 1
                if self.rng.random() < m.spec.reject:
                    m.defective += 1
                else:
                    self.kegs += 1

    # -- the operator ------------------------------------------------------------------

    def _operator(self) -> None:
        s = self.settings
        now = self.now
        start = tuple(int(x) for x in s.shift_start.split(":"))
        end = tuple(int(x) for x in s.shift_end.split(":"))
        in_shift = (
            now.weekday() in s.shift_weekdays and start <= (now.hour, now.minute) < end
        )
        powered = self.input("power/bus.pkg.energized", 1.0) >= 0.5
        self.product_open = in_shift and powered
        self.heater = in_shift and powered
        day = now.date().isoformat()
        if in_shift and day not in self.shift_days:
            self.shift_days.add(day)
            self.shift_bottles = 0
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
            elif (
                st == State.HELD
                and self.env.now >= m.jammed_until
                and not (m.name == "filler" and self.bowl_psia < LOW_BOWL_PSIA)
            ):
                m.apply(Command.UNHOLD)
            elif st == State.SUSPENDED and m.stop_reason in (
                STOP_STARVED,
                STOP_BLOCKED,
            ):
                m.apply(Command.UNSUSPEND)

    # -- dynamics ----------------------------------------------------------------------

    @property
    def bowl_psia(self) -> float:
        """Filler bowl pressure: BBT-202 head pressure through the product valve."""
        if not self.product_open:
            return 0.0
        return max(self.input("cellar/bbt202.pressure_psia", 25.0) - 1.0, 0.0)

    def advance(self, wall_dt_s: float) -> None:
        """Run the line ``wall_dt_s`` seconds."""
        if self.input("power/bus.pkg.energized", 1.0) < 0.5:
            for m in self.machines.values():
                if m.pml.state not in (State.ABORTED, State.ABORTING):
                    m.apply(Command.ABORT)
        if self.autopilot:
            self._operator()
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

    def truth(self) -> dict[str, Any]:
        """Published values plus counts, OEE, and every machine's state."""
        doc = super().truth()
        doc["packaging"] = {
            "bottles": self.bottles_total,
            "cases": self.cases,
            "kegs": self.kegs,
            "bbl": self.bottles_total / BOTTLES_PER_BBL + self.kegs * 0.5,
            "oee": self.oee(),
            "states": {n: m.pml.state.name for n, m in self.machines.items()},
        }
        return doc
