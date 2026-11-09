"""Brewhouse federate: mill, liquor tanks, mash/lauter tun, kettle, knockout.

Physics from :mod:`cherrymountain_sim.brewing` (DESIGN.md §9.3) in wall time,
at 1 s steps. Every field point of ``rio-brewhouse`` is either a measurement
the model computes or a command (valves, pumps, speed references) the model
obeys.

**Who drives the commands.** In the full range plc-brewhouse runs the ISA-88
phases and writes the commands through rio-brewhouse. Until it does, a
built-in operator (:class:`Autopilot`) runs the double-brew day of the
recipe, so the plant moves on its own (sim settings ``autopilot``: ``auto``
hands control to the PLC on its first write, ``on`` keeps it, ``off`` never
runs it). The autopilot also starts a double batch at ``auto_start`` on the
``brew_weekdays`` and on the ``start-batch`` scenario event.

Simplifications: grist and liquor mix instantly; the whirlpool cools the wort
to its stand temperature with a 5-minute time constant (the operator's
recirculation through the HX); the CLT refills from RO water at the
groundwater temperature.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from cherrymountain_sim.brewing import (
    MashParams,
    MashState,
    c_to_f,
    counterflow,
    f_to_c,
    hosom_relative_utilization,
    isomerization_rate_ratio,
    mash_step,
    strike_temperature,
    tinseth_bigness,
    water_boiling_point_c,
    wort_boiling_point_c,
)
from cherrymountain_sim.federate import (
    Federate,
    FederateConfig,
    Publication,
    ScenarioEvent,
    SimClock,
    Value,
)
from cherrymountain_sim.fermentation import sg_from_plato
from cherrymountain_sim.recipe import Recipe, default

LPM_PER_GPM: float = 3.785411784
WATER_CP: float = 4180.0  # J/(kg K)
GRAIN_CP: float = 0.4 * WATER_CP
WORT_CP: float = 4000.0
GLYCOL_CP: float = 3800.0
H_FG: float = 2.27e6  # J/kg, latent heat at about 95 °C
KPA_PER_PSI: float = 6.894757
#: Extract the grist releases into the mash liquor, kg per kg. Malt yields
#: about 0.80 in the laboratory; the model's well-mixed lauter washes the grain
#: bed out less completely than a real fly sparge, so this is calibrated so
#: the kettle reaches the recipe's 16.1 °P after the boil.
EXTRACT_POTENTIAL: float = 1.0
#: Liquor the spent grain holds back, L/kg.
ABSORPTION_L_KG: float = 0.8
#: Steam the 200 kW electric boiler can deliver, W.
BOILER_W: float = 196e3
#: ISA-88 phase numbers (plant.yaml BH.BATCH.XC01.PHASE).
PHASES: dict[str, int] = {
    "IDLE": 0,
    "MILL": 1,
    "HLT_HEAT": 2,
    "MASH_IN": 3,
    "MASH_STEP": 4,
    "VORLAUF": 5,
    "LAUTER": 6,
    "BOIL": 7,
    "WHIRLPOOL": 8,
    "KNOCKOUT": 9,
    "CIP_READY": 10,
}


class BrewhouseSettings(BaseModel):
    """Brewhouse sim settings (helics.json ``sim``)."""

    autopilot: Literal["auto", "on", "off"] = "auto"
    auto_start: str | None = "05:00"
    brew_weekdays: list[int] = Field(default_factory=lambda: [0, 2])
    brews_per_day: int = 2
    recipe: str = "fire-of-the-phenix"


class Tank(BaseModel):
    """A well-mixed liquid volume."""

    liters: float = 0.0
    temp_c: float = 20.0
    capacity_l: float
    plato: float = 0.0  # dissolved extract (wort tanks)

    @property
    def level_pct(self) -> float:
        """Level as a percentage of capacity."""
        return min(100.0 * self.liters / self.capacity_l, 100.0)

    def add(self, liters: float, temp_c: float, plato: float = 0.0) -> float:
        """Mix in ``liters`` at ``temp_c`` and ``plato``; returns the overflow."""
        if liters <= 0:
            return 0.0
        total = self.liters + liters
        self.temp_c = (self.temp_c * self.liters + temp_c * liters) / total
        self.plato = (self.plato * self.liters + plato * liters) / total
        self.liters = min(total, self.capacity_l)
        return total - self.liters

    def take(self, liters: float) -> float:
        """Remove up to ``liters``; returns what was removed."""
        out = min(max(liters, 0.0), self.liters)
        self.liters -= out
        return out


class Process(BaseModel):
    """Brewhouse process state."""

    silo_kg: float = 18000.0
    silo_capacity_kg: float = 20000.0
    grist_kg: float = 0.0
    mill_hz: float = 0.0
    clt: Tank = Field(
        default_factory=lambda: Tank(capacity_l=3520.0, liters=2800.0, temp_c=14.0)
    )
    hlt: Tank = Field(
        default_factory=lambda: Tank(capacity_l=3520.0, liters=2900.0, temp_c=60.0)
    )
    mlt: Tank = Field(default_factory=lambda: Tank(capacity_l=3000.0, temp_c=25.0))
    kettle: Tank = Field(default_factory=lambda: Tank(capacity_l=2580.0, temp_c=25.0))
    grain_kg: float = 0.0
    extract_kg: float = 0.0  # extract the grain bed can yield
    dissolved_kg: float = 0.0  # extract dissolved in the mash liquor
    overflow_l: float = 0.0
    mash: MashState = Field(default_factory=MashState)
    compaction: float = 1.0
    rake_in: float = 30.0
    runoff_lpm: float = 0.0
    sparge_lpm: float = 0.0
    boil_min: float = 0.0
    boiling: bool = False
    wort_out_c: float = 20.0
    stage1_out_c: float = 20.0
    wort_lpm: float = 0.0
    o2_slpm: float = 0.0
    do_ppm: float = 0.0
    glycol_w: float = 0.0
    steam_w: float = 0.0
    hood_w: float = 0.0
    drain_s: float = 0.0
    knocking_out: bool = False
    brews_completed: int = 0
    #: α-acids in the kettle, mg/L each, with their effective boil minutes.
    hops: list[tuple[float, float]] = Field(default_factory=list)


#: Command defaults: every writable rio-brewhouse point.
DEFAULT_COMMANDS: dict[str, Value] = {
    "mill.run": False,
    "mill.auger_run": False,
    "mill.speed_sp_hz": 0.0,
    "clt.fill_valve": False,
    "hlt.fill_valve": False,
    "hlt.steam_valve_pct": 0.0,
    "mlt.strike_valve": False,
    "mlt.rake_run": False,
    "mlt.steam_valve_pct": 0.0,
    "mlt.runoff_valve_pct": 0.0,
    "mlt.sparge_valve_pct": 0.0,
    "mlt.rake_speed_pct": 0.0,
    "kettle.whirlpool_valve": False,
    "kettle.hop_doser": False,
    "kettle.steam_valve_pct": 0.0,
    "knockout.pump_run": False,
    "knockout.to_fv_valve": False,
    "knockout.pump_speed_pct": 0.0,
    "knockout.glycol_valve_pct": 0.0,
    "knockout.o2_sp_slpm": 0.0,
}


class Brew(BaseModel):
    """One brew of a double batch, as the autopilot tracks it."""

    phase: str = "MILL"
    timer_s: float = 0.0
    mash_step: int = 0
    on_temp: bool = False


class Autopilot:
    """Built-in operator and batch engine for a double-brew day."""

    recipe: Recipe
    queue: int
    mlt_brew: Brew | None
    kettle_brew: Brew | None
    whirlpool_dosed: bool
    hops_added: set[str]
    knockout_i: float

    def __init__(self, recipe: Recipe) -> None:
        self.recipe = recipe
        self.queue = 0
        self.mlt_brew = None
        self.kettle_brew = None
        self.whirlpool_dosed = False
        self.hops_added = set()
        self.knockout_i = 0.0

    @property
    def strike_c(self) -> float:
        """Strike liquor temperature for the first mash step (metric form),
        plus 1 K for the pipe and tun losses."""
        r = self.recipe
        mash_c = r.mash[0].temp_c if r.mash else f_to_c(152.0)
        return strike_temperature(mash_c, 18.3, r.mash_ratio_l_kg, metric=True) + 1.0

    @property
    def phase(self) -> int:
        """The most advanced active phase (for ``batch.phase``)."""
        for brew in (self.kettle_brew, self.mlt_brew):
            if brew is not None:
                return PHASES[brew.phase]
        return PHASES["IDLE"]

    def start(self, brews: int) -> None:
        """Queue ``brews`` brews."""
        self.queue += brews

    def step(self, fed: BrewhouseFederate, dt: float) -> None:
        """Set the commands for this step."""
        c = fed.auto
        p = fed.proc
        r = self.recipe
        strike_l = r.grain_kg * r.mash_ratio_l_kg
        # Liquor tanks: keep the CLT and HLT topped up; heat the HLT. The HLT
        # also takes the warm stage-1 water at knockout, so leave it room.
        c["clt.fill_valve"] = p.clt.level_pct < 70.0 or (
            p.clt.level_pct < 95.0 and bool(c["clt.fill_valve"])
        )
        c["hlt.fill_valve"] = p.hlt.level_pct < 40.0 or (
            p.hlt.level_pct < 60.0 and bool(c["hlt.fill_valve"])
        )
        hlt_sp = f_to_c(168.0)
        if self.mlt_brew is None and self.queue > 0:
            self.mlt_brew = Brew()
            self.queue -= 1
        brew = self.mlt_brew
        if brew is not None and brew.phase in ("MILL", "HLT_HEAT", "MASH_IN"):
            hlt_sp = self.strike_c
        c["hlt.steam_valve_pct"] = _p_control(hlt_sp - p.hlt.temp_c, 25.0)
        if brew is not None:
            self._mlt(fed, brew, dt, strike_l)
        kb = self.kettle_brew
        if kb is not None:
            self._kettle(fed, kb, dt)
        else:
            c["kettle.steam_valve_pct"] = 0.0
            c["knockout.pump_run"] = False
            c["knockout.to_fv_valve"] = False
            c["knockout.o2_sp_slpm"] = 0.0

    def _mlt(
        self, fed: BrewhouseFederate, brew: Brew, dt: float, strike_l: float
    ) -> None:
        c = fed.auto
        p = fed.proc
        r = self.recipe
        brew.timer_s += dt
        match brew.phase:
            case "MILL":
                c["mill.run"] = c["mill.auger_run"] = True
                c["mill.speed_sp_hz"] = 50.0
                if p.grist_kg >= r.grain_kg:
                    c["mill.run"] = c["mill.auger_run"] = False
                    c["mill.speed_sp_hz"] = 0.0
                    brew.phase, brew.timer_s = "HLT_HEAT", 0.0
            case "HLT_HEAT":
                if p.mlt.liters < 50.0 and abs(p.hlt.temp_c - self.strike_c) < 1.5:
                    brew.phase, brew.timer_s = "MASH_IN", 0.0
            case "MASH_IN":
                c["mlt.strike_valve"] = p.mlt.liters < strike_l
                if not c["mlt.strike_valve"]:
                    brew.phase, brew.timer_s = "MASH_STEP", 0.0
            case "MASH_STEP":
                step = r.mash[min(brew.mash_step, len(r.mash) - 1)]
                err = step.temp_c - p.mlt.temp_c
                c["mlt.steam_valve_pct"] = _p_control(err, 40.0)
                c["mlt.rake_run"] = True
                c["mlt.rake_speed_pct"] = 20.0
                if not brew.on_temp:  # the step time starts on temperature
                    brew.on_temp = abs(err) < 1.0
                    brew.timer_s = 0.0
                if brew.timer_s >= step.minutes * 60:
                    brew.mash_step += 1
                    brew.timer_s = 0.0
                    brew.on_temp = False
                    if brew.mash_step >= len(r.mash):
                        c["mlt.steam_valve_pct"] = 0.0
                        brew.phase = "VORLAUF"
            case "VORLAUF":
                c["mlt.rake_run"] = False
                if brew.timer_s >= 10 * 60:
                    brew.phase, brew.timer_s = "LAUTER", 0.0
                    self.kettle_brew = Brew(phase="LAUTER")
            case "LAUTER":
                c["mlt.runoff_valve_pct"] = 70.0 if p.kettle.liters < 2050.0 else 0.0
                free = p.mlt.liters - p.grain_kg * ABSORPTION_L_KG
                c["mlt.sparge_valve_pct"] = (
                    60.0 if free < 400.0 and p.kettle.liters < 1900.0 else 0.0
                )
                dp = fed.bed_dp_psi
                c["mlt.rake_run"] = dp > 1.0 or (bool(c["mlt.rake_run"]) and dp > 0.6)
                c["mlt.rake_speed_pct"] = 30.0 if c["mlt.rake_run"] else 0.0
                if p.kettle.liters >= 2050.0:
                    c["mlt.runoff_valve_pct"] = c["mlt.sparge_valve_pct"] = 0.0
                    fed.grain_out()
                    self.mlt_brew = None
                    if self.kettle_brew is not None:
                        self.kettle_brew.phase, self.kettle_brew.timer_s = "BOIL", 0.0

    def _kettle(self, fed: BrewhouseFederate, kb: Brew, dt: float) -> None:
        c = fed.auto
        p = fed.proc
        kb.timer_s += dt
        match kb.phase:
            case "LAUTER":  # keep the first runnings hot, below the boil
                c["kettle.steam_valve_pct"] = (
                    _p_control(f_to_c(195.0) - p.kettle.temp_c, 30.0)
                    if p.kettle.liters > 300
                    else 0.0
                )
            case "BOIL":
                if kb.timer_s <= dt:
                    p.boil_min = 0.0
                c["kettle.steam_valve_pct"] = 75.0 if p.boiling else 100.0
                for hop in self.recipe.hops:
                    key = f"{hop.name}@{hop.minutes}"
                    remaining = self.recipe.boil_minutes - p.boil_min
                    if (
                        hop.use == "boil"
                        and key not in self.hops_added
                        and (remaining <= hop.minutes and p.boiling)
                    ):
                        fed.add_hops(hop.alpha, hop.grams)
                        self.hops_added.add(key)
                if p.boil_min >= self.recipe.boil_minutes:
                    c["kettle.steam_valve_pct"] = 0.0
                    kb.phase, kb.timer_s = "WHIRLPOOL", 0.0
                    self.whirlpool_dosed = False
            case "WHIRLPOOL":
                c["kettle.whirlpool_valve"] = kb.timer_s < 10 * 60
                c["kettle.hop_doser"] = not self.whirlpool_dosed and kb.timer_s > 5
                if c["kettle.hop_doser"]:
                    for hop in self.recipe.hops:
                        if hop.use == "whirlpool":
                            fed.add_hops(hop.alpha, hop.grams)
                    self.whirlpool_dosed = True
                if kb.timer_s >= 20 * 60:
                    c["kettle.whirlpool_valve"] = False
                    kb.phase, kb.timer_s = "KNOCKOUT", 0.0
                    self.knockout_i = 0.0
            case "KNOCKOUT":
                c["knockout.pump_run"] = c["knockout.to_fv_valve"] = True
                c["knockout.pump_speed_pct"] = 80.0
                c["knockout.o2_sp_slpm"] = 1.0
                err = c_to_f(p.wort_out_c) - 64.0
                self.knockout_i = min(max(self.knockout_i + 0.1 * err * dt, 0.0), 100.0)
                c["knockout.glycol_valve_pct"] = min(
                    max(1.5 * err + self.knockout_i, 0.0), 100.0
                )
                if p.kettle.liters <= 5.0:
                    for key in ("knockout.pump_run", "knockout.to_fv_valve"):
                        c[key] = False
                    c["knockout.o2_sp_slpm"] = c["knockout.glycol_valve_pct"] = 0.0
                    p.hops.clear()
                    self.hops_added.clear()
                    p.drain_s = 5 * 60.0  # trub dump and rinse
                    self.kettle_brew = None


def _p_control(err: float, gain: float) -> float:
    return min(max(err * gain, 0.0), 100.0)


class BrewhouseFederate(Federate):
    """The brewhouse federate."""

    name: ClassVar[str] = "brewhouse"
    settings: BrewhouseSettings
    recipe: Recipe
    proc: Process
    auto: dict[str, Value]
    pilot: Autopilot
    mash_params: MashParams
    started_days: set[str]
    #: The ``batch_id`` of the latest ``start-batch`` event (ground truth only).
    batch_id: str

    def __init__(self, cfg: FederateConfig, clock: SimClock | None = None) -> None:
        super().__init__(cfg, clock)
        self.settings = BrewhouseSettings.model_validate(cfg.sim)
        self.recipe = default()
        self.proc = Process()
        self.auto = dict(DEFAULT_COMMANDS)
        self.pilot = Autopilot(self.recipe)
        self.mash_params = MashParams()
        self.started_days = set()
        self.batch_id = ""

    # -- control authority -------------------------------------------------------------

    @property
    def autopilot(self) -> bool:
        """True while the built-in operator drives the commands."""
        mode = self.settings.autopilot
        return mode == "on" or (mode == "auto" and not self.plc_driving)

    def cmd(self, tag: str) -> Value:
        """The command in force: the autopilot's, or the last PLC write."""
        if self.autopilot:
            return self.auto[tag]
        return self.commands.get(tag, DEFAULT_COMMANDS[tag])

    def num(self, tag: str) -> float:
        """A numeric command, 0-100 % valves clamped."""
        return float(self.cmd(tag))

    def on(self, tag: str) -> bool:
        """A binary command."""
        return bool(self.cmd(tag))

    def on_event(self, event: ScenarioEvent) -> None:
        """``start-batch`` queues brews for the autopilot."""
        super().on_event(event)
        if event.type == "start-batch":
            # simctl checks the recipe (the plant has one) and the brew count.
            self.batch_id = str(event.params.get("batch_id", ""))
            self.pilot.start(int(event.params.get("brews", 2)))

    # -- helpers used by the autopilot -------------------------------------------------

    def add_hops(self, alpha: float, grams: float) -> None:
        """A hop addition to the kettle (α-acids in mg/L)."""
        liters = max(self.proc.kettle.liters, 1.0)
        self.proc.hops.append((alpha * grams * 1000.0 / liters, 0.0))

    def grain_out(self) -> None:
        """Spent grain out; the MLT is free."""
        p = self.proc
        p.mlt = Tank(capacity_l=p.mlt.capacity_l, temp_c=p.mlt.temp_c)
        p.grain_kg = p.extract_kg = p.dissolved_kg = 0.0
        p.mash = MashState()
        p.compaction = 1.0
        p.drain_s = 3 * 60.0

    @property
    def ambient_kpa(self) -> float:
        """Barometric pressure on site."""
        return self.input("weather/pressure.kpa", 83.4)

    @property
    def bed_dp_psi(self) -> float:
        """False-bottom differential pressure."""
        if self.proc.grain_kg <= 0:
            return 0.0
        return 0.025 * self.proc.runoff_lpm * self.proc.compaction

    @property
    def ibu(self) -> float:
        """Bitterness the kettle wort carries now (Tinseth, derated)."""
        p = self.proc
        g = sg_from_plato(p.kettle.plato) if p.kettle.plato > 0 else 1.0
        big = tinseth_bigness(g)
        return sum(mg * big * (1 - math.exp(-0.04 * t)) / 4.15 for mg, t in p.hops)

    # -- dynamics ----------------------------------------------------------------------

    def advance(self, wall_dt_s: float) -> None:
        """Advance the brewhouse ``wall_dt_s`` seconds."""
        self._schedule()
        if self.autopilot:
            self.pilot.step(self, wall_dt_s)
        p = self.proc
        dt = wall_dt_s
        powered = self.input("power/bus.mcc1.energized", 1.0) >= 0.5
        steam = min(max(self.input("utilities/steam.psig", 15.0) / 12.0, 0.0), 1.0)
        amb_c = f_to_c(self.input("weather/ambient.temp_f", 75.0))
        water_c = f_to_c(self.input("weather/groundwater.temp_f", 58.0))
        boil_c = wort_boiling_point_c(self.ambient_kpa, p.kettle.plato)
        self._mill(dt, powered)
        # CLT: RO make-up at the groundwater temperature.
        if self.on("clt.fill_valve"):
            p.clt.add(30.0 * LPM_PER_GPM / 60.0 * dt, water_c)
        if self.on("hlt.fill_valve"):
            p.overflow_l += p.hlt.add(
                p.clt.take(40.0 * LPM_PER_GPM / 60.0 * dt), p.clt.temp_c
            )
        # HLT, MLT, and kettle jackets, limited together by the 200 kW boiler.
        q_hlt = self.num("hlt.steam_valve_pct") / 100 * 120e3
        q_mlt = (
            self.num("mlt.steam_valve_pct") / 100 * 80e3 if p.mlt.liters > 100 else 0.0
        )
        q_ktl = (
            self.num("kettle.steam_valve_pct") / 100 * 180e3
            if p.kettle.liters > 100
            else 0.0
        )
        scale = steam * min(1.0, BOILER_W / max(q_hlt + q_mlt + q_ktl, 1.0))
        q_hlt, q_mlt, q_ktl = q_hlt * scale, q_mlt * scale, q_ktl * scale
        self._heat(
            p.hlt, q_hlt, amb_c, 30.0, dt, water_boiling_point_c(self.ambient_kpa)
        )
        self._mash(dt, q_mlt, amb_c)
        self._kettle(dt, q_ktl, amb_c, boil_c)
        self._knockout(dt, powered)
        p.steam_w = q_hlt + q_mlt + q_ktl
        p.drain_s = max(p.drain_s - dt, 0.0)

    def _schedule(self) -> None:
        """Start the double batch at ``auto_start`` on brew days."""
        s = self.settings
        now = self.now
        if s.auto_start is None or now.weekday() not in s.brew_weekdays:
            return
        day = now.date().isoformat()
        hh, mm = (int(x) for x in s.auto_start.split(":"))
        if day not in self.started_days and (now.hour, now.minute) >= (hh, mm):
            self.started_days.add(day)
            if now.hour < hh + 2:  # not when the run starts late in the day
                self.pilot.start(s.brews_per_day)

    def _mill(self, dt: float, powered: bool) -> None:
        p = self.proc
        target = (
            self.num("mill.speed_sp_hz") if self.on("mill.run") and powered else 0.0
        )
        p.mill_hz += max(min(target - p.mill_hz, 5.0 * dt), -5.0 * dt)
        if p.mill_hz > 1.0 and self.on("mill.auger_run"):
            kg = 0.6 * p.mill_hz / 60.0 * dt
            kg = min(kg, p.silo_kg)
            p.silo_kg -= kg
            p.grist_kg += kg

    @staticmethod
    def _heat(
        tank: Tank, q_w: float, amb_c: float, ua: float, dt: float, t_max: float
    ) -> None:
        if tank.liters <= 1.0:
            return
        cap = tank.liters * WATER_CP
        tank.temp_c += (q_w - ua * (tank.temp_c - amb_c)) / cap * dt
        tank.temp_c = min(tank.temp_c, t_max)

    def _mash(self, dt: float, q_w: float, amb_c: float) -> None:
        p = self.proc
        r = self.recipe
        if self.on("mlt.strike_valve"):
            liters = p.hlt.take(150.0 / 60.0 * dt)
            grist = min(liters / r.mash_ratio_l_kg, p.grist_kg)
            p.grist_kg -= grist
            if grist > 0:  # grist at the room temperature enters the mash
                cap_old = p.mlt.liters * WATER_CP + p.grain_kg * GRAIN_CP
                cap_new = cap_old + liters * WATER_CP + grist * GRAIN_CP
                p.mlt.temp_c = (
                    cap_old * p.mlt.temp_c
                    + liters * WATER_CP * p.hlt.temp_c
                    + grist * GRAIN_CP * 18.3
                ) / cap_new
                p.mlt.liters += liters
            else:
                p.mlt.add(liters, p.hlt.temp_c)
            p.grain_kg += grist
            p.extract_kg += grist * EXTRACT_POTENTIAL
        p.sparge_lpm = self.num("mlt.sparge_valve_pct") / 100 * 40.0
        sparge = p.hlt.take(p.sparge_lpm / 60.0 * dt)
        p.sparge_lpm = sparge / dt * 60.0
        p.mlt.add(sparge, p.hlt.temp_c)
        if p.mlt.liters > 1.0:
            cap = p.mlt.liters * WATER_CP + p.grain_kg * GRAIN_CP
            p.mlt.temp_c += (q_w - 25.0 * (p.mlt.temp_c - amb_c)) / cap * dt
            if p.grain_kg > 0:
                before = p.mash.conversion
                p.mash = mash_step(p.mash, p.mlt.temp_c, dt / 60.0, self.mash_params)
                p.dissolved_kg += max(p.mash.conversion - before, 0.0) * p.extract_kg
        # Plato of the liquor: dissolved extract over liquor water plus extract.
        water_kg = max(p.mlt.liters, 1.0)
        p.mlt.plato = 100.0 * p.dissolved_kg / (water_kg + p.dissolved_kg)
        # Lauter runoff: valve-limited, no more than the free liquid. The wort
        # leaves at the liquor's concentration.
        free = max(p.mlt.liters - p.grain_kg * ABSORPTION_L_KG, 0.0)
        p.runoff_lpm = min(
            self.num("mlt.runoff_valve_pct") / 100 * 40.0, free / dt * 60
        )
        out = p.mlt.take(p.runoff_lpm / 60.0 * dt)
        p.dissolved_kg -= p.dissolved_kg * out / max(out + p.mlt.liters, 1.0)
        p.kettle.add(out, p.mlt.temp_c, p.mlt.plato)
        rake = self.on("mlt.rake_run")
        if p.runoff_lpm > 0 and not rake:
            p.compaction += 0.0006 * dt
        elif rake:
            p.compaction = max(1.0, p.compaction - 0.004 * dt)
        target_in = 6.0 if rake else 30.0
        p.rake_in += max(min(target_in - p.rake_in, 0.15 * dt), -0.15 * dt)

    def _kettle(self, dt: float, q_w: float, amb_c: float, boil_c: float) -> None:
        p = self.proc
        k = p.kettle
        p.hood_w = 0.0
        if k.liters <= 1.0:
            p.boiling = False
            return
        cap = k.liters * WORT_CP
        loss = 60.0 * (k.temp_c - amb_c)
        if self.on("kettle.whirlpool_valve") or (
            self.autopilot
            and self.pilot.kettle_brew is not None
            and self.pilot.kettle_brew.phase == "WHIRLPOOL"
        ):
            stand = f_to_c(180.0)
            if k.temp_c > stand:  # recirculation cooling to the stand temperature
                k.temp_c += (stand - k.temp_c) * dt / 300.0
        else:
            k.temp_c += (q_w - loss) / cap * dt
        p.boiling = k.temp_c >= boil_c - 0.05 and q_w > loss
        if k.temp_c >= boil_c:
            excess = (k.temp_c - boil_c) * cap  # energy above the boiling point
            k.temp_c = boil_c
            evap = excess / H_FG
            water = k.liters * (1 - k.plato / 100)
            extract = k.liters * k.plato / 100
            water = max(water - evap, 1.0)
            k.liters = water + extract
            k.plato = 100.0 * extract / k.liters
            p.hood_w = evap * H_FG / dt
        if p.boiling:
            p.boil_min += dt / 60.0
        rate = (
            isomerization_rate_ratio(boil_c)
            if p.boiling
            else (hosom_relative_utilization(k.temp_c + 273.15))
        )
        p.hops = [(mg, t + rate * dt / 60.0) for mg, t in p.hops]
        p.hood_w += max(loss, 0.0)
        if not p.boiling and k.temp_c < 60.0:
            p.boil_min = 0.0

    def _knockout(self, dt: float, powered: bool) -> None:
        p = self.proc
        running = self.on("knockout.pump_run") and powered and p.kettle.liters > 1.0
        p.wort_lpm = (
            self.num("knockout.pump_speed_pct") / 100 * 120.0 if running else 0.0
        )
        if not self.on("knockout.to_fv_valve"):
            p.wort_lpm = 0.0
        out = p.kettle.take(p.wort_lpm / 60.0 * dt)
        p.wort_lpm = out / dt * 60.0
        if p.wort_lpm > 0:
            p.knocking_out = True
        elif p.knocking_out and p.kettle.liters <= 5.0:
            p.knocking_out = False
            p.brews_completed += 1
        o2_sp = self.num("knockout.o2_sp_slpm") if p.wort_lpm > 0 else 0.0
        p.o2_slpm += (o2_sp - p.o2_slpm) * min(dt / 2.0, 1.0)
        if p.wort_lpm <= 0:
            p.glycol_w = 0.0
            p.do_ppm = max(p.do_ppm - 0.05 * dt, 0.0)
            return
        m_wort = p.wort_lpm / 60.0 * 1.06
        c_wort = m_wort * WORT_CP
        # Stage 1: CLT water at 1.1x the wort flow, to the HLT (heat recovery).
        water = p.clt.take(1.1 * p.wort_lpm / 60.0 * dt)
        c_water = water / dt * WATER_CP
        s1 = counterflow(p.kettle.temp_c, p.clt.temp_c, c_wort, c_water, 36000.0)
        p.overflow_l += p.hlt.add(water, s1.cold_out)
        # Stage 2: glycol trim.
        gly_c = f_to_c(self.input("utilities/glycol.supply_f", 28.0))
        c_gly = (
            self.num("knockout.glycol_valve_pct")
            / 100
            * 100.0
            / 60.0
            * 1.03
            * GLYCOL_CP
        )
        s2 = counterflow(s1.hot_out, gly_c, c_wort, c_gly, 15000.0)
        # The plates and piping hold heat: outlet temperatures lag (tau 15 s).
        lag = min(dt / 15.0, 1.0)
        p.stage1_out_c += (s1.hot_out - p.stage1_out_c) * lag
        p.wort_out_c += (s2.hot_out - p.wort_out_c) * lag
        p.glycol_w = s2.duty_w
        dosed = p.o2_slpm * 1430.0 / max(p.wort_lpm, 1e-6)  # mg O2 per L of wort
        p.do_ppm = min(0.6 * dosed, 25.0)

    # -- publications ------------------------------------------------------------------

    def point_value(self, pub: Publication) -> Value:
        """A rio-brewhouse point."""
        tag = f"{pub.device}.{pub.point}"
        if tag in DEFAULT_COMMANDS:
            return self.cmd(tag)
        p = self.proc
        values: dict[str, Value] = {
            "mill.silo_level_pct": 100.0 * p.silo_kg / p.silo_capacity_kg,
            "mill.speed_hz": p.mill_hz,
            "mill.running": p.mill_hz > 1.0,
            "clt.level_pct": p.clt.level_pct,
            "clt.temp_f": c_to_f(p.clt.temp_c),
            "clt.high_level": p.clt.level_pct > 95.0,
            "hlt.level_pct": p.hlt.level_pct,
            "hlt.temp_f": c_to_f(p.hlt.temp_c),
            "hlt.low_level": p.hlt.level_pct < 15.0,
            "mlt.temp_f": c_to_f(p.mlt.temp_c),
            "mlt.level_pct": min(
                100.0 * (p.mlt.liters + 0.7 * p.grain_kg) / p.mlt.capacity_l, 100.0
            ),
            "mlt.bed_dp_psi": self.bed_dp_psi,
            "mlt.runoff_gpm": p.runoff_lpm / LPM_PER_GPM,
            "mlt.sparge_gpm": p.sparge_lpm / LPM_PER_GPM,
            "mlt.rake_height_in": p.rake_in,
            "mlt.rake_running": self.on("mlt.rake_run"),
            "kettle.temp_f": c_to_f(p.kettle.temp_c),
            "kettle.level_pct": p.kettle.level_pct,
            "kettle.ambient_psia": self.ambient_kpa / KPA_PER_PSI,
            "kettle.high_level": p.kettle.level_pct > 95.0,
            "knockout.wort_gpm": p.wort_lpm / LPM_PER_GPM,
            "knockout.stage1_out_f": c_to_f(p.stage1_out_c),
            "knockout.wort_out_f": c_to_f(p.wort_out_c),
            "knockout.o2_slpm": p.o2_slpm,
            "knockout.do_ppm": p.do_ppm,
            "knockout.pump_running": p.wort_lpm > 0,
        }
        if tag not in values:
            raise KeyError(f"no model value for {pub.key}")
        value = values[tag]
        return value if isinstance(value, bool) else max(float(value), 0.0)

    def output_value(self, name: str) -> Value:
        """Outputs to utilities, building, cellar, and power."""
        p = self.proc
        match name:
            case "steam.demand_kw":
                return p.steam_w / 1e3
            case "glycol.load_kw":
                return p.glycol_w / 1e3
            case "drain.gpm":
                return 15.0 if p.drain_s > 0 else 0.0  # rinse and trub dump
            case "hood.heat_kw":
                return p.hood_w / 1e3
            case "knockout.gpm":
                return (
                    p.wort_lpm / LPM_PER_GPM if self.on("knockout.to_fv_valve") else 0.0
                )
            case "batch.phase":
                return float(self.pilot.phase)
            case "batch.completed":
                return float(p.brews_completed)
            case "batch.active":
                return float(int(p.grain_kg > 0) + int(p.kettle.liters > 50.0))
            case "load.mcc1.kw":
                return self.mcc1_kw
        raise KeyError(f"no output {name}")

    @property
    def mcc1_kw(self) -> float:
        """MCC-1 electrical load: mill, auger, rake, wort pump, controls."""
        p = self.proc
        kw = 0.6
        kw += 7.5 * min(p.mill_hz / 60.0, 1.0)
        kw += 1.5 if p.mill_hz > 1 and self.on("mill.auger_run") else 0.0
        kw += 2.2 if self.on("mlt.rake_run") else 0.0
        kw += (
            3.7 * (self.num("knockout.pump_speed_pct") / 100) ** 3
            if p.wort_lpm > 0
            else 0.0
        )
        return kw

    def truth(self) -> dict[str, Any]:
        """Published values plus the batch state."""
        doc = super().truth()
        p = self.proc
        doc["batch"] = {
            "phase": self.pilot.phase,
            "batch_id": self.batch_id,
            "autopilot": self.autopilot,
            "boil_min": p.boil_min,
            "kettle_plato": p.kettle.plato,
            "ibu": self.ibu,
            "mash_fermentability": p.mash.fermentability,
            "wort_boil_c": wort_boiling_point_c(self.ambient_kpa, p.kettle.plato),
        }
        return doc
