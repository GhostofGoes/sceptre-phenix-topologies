"""Building federate: taproom RTU-1, cold room, ventilation, cellar CO2 (§8).

Low-order models from DESIGN.md §8.3, stepped every 10 s:

- **Taproom 3R2C.** C_a·dT_a/dt = (T_m − T_a)/R_am + (T_o − T_a)/R_ao + Q_hvac
  + Q_int + f·Q_sol and C_m·dT_m/dt = (T_a − T_m)/R_am + (T_o − T_m)/R_mo +
  (1 − f)·Q_sol, with an RTU (two 17.5 kW DX stages, 40 kW gas heat,
  economizer, supply fan) and an occupancy-driven CO2 balance (taproom open
  11:00-22:00).
- **Cold room 1R1C.** C·dT/dt = (T_o − T)/R + Q_door − Q_ref·u, with a
  condensing-unit thermostat (±1.5 °F), four 20-minute defrosts a day, and
  random door openings while the taproom is open.
- **Cellar CO2.** V·dC/dt = G_CO2·10⁶ + Q_vent·(C_out − C), with G_CO2 =
  ṁ_CO2/ρ_CO2 from the cellar federate and ρ_CO2 = 1.51 kg/m³ at the site's
  83.4 kPa and 68 °F (the sea-level 1.83 kg/m³ would be 22 % off). The four
  sensors sit low, so they read the well-mixed value times a stratification
  factor (1.25) plus a per-sensor offset.

Each controller's local logic runs here, the way the BAS controllers would run
it in their ot-sim logic module: RTU staging and economizer, the cold-room
thermostat and defrost, the CO2 tiers (5,000 ppm starts the purge fans,
15,000 inhibits carbonation, 30,000 evacuates), and the kettle hood exhaust.
A BACnet write to a point overrides the local logic for that point, as a
priority-array write above the controller's own priority would.
"""

from __future__ import annotations

import math
import random
from typing import Any, ClassVar

from pydantic import BaseModel

from cherrymountain_sim.federate import (
    Federate,
    FederateConfig,
    Publication,
    SimClock,
    Value,
)

RHO_CP_AIR: float = 1.0 * 1006.0  # J/(m3 K) at the site's air density
#: CO2 density at 83.4 kPa and 68 °F, kg/m3.
RHO_CO2_SITE: float = 1.51
OUTDOOR_CO2_PPM: float = 420.0
STRATIFICATION: float = 1.25
SENSOR_OFFSETS: tuple[float, ...] = (0.97, 1.02, 1.0, 1.04)
CO2_TIERS: tuple[float, ...] = (5000.0, 15000.0, 30000.0)
# A tier clears only once the sensed value falls 10 % below its limit, so a
# reading hovering at a threshold does not chatter the BACnet alarm.
CO2_TIER_DEADBAND: float = 0.10


def f_to_c(t_f: float) -> float:
    """Fahrenheit to Celsius."""
    return (t_f - 32.0) / 1.8


def c_to_f(t_c: float) -> float:
    """Celsius to Fahrenheit."""
    return t_c * 1.8 + 32.0


class BuildingSettings(BaseModel):
    """Building sim settings (helics.json ``sim``)."""

    seed: int = 0
    open_hour: int = 11
    close_hour: int = 22
    cellar_volume_m3: float = 1100.0
    # Continuous cellar exhaust plus infiltration (about 640 cfm). At a typical
    # 1 g/s of vented CO2 the sensors settle below the 3,500 ppm purge stop, so
    # tier 1 trips only on real releases instead of cycling in normal running.
    cellar_infiltration_m3s: float = 0.3
    purge_fan_m3s: float = 1.4


class Taproom(BaseModel):
    """3R2C zone and RTU state."""

    t_air_c: float = 23.0
    t_mass_c: float = 22.5
    co2_ppm: float = 600.0
    c_air: float = 6.0e6
    c_mass: float = 6.0e7
    ua_am: float = 2500.0
    ua_ao: float = 180.0
    ua_mo: float = 250.0
    solar_m2: float = 12.0
    solar_air_fraction: float = 0.3
    volume_m3: float = 1500.0
    supply_air_c: float = 23.0
    power_kw: float = 0.0


class ColdRoom(BaseModel):
    """1R1C cold room state."""

    temp_c: float = 2.2
    capacity_j_k: float = 2.0e7
    ua: float = 60.0
    ua_door: float = 800.0
    q_ref_w: float = 4400.0
    door_until: float = -1.0
    door_open_s: float = 0.0
    door_day: str = ""
    compressor: bool = False


class Cellar(BaseModel):
    """Cellar room and brewhouse ventilation."""

    co2_ppm: float = 800.0
    temp_c: float = 19.0
    tiers: tuple[bool, bool, bool] = (False, False, False)
    purge: bool = False
    mua_c: float = 20.0


class BuildingFederate(Federate):
    """The building federate."""

    name: ClassVar[str] = "building"
    settings: BuildingSettings
    rng: random.Random
    tap: Taproom
    cold: ColdRoom
    cel: Cellar
    local: dict[str, Value]

    def __init__(self, cfg: FederateConfig, clock: SimClock | None = None) -> None:
        super().__init__(cfg, clock)
        self.settings = BuildingSettings.model_validate(cfg.sim)
        self.rng = random.Random(self.settings.seed)
        self.tap = Taproom()
        self.cold = ColdRoom()
        self.cel = Cellar()
        self.local = {
            "bas-rtu-taproom.econ_pct": 20.0,
            "bas-rtu-taproom.cool_sp_f": 74.0,
            "bas-rtu-taproom.heat_sp_f": 68.0,
            "bas-rtu-taproom.supply_fan": False,
            "bas-rtu-taproom.cool_stage1": False,
            "bas-rtu-taproom.cool_stage2": False,
            "bas-rtu-taproom.heat": False,
            "bas-rtu-taproom.occ_mode": 2.0,
            "bas-coldroom.setpoint_f": 36.0,
            "bas-coldroom.compressor": False,
            "bas-coldroom.defrost": False,
            "bas-vent.exhaust_pct": 20.0,
            "bas-vent.purge_fan_1": False,
            "bas-vent.purge_fan_2": False,
            "bas-vent.mua_enable": False,
        }

    def cmd(self, tag: str) -> Value:
        """Per-point authority: a BACnet write overrides the local logic."""
        return self.commands.get(tag, self.local[tag])

    def on(self, tag: str) -> bool:
        """A binary output."""
        return bool(self.cmd(tag))

    def num(self, tag: str) -> float:
        """An analog output or value."""
        return float(self.cmd(tag))

    def bus(self, name: str) -> bool:
        """Whether a power bus is energized."""
        return self.input(f"power/bus.{name}.energized", 1.0) >= 0.5

    @property
    def occupied(self) -> bool:
        """Taproom open hours."""
        h = self.now.hour
        return self.settings.open_hour <= h < self.settings.close_hour

    @property
    def people(self) -> float:
        """Taproom occupancy (peaks at 18:00-20:00)."""
        if not self.occupied:
            return 2.0
        h = self.now.hour + self.now.minute / 60
        return 15.0 + 85.0 * math.exp(-(((h - 19.0) / 2.5) ** 2))

    # -- dynamics ----------------------------------------------------------------------

    def advance(self, wall_dt_s: float) -> None:
        """Advance every space ``wall_dt_s`` seconds."""
        t_out_c = f_to_c(self.input("weather/ambient.temp_f", 75.0))
        ghi = self.input("weather/ghi.wm2", 0.0)
        n = max(1, math.ceil(wall_dt_s / 10.0))
        h = wall_dt_s / n
        for _ in range(n):
            self._taproom(h, t_out_c, ghi)
            self._coldroom(h, t_out_c)
            self._cellar(h, t_out_c)

    def _taproom(self, dt: float, t_out: float, ghi: float) -> None:
        tap = self.tap
        loc = self.local
        occ = self.occupied
        loc["bas-rtu-taproom.occ_mode"] = 1.0 if occ else 2.0
        mode = self.num("bas-rtu-taproom.occ_mode")
        cool_sp = f_to_c(
            self.num("bas-rtu-taproom.cool_sp_f") + (0 if mode == 1 else 11)
        )
        heat_sp = f_to_c(
            self.num("bas-rtu-taproom.heat_sp_f") - (0 if mode == 1 else 8)
        )
        t = tap.t_air_c
        loc["bas-rtu-taproom.cool_stage1"] = t > cool_sp + 0.3 or (
            bool(loc["bas-rtu-taproom.cool_stage1"]) and t > cool_sp - 0.3
        )
        loc["bas-rtu-taproom.cool_stage2"] = t > cool_sp + 1.1 or (
            bool(loc["bas-rtu-taproom.cool_stage2"]) and t > cool_sp + 0.3
        )
        loc["bas-rtu-taproom.heat"] = t < heat_sp - 0.3 or (
            bool(loc["bas-rtu-taproom.heat"]) and t < heat_sp + 0.3
        )
        free = t_out < t - 3.0 and bool(loc["bas-rtu-taproom.cool_stage1"])
        dcv = min(max((tap.co2_ppm - 800.0) / 400.0, 0.0), 1.0) * 50.0
        loc["bas-rtu-taproom.econ_pct"] = (
            (100.0 if free else 20.0 + dcv) if occ else 0.0
        )
        loc["bas-rtu-taproom.supply_fan"] = occ or any(
            bool(loc[k])
            for k in ("bas-rtu-taproom.cool_stage1", "bas-rtu-taproom.heat")
        )
        powered = self.bus("rtu")
        fan = self.on("bas-rtu-taproom.supply_fan") and powered
        q = 0.0
        kw = 0.0
        if fan:
            kw += 1.5
            q += 1500.0
            stages = int(self.on("bas-rtu-taproom.cool_stage1")) + int(
                self.on("bas-rtu-taproom.cool_stage2")
            )
            q -= 17.5e3 * stages
            kw += 5.25 * stages
            if self.on("bas-rtu-taproom.heat"):
                q += 40e3  # gas heat
        oa = (self.num("bas-rtu-taproom.econ_pct") / 100 * 1.5 if fan else 0.0) + 0.1
        q_oa = oa * RHO_CP_AIR * (t_out - t)
        q_int = 100.0 * self.people + (5e3 if occ else 1e3)
        q_sol = ghi * tap.solar_m2
        f = tap.solar_air_fraction
        dta = (
            (tap.t_mass_c - t) * tap.ua_am
            + (t_out - t) * tap.ua_ao
            + q
            + q_oa
            + q_int
            + f * q_sol
        ) / tap.c_air
        dtm = (
            (t - tap.t_mass_c) * tap.ua_am
            + (t_out - tap.t_mass_c) * tap.ua_mo
            + (1 - f) * q_sol
        ) / tap.c_mass
        tap.t_air_c += dta * dt
        tap.t_mass_c += dtm * dt
        tap.supply_air_c = t + (q / (1.5 * RHO_CP_AIR) if fan else 0.0)
        gen = self.people * 5.2e-6 * 1e6  # ppm·m3/s
        tap.co2_ppm += (gen + oa * (OUTDOOR_CO2_PPM - tap.co2_ppm)) / tap.volume_m3 * dt
        tap.power_kw = kw

    def _coldroom(self, dt: float, t_out: float) -> None:
        c = self.cold
        now = self.now
        day = now.date().isoformat()
        if day != c.door_day:
            c.door_day, c.door_open_s = day, 0.0
        if (
            self.occupied
            and self.t >= c.door_until
            and self.rng.random() < 4.0 / 3600.0 * dt
        ):
            c.door_until = self.t + self.rng.uniform(30.0, 90.0)
        door = self.t < c.door_until
        if door:
            c.door_open_s += dt
        defrost = now.hour % 6 == 0 and now.minute < 20
        self.local["bas-coldroom.defrost"] = defrost
        sp = f_to_c(self.num("bas-coldroom.setpoint_f"))
        band = 1.5 / 1.8
        want = c.temp_c > sp + band or (c.compressor and c.temp_c > sp - band)
        self.local["bas-coldroom.compressor"] = want and not defrost
        c.compressor = self.on("bas-coldroom.compressor") and self.bus("ep1")
        heat = c.ua * (20.0 - c.temp_c) + (
            c.ua_door * (20.0 - c.temp_c) if door else 0.0
        )
        heat += 1000.0 if self.on("bas-coldroom.defrost") else 0.0
        heat -= c.q_ref_w if c.compressor else 0.0
        c.temp_c += heat / c.capacity_j_k * dt
        _ = t_out  # the cold room sits inside the conditioned building

    def _cellar(self, dt: float, t_out: float) -> None:
        cel = self.cel
        s = self.settings
        g = self.input("cellar/co2.vent_gps", 0.0) / 1000.0 / RHO_CO2_SITE  # m3/s
        sensed = cel.co2_ppm * STRATIFICATION
        tiers = tuple(
            sensed >= limit or (was and sensed >= limit * (1.0 - CO2_TIER_DEADBAND))
            for limit, was in zip(CO2_TIERS, cel.tiers, strict=True)
        )
        cel.tiers = (tiers[0], tiers[1], tiers[2])
        cel.purge = tiers[0] or (cel.purge and sensed >= 3500.0)
        self.local["bas-vent.purge_fan_1"] = cel.purge
        self.local["bas-vent.purge_fan_2"] = cel.purge
        fans = sum(
            1
            for k in ("bas-vent.purge_fan_1", "bas-vent.purge_fan_2")
            if self.on(k) and self.bus("ep1")
        )
        q_vent = s.cellar_infiltration_m3s + fans * s.purge_fan_m3s
        cel.co2_ppm += (
            (g * 1e6 + q_vent * (OUTDOOR_CO2_PPM - cel.co2_ppm))
            / s.cellar_volume_m3
            * dt
        )
        target = 0.25 * t_out + 0.75 * 18.5 if fans == 0 else 0.6 * t_out + 0.4 * 18.5
        cel.temp_c += (target - cel.temp_c) * dt / (6 * 3600.0)
        hood_kw = self.input("brewhouse/hood.heat_kw", 0.0)
        self.local["bas-vent.exhaust_pct"] = 100.0 if hood_kw > 10.0 else 20.0
        self.local["bas-vent.mua_enable"] = self.num("bas-vent.exhaust_pct") > 30.0
        cel.mua_c = max(t_out, 18.3) if self.on("bas-vent.mua_enable") else t_out

    # -- publications ------------------------------------------------------------------

    def sensor_ppm(self, i: int) -> float:
        """Low-mounted CO2 sensor ``i`` (1-4)."""
        return self.cel.co2_ppm * STRATIFICATION * SENSOR_OFFSETS[i - 1]

    def point_value(self, pub: Publication) -> Value:
        """A BACnet object value."""
        tag = f"{pub.device}.{pub.point}"
        if tag in self.local:
            return self.cmd(tag)
        point = pub.point or ""
        if point.startswith("co2_") and point.endswith("_ppm"):
            return self.sensor_ppm(int(point.split("_")[1]))
        if point.startswith("co2_tier_"):
            return self.cel.tiers[int(point[-1]) - 1]
        ep1 = self.bus("ep1")
        values: dict[str, Value] = {
            "bas-rtu-taproom.zone_temp_f": c_to_f(self.tap.t_air_c),
            "bas-rtu-taproom.zone_co2_ppm": self.tap.co2_ppm,
            "bas-rtu-taproom.supply_air_f": c_to_f(self.tap.supply_air_c),
            "bas-rtu-taproom.power_kw": self.tap.power_kw,
            "bas-coldroom.room_temp_f": c_to_f(self.cold.temp_c),
            "bas-coldroom.door_open_min": self.cold.door_open_s / 60.0,
            "bas-coldroom.door": self.t < self.cold.door_until,
            "bas-vent.mua_discharge_f": c_to_f(self.cel.mua_c),
            "bas-vent.purge_fan_1_status": self.on("bas-vent.purge_fan_1") and ep1,
            "bas-vent.purge_fan_2_status": self.on("bas-vent.purge_fan_2") and ep1,
        }
        if tag not in values:
            raise KeyError(f"no model value for {pub.key}")
        return values[tag]

    def output_value(self, name: str) -> Value:
        """Outputs to the cellar and the power federate."""
        match name:
            case "cellar.temp_f":
                return c_to_f(self.cel.temp_c)
            case "cellar.co2_ppm":
                return self.cel.co2_ppm
            case "vent.co2_relay_ok":
                # The gas-detection relay to rio-cellar is de-energize to trip: it
                # drops at tier 2 (15,000 ppm) and when EP-1, which feeds the
                # detection panel, is dead (DESIGN.md §6.6 #6).
                return 1.0 if (not self.cel.tiers[1]) and self.bus("ep1") else 0.0
            case "load.rtu.kw":
                return self.tap.power_kw
            case "load.coldroom.kw":
                return (2.2 if self.cold.compressor else 0.0) + (
                    1.0 if self.on("bas-coldroom.defrost") else 0.3
                )
            case "load.fans.kw":
                ex = self.num("bas-vent.exhaust_pct") / 100
                purge = sum(
                    0.75
                    for k in ("bas-vent.purge_fan_1", "bas-vent.purge_fan_2")
                    if self.on(k)
                )
                return (
                    2.2 * ex**3
                    + purge
                    + (1.5 if self.on("bas-vent.mua_enable") else 0.0)
                )
            case "load.lighting.kw":
                return 16.0 * (0.25 + 0.75 * (1.0 if self.occupied else 0.0))
            case "load.otracks.kw":
                return 2.0
        raise KeyError(f"no output {name}")

    def truth(self) -> dict[str, Any]:
        """Published values plus the true (unstratified) cellar CO2."""
        doc = super().truth()
        doc["building"] = {
            "cellar_co2_ppm_mixed": self.cel.co2_ppm,
            "taproom_people": self.people,
            "overrides": sorted(self.commands),
        }
        return doc
