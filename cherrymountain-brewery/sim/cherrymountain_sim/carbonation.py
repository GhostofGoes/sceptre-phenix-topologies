"""Carbonation and the bright beer tanks (DESIGN.md §6.3, §9.3).

**Equilibrium.** The ASBC/Zahm-Nagel relationship, a common empirical fit in
sea-level gauge pressure (psig) and °F::

    P = -16.6999 - 0.0101059·T + 0.00116512·T² + 0.173354·T·V
        + 4.24267·V - 0.0684226·V²

The fit is really about the absolute pressure over the beer, so on site
(12.1 psia ambient) the gauge reads the sea-level value plus the ambient
difference (14.696 - 12.1 ≈ 2.6 psi): 2.6 vol at 34 °F is about 10.2 psig at
sea level and about 12.8 psig on site.

**Cross-check.** The fit's provenance is unconfirmed, so :func:`henry_volumes`
computes the same equilibrium from Henry's law, k_H(298.15 K) = 0.034
mol·kg⁻¹·bar⁻¹ with a van 't Hoff temperature dependence (d ln k_H / d(1/T) =
2400 K, Sander 2015). One "volume" is one liter of CO2 at 0 °C and 1 atm per
liter of beer. The tests hold the two within a few percent over the cellar's
range.

**Bright tank.** :class:`BrightTank` is a 30 bbl BBT with a glycol jacket, a
carbonation stone fed by a CO2 flow valve, a headspace with relief at 15 psig
MAWP, a top-pressure regulator while serving, and an outlet to packaging.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel

from cherrymountain_sim.thermal import JacketedVessel, Zone, c_to_f, f_to_c

SEA_LEVEL_PSIA: float = 14.696
PA_PER_PSI: float = 6894.757
PA_PER_BAR: float = 1.0e5
R_GAS: float = 8.314462
#: Molar volume of an ideal gas at 0 °C and 1 atm, L/mol (defines "volumes").
MOLAR_VOLUME_STP_L: float = 22.414
#: Henry's law constant of CO2 in water at 298.15 K, mol/(kg bar).
KH_298: float = 0.034
#: van 't Hoff temperature dependence of k_H, K.
KH_VANT_HOFF_K: float = 2400.0
BEER_KG_PER_L: float = 1.010
LITERS_PER_BBL: float = 117.348
#: Zahm-Nagel fit coefficients (psig, °F, volumes).
ZN: tuple[float, float, float, float, float, float] = (
    -16.6999,
    -0.0101059,
    0.00116512,
    0.173354,
    4.24267,
    -0.0684226,
)


def zahm_nagel_psig(temp_f: float, volumes: float) -> float:
    """Sea-level gauge pressure (psig) in equilibrium with ``volumes`` at ``temp_f``."""
    a, b, c, d, e, f = ZN
    t, v = temp_f, volumes
    return a + b * t + c * t * t + d * t * v + e * v + f * v * v


def zahm_nagel_volumes(temp_f: float, psig_sea_level: float) -> float:
    """Inverse of :func:`zahm_nagel_psig`: the smaller positive root in V."""
    a, b, c, d, e, f = ZN
    t = temp_f
    qa = f
    qb = d * t + e
    qc = a + b * t + c * t * t - psig_sea_level
    disc = qb * qb - 4 * qa * qc
    if disc < 0:
        raise ValueError("no equilibrium carbonation for these conditions")
    roots = sorted(
        r for r in ((-qb + s * math.sqrt(disc)) / (2 * qa) for s in (1, -1)) if r >= 0
    )
    return roots[0] if roots else 0.0


def site_gauge_psig(temp_f: float, volumes: float, ambient_psia: float = 12.1) -> float:
    """Gauge pressure on site for ``volumes`` at ``temp_f`` (altitude corrected)."""
    return zahm_nagel_psig(temp_f, volumes) + (SEA_LEVEL_PSIA - ambient_psia)


def equilibrium_volumes(temp_f: float, psia: float) -> float:
    """Equilibrium volumes under an absolute CO2 pressure (Zahm-Nagel)."""
    return max(zahm_nagel_volumes(temp_f, psia - SEA_LEVEL_PSIA), 0.0)


def henry_kh(temp_k: float) -> float:
    """Henry's law solubility of CO2, mol/(kg bar), van 't Hoff corrected."""
    return KH_298 * math.exp(KH_VANT_HOFF_K * (1.0 / temp_k - 1.0 / 298.15))


def henry_volumes(temp_f: float, psia: float) -> float:
    """Equilibrium volumes from Henry's law at an absolute CO2 pressure."""
    temp_k = f_to_c(temp_f) + 273.15
    mol_per_kg = henry_kh(temp_k) * psia * PA_PER_PSI / PA_PER_BAR
    return mol_per_kg * BEER_KG_PER_L * MOLAR_VOLUME_STP_L


class BrightTankConfig(BaseModel):
    """Geometry and transfer constants of one 30 bbl BBT."""

    capacity_bbl: float = 30.0
    vessel_l: float = 34.0 * LITERS_PER_BBL
    ua_jacket_w_k: float = 1200.0
    ua_ambient_w_k: float = 15.0
    steel_kg: float = 750.0
    #: Stone CO2 flow at 100% valve, standard L/min.
    stone_max_slpm: float = 200.0
    #: Fraction of stone gas that dissolves on the way up.
    stone_efficiency: float = 0.6
    #: Surface exchange between headspace and beer, 1/h.
    kla_per_h: float = 0.15
    relief_psig: float = 15.0
    #: Serving top pressure regulator, standard L/min at full demand.
    regulator_max_slpm: float = 150.0


class BrightTank:
    """One BBT: jacket (wall time), dissolved CO2, headspace gas."""

    unit: str
    cfg: BrightTankConfig
    ambient_psia: float
    vessel: JacketedVessel
    liquid_l: float
    co2_vol: float
    gas_mol: float
    regulator_psia: float | None
    outlet_open: bool
    co2_valve_pct: float
    stone_slpm: float
    regulator_slpm: float
    vented_mol: float

    def __init__(
        self,
        unit: str,
        *,
        phase: Literal["empty", "carbonating", "serving"] = "empty",
        temp_f: float = 34.0,
        level_pct: float = 0.0,
        co2_vol: float = 0.0,
        ambient_psia: float = 12.1,
        cfg: BrightTankConfig | None = None,
    ) -> None:
        self.unit = unit
        self.cfg = cfg or BrightTankConfig()
        self.ambient_psia = ambient_psia
        self.liquid_l = (
            0.0
            if phase == "empty"
            else level_pct / 100 * self.cfg.capacity_bbl * LITERS_PER_BBL
        )
        self.vessel = JacketedVessel(
            [
                Zone(
                    name="jacket",
                    liquid_kg=self.liquid_l * BEER_KG_PER_L,
                    ua_jacket_w_k=self.cfg.ua_jacket_w_k,
                    ua_ambient_w_k=self.cfg.ua_ambient_w_k,
                    temp_c=f_to_c(temp_f),
                    steel_kg=self.cfg.steel_kg,
                )
            ]
        )
        self.co2_vol = co2_vol if self.liquid_l > 0 else 0.0
        head_psia = ambient_psia
        if self.liquid_l > 0 and co2_vol > 0:
            head_psia = max(
                ambient_psia,
                site_gauge_psig(temp_f, co2_vol, ambient_psia) + ambient_psia,
            )
        self.gas_mol = self._mol_at(head_psia)
        self.regulator_psia = head_psia if phase == "serving" else None
        self.outlet_open = phase == "serving"
        self.co2_valve_pct = 0.0
        self.stone_slpm = 0.0
        self.regulator_slpm = 0.0
        self.vented_mol = 0.0

    # -- state -------------------------------------------------------------------------

    @property
    def temp_f(self) -> float:
        """Beer temperature, °F."""
        return c_to_f(self.vessel.zones[0].temp_c)

    @property
    def headspace_l(self) -> float:
        """Gas volume above the beer, L."""
        return max(self.cfg.vessel_l - self.liquid_l, 50.0)

    def _mol_at(self, psia: float) -> float:
        t_k = self.vessel.zones[0].temp_c + 273.15
        return psia * PA_PER_PSI * self.headspace_l / 1000.0 / (R_GAS * t_k)

    @property
    def pressure_psia(self) -> float:
        """Headspace pressure, psia."""
        t_k = self.vessel.zones[0].temp_c + 273.15
        return self.gas_mol * R_GAS * t_k / (self.headspace_l / 1000.0) / PA_PER_PSI

    @property
    def level_pct(self) -> float:
        """Level as a percentage of the 30 bbl working volume."""
        return 100.0 * self.liquid_l / (self.cfg.capacity_bbl * LITERS_PER_BBL)

    @property
    def co2_slpm(self) -> float:
        """CO2 drawn from the supply (stone plus top pressure), standard L/min."""
        return self.stone_slpm + self.regulator_slpm

    # -- I/O ---------------------------------------------------------------------------

    def read(self, point: str) -> float | bool:
        """A device point value."""
        match point:
            case "temp_f":
                return self.temp_f
            case "pressure_psia":
                return self.pressure_psia
            case "level_pct":
                return self.level_pct
            case "co2_vol":
                return self.co2_vol
            case "co2_slpm":
                return self.stone_slpm
            case "valve":
                return self.vessel.zones[0].valve_open
            case "outlet_valve":
                return self.outlet_open
            case "co2_valve_pct":
                return self.co2_valve_pct
        raise KeyError(f"bbt has no point {point}")

    def write(self, point: str, value: Any) -> None:
        """Apply a PLC write."""
        match point:
            case "valve":
                self.vessel.zones[0].valve_open = bool(value)
            case "outlet_valve":
                self.outlet_open = bool(value)
            case "co2_valve_pct":
                self.co2_valve_pct = min(max(float(value), 0.0), 100.0)
            case _:
                raise KeyError(f"bbt has no writable point {point}")

    # -- dynamics ----------------------------------------------------------------------

    def step(
        self,
        dt_s: float,
        *,
        glycol_f: float,
        ambient_f: float,
        draw_gpm: float = 0.0,
        supply_psig: float = 60.0,
    ) -> None:
        """Advance ``dt_s`` seconds (wall time)."""
        if draw_gpm > 0 and self.outlet_open and self.liquid_l > 0:
            gone = min(draw_gpm * 3.78541 / 60.0 * dt_s, self.liquid_l)
            self.liquid_l -= gone
        zone = self.vessel.zones[0]
        zone.liquid_kg = self.liquid_l * BEER_KG_PER_L
        self.vessel.step(dt_s, 0.0, f_to_c(glycol_f), f_to_c(ambient_f))
        mol_per_vol = self.liquid_l / MOLAR_VOLUME_STP_L  # mol per 1 vol of CO2
        # Stone: gas the supply can push at its pressure.
        supply_ok = supply_psig + self.ambient_psia > self.pressure_psia + 1.0
        self.stone_slpm = (
            self.co2_valve_pct / 100 * self.cfg.stone_max_slpm
            if supply_ok and self.liquid_l > 0
            else 0.0
        )
        stone_mol = self.stone_slpm / MOLAR_VOLUME_STP_L / 60.0 * dt_s
        dissolved_mol = 0.0
        if mol_per_vol > 0:
            v_eq = equilibrium_volumes(self.temp_f, self.pressure_psia)
            # Direct dissolution of stone gas, then surface exchange.
            direct = min(
                stone_mol * self.cfg.stone_efficiency,
                max(v_eq + 0.3 - self.co2_vol, 0.0) * mol_per_vol,
            )
            surface = self.cfg.kla_per_h / 3600.0 * (v_eq - self.co2_vol) * dt_s
            dissolved_mol = direct + surface * mol_per_vol
            self.co2_vol += dissolved_mol / mol_per_vol
        self.gas_mol += stone_mol - dissolved_mol
        # Serving: the regulator holds top pressure from the supply.
        self.regulator_slpm = 0.0
        if self.regulator_psia is not None and self.pressure_psia < self.regulator_psia:
            need = self._mol_at(self.regulator_psia) - self.gas_mol
            cap = self.cfg.regulator_max_slpm / MOLAR_VOLUME_STP_L / 60.0 * dt_s
            add = min(need, cap) if supply_ok else 0.0
            self.gas_mol += add
            self.regulator_slpm = add * MOLAR_VOLUME_STP_L * 60.0 / dt_s
        relief = self.ambient_psia + self.cfg.relief_psig
        if self.pressure_psia > relief:
            excess = self.gas_mol - self._mol_at(relief)
            self.gas_mol -= excess
            self.vented_mol += excess
