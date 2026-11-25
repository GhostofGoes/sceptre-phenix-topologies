"""Lumped thermal model of a glycol-jacketed fermenter (DESIGN.md §9.3).

Each jacket zone follows::

    m·c_p·dT/dt = Q_ferm - U·A·(T - T_gly)·u_valve - UA_amb·(T - T_amb)
                  - UA_mix·(T - T_other)

``u_valve`` is the solenoid state (0 or 1). A dimple jacket has U of about
150-400 W/m²K; the defaults use 300 W/m²K. Temperatures are in °C, powers
in W, time in seconds. This model always runs in wall-clock time.
"""

from __future__ import annotations

import math

from pydantic import BaseModel


def f_to_c(t_f: float) -> float:
    """Fahrenheit to Celsius."""
    return (t_f - 32.0) / 1.8


def c_to_f(t_c: float) -> float:
    """Celsius to Fahrenheit."""
    return t_c * 1.8 + 32.0


class Zone(BaseModel):
    """One jacket zone: liquid plus the steel it wets (mutable state)."""

    name: str
    liquid_kg: float
    ua_jacket_w_k: float
    ua_ambient_w_k: float
    temp_c: float
    steel_kg: float = 0.0
    cp_liquid: float = 4000.0  # J/(kg K), wort and beer
    cp_steel: float = 500.0  # J/(kg K), stainless steel
    valve_open: bool = False

    @property
    def heat_capacity(self) -> float:
        """Total heat capacity, J/K."""
        return self.liquid_kg * self.cp_liquid + self.steel_kg * self.cp_steel

    def jacket_duty_w(self, t_glycol_c: float) -> float:
        """Heat removed by the jacket right now, W (zero when closed)."""
        if not self.valve_open:
            return 0.0
        return self.ua_jacket_w_k * (self.temp_c - t_glycol_c)


class JacketedVessel:
    """Two or more zones coupled by internal mixing (``ua_mix_w_k``)."""

    zones: list[Zone]
    ua_mix_w_k: float

    def __init__(self, zones: list[Zone], ua_mix_w_k: float = 0.0) -> None:
        self.zones = zones
        self.ua_mix_w_k = ua_mix_w_k

    def zone(self, name: str) -> Zone:
        """Look up a zone by name."""
        for z in self.zones:
            if z.name == name:
                return z
        raise KeyError(name)

    @property
    def liquid_kg(self) -> float:
        """Total liquid mass, kg."""
        return sum(z.liquid_kg for z in self.zones)

    @property
    def mean_temp_c(self) -> float:
        """Liquid-mass-weighted mean temperature (heat-capacity weighted if empty)."""
        weight = self.liquid_kg
        if weight > 0:
            return sum(z.temp_c * z.liquid_kg for z in self.zones) / weight
        cap = sum(z.heat_capacity for z in self.zones)
        return sum(z.temp_c * z.heat_capacity for z in self.zones) / cap

    def _derivs(
        self, temps: list[float], q_w: list[float], t_gly: float, t_amb: float
    ) -> list[float]:
        n = len(self.zones)
        mean = sum(temps) / n
        out = []
        for z, t, q in zip(self.zones, temps, q_w, strict=True):
            jacket = z.ua_jacket_w_k * (t - t_gly) if z.valve_open else 0.0
            ambient = z.ua_ambient_w_k * (t - t_amb)
            # Mixing couples each zone to the mean of the others.
            others = (mean * n - t) / (n - 1) if n > 1 else t
            mixing = self.ua_mix_w_k * (t - others)
            out.append((q - jacket - ambient - mixing) / z.heat_capacity)
        return out

    def step(
        self, dt_s: float, q_ferm_w: float, t_glycol_c: float, t_ambient_c: float
    ) -> float:
        """Advance ``dt_s`` seconds; returns the mean jacket duty (W).

        Fermentation heat is split between zones by liquid mass. Explicit
        Euler with sub-steps well inside the stability limit.
        """
        liquid = self.liquid_kg
        q = [
            q_ferm_w * (z.liquid_kg / liquid) if liquid > 0 else 0.0 for z in self.zones
        ]
        tau = min(
            z.heat_capacity / (z.ua_jacket_w_k + z.ua_ambient_w_k + 2 * self.ua_mix_w_k)
            for z in self.zones
        )
        n_sub = max(1, math.ceil(dt_s / (0.1 * tau)))
        h = dt_s / n_sub
        duty = 0.0
        for _ in range(n_sub):
            duty += sum(z.jacket_duty_w(t_glycol_c) for z in self.zones) * h
            temps = [z.temp_c for z in self.zones]
            d = self._derivs(temps, q, t_glycol_c, t_ambient_c)
            for z, dt_dt in zip(self.zones, d, strict=True):
                z.temp_c += h * dt_dt
        return duty / dt_s


def single_zone_steady_state(
    q_w: float, ua_jacket: float, t_glycol: float, ua_ambient: float, t_ambient: float
) -> float:
    """Hand-calc steady state of one zone with its valve open (°C)."""
    return (q_w + ua_jacket * t_glycol + ua_ambient * t_ambient) / (
        ua_jacket + ua_ambient
    )
