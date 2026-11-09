"""Cellar process model: fermenters with kinetics, jackets, and headspace.

Combines the de Andrés-Toro kinetics (compressed time), the jacket thermal
model (wall time), and a headspace CO2 balance with an automatic spunding
valve (wall time). See DESIGN.md §6.3, §9.3, and §9.4.

Simplifications: the headspace is treated as CO2 (it is purged quickly by
fermentation gas), dissolved CO2 is not tracked, and the spunding valve is
an ideal relief at its setpoint.
"""

from __future__ import annotations

import math
import random
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from cherrymountain_sim.carbonation import BrightTank
from cherrymountain_sim.fermentation import (
    ALE,
    CO2_PER_ETHANOL,
    HEAT_KJ_PER_KG_EXTRACT,
    Fermentation,
    FermentState,
    KineticParams,
    Wort,
    pitch,
)
from cherrymountain_sim.thermal import JacketedVessel, Zone, c_to_f, f_to_c
from cherrymountain_sim.timebase import TimeBase

LITERS_PER_BBL: float = 117.348
R_GAS: float = 8.314462  # J/(mol K)
CO2_G_PER_MOL: float = 44.01
PA_PER_PSI: float = 6894.757
#: Mechanical vacuum relief opens this far below ambient (psi).
VACUUM_RELIEF_PSI: float = 0.3
#: Longest kinetic sub-step, hours.
MAX_KINETIC_STEP_H: float = 0.05


class FermenterConfig(BaseModel):
    """Geometry and heat-transfer constants for one 30 bbl unitank."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    volume_bbl: float = 30.0
    headspace_l: float = 900.0
    side_fraction: float = 0.75  # liquid share of the side zone
    ua_side_w_k: float = 1050.0  # 300 W/m²K x 3.5 m²
    ua_cone_w_k: float = 360.0  # 300 W/m²K x 1.2 m²
    ua_mix_w_k: float = 400.0
    ua_ambient_side_w_k: float = 12.0
    ua_ambient_cone_w_k: float = 4.0
    steel_side_kg: float = 600.0
    steel_cone_kg: float = 200.0
    wort_kg_per_l: float = 1.04
    pitch_g_l: float = 2.5  # ale pitch (lager reference uses 4 g/L)
    wort: Wort = Field(default_factory=Wort)
    params: KineticParams = ALE

    @property
    def volume_l(self) -> float:
        """Working volume, L."""
        return self.volume_bbl * LITERS_PER_BBL


class Environment(BaseModel):
    """Boundary conditions from other federates (or their defaults)."""

    glycol_supply_f: float = 28.0
    ambient_f: float = 68.0  # cellar room
    ambient_psia: float = 12.1
    knockout_gpm: float = 0.0  # wort arriving from the brewhouse
    beer_draw_gpm: float = 0.0  # beer leaving BBT-202 for packaging
    co2_supply_psig: float = 60.0


class Fluxes(BaseModel):
    """Real-time-equivalent fluxes exported by one fermenter."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    co2_gen_gps: float = 0.0  # CO2 evolved by fermentation, g/s
    co2_vent_gps: float = 0.0  # CO2 vented to the room by spunding, g/s
    heat_w: float = 0.0  # fermentation heat, W
    jacket_w: float = 0.0  # heat removed by the glycol jackets, W


class InitialState(BaseModel):
    """Per-vessel initial state (from plantgen's federate config).

    Fermenters use ``empty`` or ``fermenting``; bright tanks use ``empty``,
    ``carbonating``, or ``serving`` with ``level_pct`` and ``co2_vol``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    phase: Literal["empty", "fermenting", "carbonating", "serving"] = "empty"
    sim_hours: float = 0.0  # time since pitch
    temp_f: float = 66.0
    dry_hop_lb_per_bbl: float = 0.0
    spund_sp_psig: float | None = None
    level_pct: float | None = None
    co2_vol: float | None = None


class Leak(BaseModel):
    """A scenario CO2 release into the cellar room (event API)."""

    unit: str
    gps: float
    remaining_s: float


class Fermenter:
    """One FV: kinetics, two jacket zones (side, cone), and headspace."""

    unit: str
    cfg: FermenterConfig
    env: Environment
    kinetics: Fermentation
    state: FermentState | None
    vessel: JacketedVessel
    spund_sp_psia: float
    gas_mol: float
    spund_open: bool
    fluxes: Fluxes
    co2_vented_g: float
    co2_generated_g: float
    liquid_l: float

    def __init__(
        self,
        unit: str,
        env: Environment,
        cfg: FermenterConfig | None = None,
        temp_f: float = 66.0,
    ) -> None:
        self.unit = unit
        self.cfg = cfg or FermenterConfig()
        self.env = env
        self.kinetics = Fermentation(self.cfg.wort.fermentable, self.cfg.params)
        self.state = None
        t_c = f_to_c(temp_f)
        self.vessel = JacketedVessel(
            [
                Zone(
                    name="side",
                    liquid_kg=0.0,
                    ua_jacket_w_k=self.cfg.ua_side_w_k,
                    ua_ambient_w_k=self.cfg.ua_ambient_side_w_k,
                    temp_c=t_c,
                    steel_kg=self.cfg.steel_side_kg,
                ),
                Zone(
                    name="cone",
                    liquid_kg=0.0,
                    ua_jacket_w_k=self.cfg.ua_cone_w_k,
                    ua_ambient_w_k=self.cfg.ua_ambient_cone_w_k,
                    temp_c=t_c,
                    steel_kg=self.cfg.steel_cone_kg,
                ),
            ],
            self.cfg.ua_mix_w_k,
        )
        self.spund_sp_psia = env.ambient_psia + 1.0
        self.gas_mol = self.mol_at(env.ambient_psia)
        self.spund_open = False
        self.fluxes = Fluxes()
        self.co2_vented_g = 0.0  # running totals for mass-balance checks
        self.co2_generated_g = 0.0
        self.liquid_l = 0.0

    # -- setup -------------------------------------------------------------------------

    def fill_and_pitch(self, temp_f: float | None = None) -> None:
        """Fill with wort and pitch yeast (knockout)."""
        self._set_liquid(self.cfg.volume_l)
        if temp_f is not None:
            self.vessel.zone("side").temp_c = f_to_c(temp_f)
            self.vessel.zone("cone").temp_c = f_to_c(temp_f)
        self.state = pitch(self.cfg.wort.fermentable, self.cfg.pitch_g_l)

    def _set_liquid(self, liters: float) -> None:
        self.liquid_l = liters
        liquid = liters * self.cfg.wort_kg_per_l
        side = self.vessel.zone("side")
        cone = self.vessel.zone("cone")
        side.liquid_kg = liquid * self.cfg.side_fraction
        cone.liquid_kg = liquid - side.liquid_kg

    def receive(self, liters: float, temp_f: float) -> None:
        """Knockout: wort arrives (oxygenated and pitched in line).

        The yeast is pitched with the first wort, so the kinetic
        concentrations hold while the vessel fills; the fluxes scale with
        the volume in the tank. A second brew tops the vessel up.
        """
        if liters <= 0:
            return
        new = min(self.liquid_l + liters, self.cfg.volume_l)
        zones = (self.vessel.zone("side"), self.vessel.zone("cone"))
        if self.liquid_l <= 0:
            for z in zones:
                z.temp_c = f_to_c(temp_f)
        else:  # mix the arriving wort into each zone
            frac = (new - self.liquid_l) / new
            for z in zones:
                z.temp_c += frac * (f_to_c(temp_f) - z.temp_c)
        self._set_liquid(new)
        if self.state is None:
            self.state = pitch(self.cfg.wort.fermentable, self.cfg.pitch_g_l)

    def advance_kinetics(self, sim_hours: float, temp_f: float) -> None:
        """Run the kinetics alone (isothermal), e.g. to set an initial state."""
        if self.state is None:
            raise RuntimeError(f"{self.unit} is empty")
        t_k = f_to_c(temp_f) + 273.15
        n = max(1, math.ceil(sim_hours / MAX_KINETIC_STEP_H))
        for _ in range(n):
            self.state = self.kinetics.step(self.state, t_k, sim_hours / n)

    def dry_hop(self, dose_lb_per_bbl: float) -> None:
        """Add dry hops; starts the hop-creep sugar source.

        Hop enzymes convert residual dextrins to fermentable sugar, so the
        converted extract moves from the residual pool (gravity unchanged).
        """
        if self.state is None:
            raise RuntimeError(f"{self.unit} is empty")
        extra = dose_lb_per_bbl * self.cfg.params.hop_creep_yield
        self.state = self.state.model_copy(
            update={
                "hop_extract": self.state.hop_extract + extra,
                "residual_offset": self.state.residual_offset - extra,
            }
        )

    # -- observables -------------------------------------------------------------------

    @property
    def filled(self) -> bool:
        """True when the vessel holds wort or beer."""
        return self.state is not None

    def zone_temp_f(self, zone: str) -> float:
        """Zone temperature, °F."""
        return c_to_f(self.vessel.zone(zone).temp_c)

    def valve(self, zone: str) -> bool:
        """Glycol solenoid state for a zone."""
        return self.vessel.zone(zone).valve_open

    def set_valve(self, zone: str, open_: bool) -> None:
        """Command a glycol solenoid."""
        self.vessel.zone(zone).valve_open = bool(open_)

    @property
    def gravity_plato(self) -> float:
        """Apparent extract the density meter reads (0 when empty)."""
        if self.state is None:
            return 0.0
        return self.cfg.wort.gravity(self.state)

    @property
    def pressure_psia(self) -> float:
        """Headspace pressure, psia (gas at the side-zone temperature)."""
        t_k = self.vessel.zone("side").temp_c + 273.15
        pa = self.gas_mol * R_GAS * t_k / (self.cfg.headspace_l / 1000.0)
        return pa / PA_PER_PSI

    def mol_at(self, psia: float) -> float:
        """Headspace gas moles that give ``psia`` at the current temperature."""
        t_k = self.vessel.zone("side").temp_c + 273.15
        return psia * PA_PER_PSI * (self.cfg.headspace_l / 1000.0) / (R_GAS * t_k)

    # -- dynamics ----------------------------------------------------------------------

    def step(self, wall_dt_s: float, tb: TimeBase) -> Fluxes:
        """Advance ``wall_dt_s`` wall seconds.

        Fluxes come from the kinetic rates at the start of the step, per sim
        hour, so they are real-time-equivalent (§9.4). The kinetic state then
        advances by ``wall_dt_s * compression``.
        """
        co2_gps = 0.0
        heat_w = 0.0
        if self.state is not None:
            t_k = self.vessel.mean_temp_c + 273.15
            r = self.kinetics.rates(self.state, t_k)
            vol = self.liquid_l
            co2_gps = tb.flux_per_second(CO2_PER_ETHANOL * r.ethanol * vol)
            extract_kg_per_h = max(r.sugar_consumed, 0.0) * vol / 1000.0
            heat_w = 1e3 * tb.flux_per_second(extract_kg_per_h * HEAT_KJ_PER_KG_EXTRACT)
            sim_h = tb.kinetic_hours(wall_dt_s)
            n = max(1, math.ceil(sim_h / MAX_KINETIC_STEP_H))
            for _ in range(n):
                self.state = self.kinetics.step(self.state, t_k, sim_h / n)

        jacket_w = self.vessel.step(
            wall_dt_s,
            heat_w,
            f_to_c(self.env.glycol_supply_f),
            f_to_c(self.env.ambient_f),
        )

        # Headspace: real-time CO2 inflow, ideal-gas pressure, spunding relief.
        self.gas_mol += co2_gps * wall_dt_s / CO2_G_PER_MOL
        self.co2_generated_g += co2_gps * wall_dt_s
        vent_g = 0.0
        sp = max(self.spund_sp_psia, self.env.ambient_psia)
        if self.pressure_psia > sp:
            excess = self.gas_mol - self.mol_at(sp)
            self.gas_mol -= excess
            vent_g = excess * CO2_G_PER_MOL
            self.spund_open = True
        else:
            self.spund_open = False
        floor = self.env.ambient_psia - VACUUM_RELIEF_PSI
        if self.pressure_psia < floor:  # vacuum relief admits air
            self.gas_mol = self.mol_at(floor)
        self.co2_vented_g += vent_g
        self.fluxes = Fluxes(
            co2_gen_gps=co2_gps,
            co2_vent_gps=vent_g / wall_dt_s,
            heat_w=heat_w,
            jacket_w=jacket_w,
        )
        return self.fluxes


class Cellar:
    """All fermenters plus the sim clock and cellar-level aggregates."""

    tb: TimeBase
    env: Environment
    sim_hours: float
    fermenters: dict[str, Fermenter]
    bbts: dict[str, BrightTank]
    leaks: list[Leak]
    knockout_target: str | None

    def __init__(
        self,
        units: list[str],
        *,
        tb: TimeBase | None = None,
        env: Environment | None = None,
        initial: dict[str, InitialState] | None = None,
        start_sim_hours: float = 0.0,
        cfg: FermenterConfig | None = None,
        bbts: list[str] | None = None,
    ) -> None:
        self.tb = tb or TimeBase()
        self.env = env or Environment()
        self.sim_hours = start_sim_hours
        self.fermenters = {}
        self.bbts = {}
        self.leaks = []
        self.knockout_target = None
        for unit in units:
            init = (initial or {}).get(unit) or InitialState()
            fv = Fermenter(unit, self.env, cfg, temp_f=init.temp_f)
            if init.phase == "fermenting":
                fv.fill_and_pitch(init.temp_f)
                if init.sim_hours > 0:
                    fv.advance_kinetics(init.sim_hours, init.temp_f)
                if init.dry_hop_lb_per_bbl:
                    fv.dry_hop(init.dry_hop_lb_per_bbl)
            if init.spund_sp_psig is not None:
                fv.spund_sp_psia = self.env.ambient_psia + init.spund_sp_psig
                fv.gas_mol = fv.mol_at(fv.spund_sp_psia)
            self.fermenters[unit] = fv
        for unit in bbts or []:
            init = (initial or {}).get(unit) or InitialState(phase="empty", temp_f=34.0)
            phase = init.phase if init.phase != "fermenting" else "carbonating"
            self.bbts[unit] = BrightTank(
                unit,
                phase=phase,
                temp_f=init.temp_f,
                level_pct=init.level_pct or 0.0,
                co2_vol=init.co2_vol or 0.0,
                ambient_psia=self.env.ambient_psia,
            )

    def add_leak(self, unit: str, gps: float, seconds: float) -> None:
        """Start a CO2 release at ``unit`` (scenario event)."""
        self.leaks.append(Leak(unit=unit, gps=gps, remaining_s=seconds))

    def step(self, wall_dt_s: float) -> None:
        """Advance every vessel and the sim clock."""
        self._knockout(wall_dt_s)
        for fv in self.fermenters.values():
            fv.step(wall_dt_s, self.tb)
        serving = next(
            (u for u, b in self.bbts.items() if b.outlet_open and b.liquid_l > 0), None
        )
        for unit, bbt in self.bbts.items():
            bbt.step(
                wall_dt_s,
                glycol_f=self.env.glycol_supply_f,
                ambient_f=self.env.ambient_f,
                draw_gpm=self.env.beer_draw_gpm if unit == serving else 0.0,
                supply_psig=self.env.co2_supply_psig,
            )
        for leak in self.leaks:
            leak.remaining_s -= wall_dt_s
        self.leaks = [lk for lk in self.leaks if lk.remaining_s > 0]
        self.sim_hours += self.tb.kinetic_hours(wall_dt_s)

    def _knockout(self, wall_dt_s: float) -> None:
        """Route knockout wort to the first empty (or filling) fermenter."""
        gpm = self.env.knockout_gpm
        if gpm <= 0:
            return
        target = self.knockout_target
        if target is None or (
            self.fermenters[target].liquid_l >= self.fermenters[target].cfg.volume_l
        ):
            target = next(
                (u for u, fv in self.fermenters.items() if not fv.filled), None
            )
            self.knockout_target = target
        if target is not None:
            self.fermenters[target].receive(gpm * 3.78541 / 60.0 * wall_dt_s, 64.0)

    @property
    def co2_vent_gps(self) -> float:
        """CO2 vented into the cellar room, g/s (to the building federate)."""
        fv_vent = sum(fv.fluxes.co2_vent_gps for fv in self.fermenters.values())
        return fv_vent + sum(lk.gps for lk in self.leaks)

    @property
    def co2_draw_slpm(self) -> float:
        """CO2 drawn from the supply by the bright tanks, standard L/min."""
        return sum(b.co2_slpm for b in self.bbts.values())

    @property
    def glycol_load_kw(self) -> float:
        """Heat the jackets put into the glycol loop, kW (to utilities)."""
        return sum(fv.fluxes.jacket_w for fv in self.fermenters.values()) / 1e3


class Sensor:
    """Adds seeded Gaussian measurement noise to published values."""

    rng: random.Random
    enabled: bool

    def __init__(self, seed: int = 0, enabled: bool = True) -> None:
        self.rng = random.Random(seed)
        self.enabled = enabled

    def read(self, value: float, sigma: float) -> float:
        """Return ``value`` plus noise with standard deviation ``sigma``."""
        if not self.enabled or sigma <= 0:
            return value
        return value + self.rng.gauss(0.0, sigma)
