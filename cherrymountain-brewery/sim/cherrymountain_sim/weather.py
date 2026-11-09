"""Weather federate on sim-ctl, with the scenario event API (DESIGN.md §8.3, §9.5).

**Weather.** The federate reads an EnergyPlus TMY3 EPW file (the Golden or
Buckley stations named in DESIGN.md §8.3) when one is configured and present,
and interpolates it hourly. The EPW station pressure is corrected from the
station elevation to the site's 1,585 m with the barometric formula. Without
an EPW file (they are not committed; DESIGN.md §20 asks to confirm their
redistribution terms) it uses a synthetic Denver model: a seasonal and diurnal
temperature curve, clear-sky irradiance from the solar elevation at 39.75 °N,
and the standard-atmosphere site pressure.

The groundwater (city water) temperature follows a seasonal curve, 55 °F mean
and ±10 °F, warmest in late September; it sets the CLT temperature and so the
knockout heat exchanger's first stage.

**Events.** The federate serves :mod:`cherrymountain_sim.simctl` and forwards
each accepted event to its federate's endpoint. ``weather-step`` is applied
here: the outdoor temperature steps by ``delta_f`` for ``duration`` seconds
(0 holds it).
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
import logging
import math
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from cherrymountain_sim.federate import (
    Federate,
    FederateConfig,
    ScenarioEvent,
    SimClock,
    Value,
)
from cherrymountain_sim.simctl import EventApi, Kpis

LOG: logging.Logger = logging.getLogger("cherrymountain_sim.weather")
G: float = 9.80665
M_AIR: float = 0.0289644
R_GAS: float = 8.314462
SITE_LAT_DEG: float = 39.75
SITE_LON_DEG: float = -105.2


class WeatherSettings(BaseModel):
    """Weather federate settings (helics.json ``sim``)."""

    epw: str | None = None
    station_elevation_m: float = 1829.0
    site_elevation_m: float = 1585.0
    event_api: bool = True
    event_api_host: str = "127.0.0.1"
    event_api_port: int = 8080


class WeatherSample(BaseModel):
    """Weather at one moment."""

    temp_c: float
    rh_pct: float
    pressure_kpa: float
    ghi_wm2: float


def barometric_correction(
    p_station_kpa: float, station_m: float, site_m: float, temp_c: float
) -> float:
    """Station pressure moved to the site elevation (isothermal layer)."""
    t_k = temp_c + 273.15
    return p_station_kpa * math.exp(-G * M_AIR * (site_m - station_m) / (R_GAS * t_k))


def standard_pressure_kpa(elevation_m: float) -> float:
    """ICAO standard-atmosphere pressure at an elevation."""
    return 101.325 * (1.0 - 2.25577e-5 * elevation_m) ** 5.25588


def solar_elevation_deg(when: dt.datetime) -> float:
    """Approximate solar elevation at the site (no equation of time)."""
    doy = when.timetuple().tm_yday
    utc = when.astimezone(dt.UTC)
    solar_h = utc.hour + utc.minute / 60 + SITE_LON_DEG / 15.0
    decl = math.radians(23.44) * math.sin(2 * math.pi * (284 + doy) / 365)
    hour_angle = math.radians(15.0 * (solar_h - 12.0))
    lat = math.radians(SITE_LAT_DEG)
    s = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(
        hour_angle
    )
    return math.degrees(math.asin(max(min(s, 1.0), -1.0)))


def synthetic(when: dt.datetime, site_m: float = 1585.0) -> WeatherSample:
    """Synthetic Denver weather: seasonal plus diurnal temperature, clear sky."""
    doy = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60
    mean_f = 50.5 + 23.0 * math.sin(2 * math.pi * (doy - 105) / 365)
    swing_f = 13.0 + 2.0 * math.sin(2 * math.pi * (doy - 105) / 365)
    temp_f = mean_f + swing_f * math.cos(2 * math.pi * (hour - 15.0) / 24.0)
    elev = solar_elevation_deg(when)
    s = math.sin(math.radians(elev))
    ghi = 1098.0 * s * math.exp(-0.057 / s) * 0.9 if s > 0.01 else 0.0
    rh = 55.0 - 25.0 * math.cos(2 * math.pi * (hour - 15.0) / 24.0)
    return WeatherSample(
        temp_c=(temp_f - 32) / 1.8,
        rh_pct=rh,
        pressure_kpa=standard_pressure_kpa(site_m),
        ghi_wm2=ghi,
    )


def groundwater_f(when: dt.datetime) -> float:
    """Seasonal city-water temperature (warmest in late September)."""
    doy = when.timetuple().tm_yday
    return 55.0 + 10.0 * math.cos(2 * math.pi * (doy - 265) / 365)


class Epw:
    """Hourly records of an EPW file, keyed by hour of the year."""

    hours: list[float]
    samples: list[WeatherSample]

    def __init__(self, path: Path, station_m: float, site_m: float) -> None:
        self.hours = []
        self.samples = []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[8:]:
            f = line.split(",")
            if len(f) < 16:
                continue
            month, day, hour = int(f[1]), int(f[2]), int(f[3])
            temp = float(f[6])
            start = dt.date(2001, month, day).timetuple().tm_yday - 1
            self.hours.append(start * 24 + hour - 0.5)  # EPW hours end at h
            self.samples.append(
                WeatherSample(
                    temp_c=temp,
                    rh_pct=float(f[8]),
                    pressure_kpa=barometric_correction(
                        float(f[9]) / 1000.0, station_m, site_m, temp
                    ),
                    ghi_wm2=float(f[13]),
                )
            )
        if not self.samples:
            raise ValueError(f"{path}: no weather records")

    def at(self, when: dt.datetime) -> WeatherSample:
        """Linear interpolation between the bracketing hours."""
        tt = when.timetuple()
        h = (tt.tm_yday - 1) * 24 + when.hour + when.minute / 60
        i = bisect.bisect_right(self.hours, h) % len(self.hours)
        a, b = self.samples[i - 1], self.samples[i]
        span = (self.hours[i] - self.hours[i - 1]) % 8760 or 1.0
        w = ((h - self.hours[i - 1]) % 8760) / span
        mix: dict[str, float] = {
            k: getattr(a, k) * (1 - w) + getattr(b, k) * w
            for k in WeatherSample.model_fields
        }
        return WeatherSample.model_validate(mix)


class WeatherFederate(Federate):
    """Weather player, clock, and event API (sim-ctl)."""

    name: ClassVar[str] = "weather"
    settings: WeatherSettings
    epw: Epw | None
    sample: WeatherSample
    step_f: float
    step_until: float | None
    last_event: str
    api: EventApi | None

    def __init__(self, cfg: FederateConfig, clock: SimClock | None = None) -> None:
        super().__init__(cfg, clock)
        self.settings = WeatherSettings.model_validate(cfg.sim)
        s = self.settings
        self.epw = None
        if s.epw and Path(s.epw).is_file():
            self.epw = Epw(Path(s.epw), s.station_elevation_m, s.site_elevation_m)
        elif s.epw:
            LOG.warning("EPW %s not found; using the synthetic Denver weather", s.epw)
        self.sample = self._weather()
        self.step_f = 0.0
        self.step_until = None
        self.last_event = ""
        self.api = None

    def start_api(self, variant: str = "") -> int:
        """Serve the event API; returns the bound port."""
        self.api = EventApi(variant)
        return self.api.serve(
            self.settings.event_api_host, self.settings.event_api_port
        )

    def _weather(self) -> WeatherSample:
        if self.epw is not None:
            return self.epw.at(self.now)
        return synthetic(self.now, self.settings.site_elevation_m)

    def on_event(self, event: ScenarioEvent) -> None:
        """``weather-step``: step the outdoor temperature."""
        super().on_event(event)
        if event.type == "weather-step":
            self.step_f = float(event.params.get("delta_f", 0.0))
            duration = float(event.params.get("duration", 0.0))
            self.step_until = self.t + duration if duration > 0 else None

    def advance(self, wall_dt_s: float) -> None:
        """Refresh the weather each minute; forward events; refresh KPIs."""
        if int(self.t) % 60 == 0:
            self.sample = self._weather()
        if self.step_until is not None and self.t >= self.step_until:
            self.step_f, self.step_until = 0.0, None
        if self.api is None:
            return
        for ev in self.api.drain():
            scenario = ScenarioEvent(type=ev.event, params=ev.params, id=ev.id)
            self.last_event = json.dumps(ev.model_dump())
            if ev.target == self.name:
                self.events.append(scenario)
                self.on_event(scenario)
            elif ev.target in self.peers:
                payload = json.dumps({"event": scenario.model_dump()})
                self.outbox.append((self.peers[ev.target], payload))
            else:
                LOG.warning("event %s: federate %s is not running", ev.event, ev.target)
        self.api.update(self.now.isoformat(), self.kpis())

    def kpis(self) -> Kpis:
        """The KPI snapshot from the subscriptions."""
        return Kpis(
            sim_time=self.now.isoformat(),
            batches_completed=int(self.input("brewhouse/batch.completed")),
            batches_in_progress=int(self.input("brewhouse/batch.active")),
            bottles_packaged=int(self.input("packaging/bottles.total")),
            kegs_packaged=int(self.input("packaging/kegs.total")),
            energy_kwh=round(self.input("power/energy.kwh"), 1),
            demand_kw=round(self.input("power/demand.kw"), 1),
            peak_demand_kw=round(self.input("power/demand.peak_kw"), 1),
        )

    @property
    def ambient_f(self) -> float:
        """Outdoor temperature with any scenario step, °F."""
        return self.sample.temp_c * 1.8 + 32.0 + self.step_f

    def output_value(self, name: str) -> Value:
        """Weather outputs."""
        match name:
            case "ambient.temp_f":
                return self.ambient_f
            case "rh.pct":
                return self.sample.rh_pct
            case "ghi.wm2":
                return self.sample.ghi_wm2
            case "pressure.kpa":
                return self.sample.pressure_kpa
            case "groundwater.temp_f":
                return groundwater_f(self.now)
            case "clock.epoch_s":
                return self.now.timestamp()
            case "event":
                return self.last_event
        raise KeyError(f"no output {name}")

    def truth(self) -> dict[str, Any]:
        """Published values plus the event history."""
        doc = super().truth()
        doc["events"] = [e.model_dump() for e in self.events[-20:]]
        return doc
