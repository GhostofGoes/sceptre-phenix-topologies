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
- **CO2 beyond the cellar** (Denver IFC 5307: every point of use and the
  supply tank area; DESIGN.md §6.7). Three more well-mixed areas with one
  low sensor each: the bulk tank and vaporizer room, the filler area (the
  filler vents half the CO2 it draws for pre-evacuation and counterpressure
  into the room), and the cold room with the draft system (a small background
  leak, and air exchange while the door is open). A ``co2-leak`` event opens a
  leak on the supply piping in one area; its flow follows the supply header
  pressure, so the CO2 supply shutoff stops it.

Each controller's local logic runs here, the way the BAS controllers would run
it in their ot-sim logic module: RTU staging and economizer, the cold-room
thermostat and defrost, the CO2 tiers (5,000 ppm starts the purge fans,
15,000 inhibits carbonation, 30,000 evacuates), the area exhaust and the
horns and strobes, the two de-energize-to-trip relays (tier 2 in the cellar
to rio-cellar; tier 3 anywhere, or loss of detection, to the CO2 supply
shutoff on rio-utilities), and the kettle hood exhaust. A ``gas-detection``
event fails the detection panel (both relays drop) or restores it.
A BACnet write to a point overrides the local logic for that point, as a
priority-array write above the controller's own priority would, and a null
write hands the point back: the ot-sim BACnet module sends one when the last
priority of the point is relinquished.
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
    ScenarioEvent,
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
#: Sensed CO2 below which a purge or area exhaust stops, ppm.
PURGE_STOP_PPM: float = 3500.0
#: co2-leak and co2-release rates by name, g/s.
LEAK_RATES: dict[str, float] = {"low": 2.0, "medium": 10.0, "high": 40.0}
#: Supply header pressure the leak rates are quoted at, psig.
LEAK_REF_PSIG: float = 60.0
#: CO2 density at 0 °C and 1 atm, g per standard liter.
CO2_G_PER_SL: float = 1.977
#: Fraction of the filler's CO2 draw vented into the filler area.
FILLER_VENT_FRACTION: float = 0.5


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


class Co2Area(BaseModel):
    """A well-mixed area with one low CO2 sensor (bulk tank, filler, draft)."""

    sensor: int
    volume_m3: float
    base_m3s: float
    exhaust_m3s: float
    background_gps: float = 0.0
    co2_ppm: float = 450.0
    tiers: tuple[bool, bool, bool] = (False, False, False)
    leak_gps: float = 0.0
    leak_until: float = -1.0


def default_areas() -> dict[str, Co2Area]:
    """The three CO2 areas beyond the cellar (DESIGN.md §6.7, §8.1).

    The tank room (about 100 m³) has louvers and an exhaust fan sized near
    the 1 cfm/ft² of the Denver policy; the filler area is part of the
    packaging hall's general ventilation; the cold room exchanges little air
    except while its door is open, and its draft system leaks a trickle."""
    return {
        "tank": Co2Area(sensor=5, volume_m3=100.0, base_m3s=0.05, exhaust_m3s=0.25),
        "filler": Co2Area(sensor=6, volume_m3=1200.0, base_m3s=0.8, exhaust_m3s=1.0),
        "draft": Co2Area(
            sensor=7, volume_m3=60.0, base_m3s=0.01, exhaust_m3s=0.15, background_gps=0.01
        ),
    }


class Cellar(BaseModel):
    """Cellar room and brewhouse ventilation."""

    co2_ppm: float = 800.0
    temp_c: float = 19.0
    tiers: tuple[bool, bool, bool] = (False, False, False)
    purge: bool = False
    mua_c: float = 20.0
    areas: dict[str, Co2Area] = {}
    area_exhaust: bool = False
    #: The detection panel (sensors, controller, relays) is healthy.
    detection_ok: bool = True


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
        self.cel = Cellar(areas=default_areas())
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
            "bas-vent.horn_strobe": False,
            "bas-vent.area_exhaust": False,
        }

    def cmd(self, tag: str) -> Value:
        """Per-point authority: a BACnet write overrides the local logic until
        a null write relinquishes the point."""
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

    def on_event(self, event: ScenarioEvent) -> None:
        """``co2-leak`` opens a leak on the CO2 supply piping in one area
        (``zone``: tank, filler, or draft; ``rate``: low, medium, high, or g/s;
        ``duration`` s, default 600); ``gas-detection`` fails (``action:
        fail``) or restores the detection panel."""
        super().on_event(event)
        if event.type == "co2-leak":
            area = self.cel.areas[str(event.params.get("zone", "tank"))]
            rate = event.params.get("rate", "high")
            gps = LEAK_RATES.get(str(rate))
            gps = float(rate) if gps is None else gps
            seconds = float(event.params.get("duration", 600.0))
            area.leak_gps, area.leak_until = gps, self.t + seconds
        elif event.type == "gas-detection":
            self.cel.detection_ok = event.params.get("action", "fail") == "restore"

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
            self._areas(h)

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
        cel.purge = tiers[0] or (cel.purge and sensed >= PURGE_STOP_PPM)
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

    def _areas(self, dt: float) -> None:
        """The bulk tank, filler, and draft areas: CO2 balance, tiers, the area
        exhaust (on at tier 1 in any area, off below 3,500 ppm everywhere),
        and the horns and strobes (tier 3 in any area, the cellar included)."""
        cel = self.cel
        ep1 = self.bus("ep1")
        supply = min(max(self.input("utilities/co2.supply_psig", LEAK_REF_PSIG), 0.0), 100.0)
        filler_gps = (
            self.input("packaging/co2.draw_slpm", 0.0) / 60.0 * CO2_G_PER_SL * FILLER_VENT_FRACTION
        )
        door = self.t < self.cold.door_until
        exhaust = self.on("bas-vent.area_exhaust") and ep1
        for name, a in cel.areas.items():
            if self.t >= a.leak_until:
                a.leak_gps = 0.0
            gps = a.background_gps + a.leak_gps * supply / LEAK_REF_PSIG
            if name == "filler":
                gps += filler_gps
            q = a.base_m3s + (a.exhaust_m3s if exhaust else 0.0)
            if name == "draft" and door:
                q += 0.3
            g = gps / 1000.0 / RHO_CO2_SITE
            a.co2_ppm += (g * 1e6 + q * (OUTDOOR_CO2_PPM - a.co2_ppm)) / a.volume_m3 * dt
            sensed = a.co2_ppm * STRATIFICATION
            tiers = tuple(
                sensed >= limit or (was and sensed >= limit * (1.0 - CO2_TIER_DEADBAND))
                for limit, was in zip(CO2_TIERS, a.tiers, strict=True)
            )
            a.tiers = (tiers[0], tiers[1], tiers[2])
        tier1 = any(a.tiers[0] for a in cel.areas.values())
        cel.area_exhaust = tier1 or (
            cel.area_exhaust
            and any(a.co2_ppm * STRATIFICATION >= PURGE_STOP_PPM for a in cel.areas.values())
        )
        self.local["bas-vent.area_exhaust"] = cel.area_exhaust
        self.local["bas-vent.horn_strobe"] = self.tier3_anywhere and cel.detection_ok

    @property
    def tier3_anywhere(self) -> bool:
        """Tier 3 (30,000 ppm) in the cellar or in any other area."""
        return self.cel.tiers[2] or any(a.tiers[2] for a in self.cel.areas.values())

    @property
    def shutoff_ok(self) -> bool:
        """The CO2 supply shutoff relay to rio-utilities (de-energize to trip):
        energized only while no area is at tier 3, EP-1 powers the detection
        panel, and the panel is healthy (Denver IFC 5307: fail closed on loss
        of power or detection)."""
        return not self.tier3_anywhere and self.bus("ep1") and self.cel.detection_ok

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
            n = int(point.split("_")[1])
            area = next((a for a in self.cel.areas.values() if a.sensor == n), None)
            if area is not None:
                return area.co2_ppm * STRATIFICATION
            return self.sensor_ppm(n)
        if point.startswith("co2_tier_"):
            return self.cel.tiers[int(point[-1]) - 1]
        if point == "co2_area_tier_1":
            return any(a.tiers[0] for a in self.cel.areas.values())
        if point == "co2_shutoff_ok":
            return self.shutoff_ok
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
                return (
                    1.0
                    if (not self.cel.tiers[1]) and self.bus("ep1") and self.cel.detection_ok
                    else 0.0
                )
            case "vent.co2_shutoff_ok":
                return 1.0 if self.shutoff_ok else 0.0
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
                    for k in ("bas-vent.purge_fan_1", "bas-vent.purge_fan_2", "bas-vent.area_exhaust")
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
            "co2_areas": {
                name: {
                    "co2_ppm_mixed": round(a.co2_ppm, 1),
                    "tiers": list(a.tiers),
                    "leak_gps": a.leak_gps,
                }
                for name, a in self.cel.areas.items()
            },
            "co2_detection_ok": self.cel.detection_ok,
            "co2_shutoff_relay_ok": self.shutoff_ok,
            "taproom_people": self.people,
            "overrides": sorted(self.commands),
        }
        return doc
