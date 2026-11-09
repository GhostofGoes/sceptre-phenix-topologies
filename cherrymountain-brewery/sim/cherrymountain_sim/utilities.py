"""Utilities federate: glycol, steam, air, CO2, RO, wastewater, CIP (DESIGN.md §9.3).

Lumped first-order models with tank volumes, mass and energy balances, and a
pH/conductivity mixing model, stepped at 1 s in wall time.

- **Glycol.** Reservoir energy balance m·c_p·dT/dt = ΣQ_jackets + Q_HX2 +
  Q_pump + Q_ambient − Q_chiller. The 15-ton chiller has two 26.5 kW stages;
  capacity falls and the COP drops as the ambient warms. The OEM controller's
  staging (stage 1 at SP + 1 °F, stage 2 at SP + 3 °F, with hysteresis) runs
  here, because the chiller's points expose the stage status, not stage
  commands. A 35 % propylene glycol c_p of 3.8 kJ/(kg·K) is used (CoolProp
  ``INCOMP::MPG[0.35]`` gives about 3.75 at 28 °F; CoolProp is optional).
- **Steam.** A 200 kW electric boiler modulating on header pressure, with a
  storage capacity of 400 kJ/psi; the hardwired low-water cutoff trips it.
- **Air.** A 15 hp load/unload compressor (100/120 psig) on a 240 gal receiver.
- **CO2.** A 6,000 lb bulk tank at about 300 psig with a 60 psig supply
  regulator.
- **Wastewater.** A 2,000 gal neutralization tank. Net base equivalents set
  the pH through a buffered titration curve; caustic and acid dosing pumps and
  a discharge valve (permitted at pH 6.0-9.0). The tank keeps the record a
  POTW pretreatment permit asks for (DESIGN.md §18A): the pH range seen at
  the discharge point, the volume discharged today, hourly pH samples, and
  the time the valve was commanded while the pH was outside the permit band.
- **CIP.** The supply solution's conductivity follows the dosed chemical; the
  return lags it; return flow needs both pumps and a swing-panel route. Rinse
  water returns to drain, carrying the residual chemical to wastewater. Each
  circuit run is logged as a verification record (circuit, chemical, peak
  temperature and return conductivity, proven-flow time; DESIGN.md §5.3).

As in the brewhouse, a built-in operator (``autopilot``) keeps the utilities
running until the PLC writes.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from cherrymountain_sim.federate import (
    Federate,
    FederateConfig,
    Publication,
    ScenarioEvent,
    SimClock,
    Value,
)

GLYCOL_CP: float = 3800.0
WATER_CP: float = 4180.0
LPM_PER_GPM: float = 3.785411784
#: CO2 density at 0 °C and 1 atm, kg per standard liter.
CO2_KG_PER_SL: float = 1.977e-3
#: Conductivity per mol/L of NaOH (about 75 mS/cm at 1.5 %), mS/cm.
MS_PER_MOLAR: float = 213.0
CIP_ROUTES: tuple[str, ...] = (
    "route_bh",
    "route_fv101",
    "route_fv102",
    "route_fv103",
    "route_fv104",
    "route_bbt201",
    "route_bbt202",
)
#: CIP verification (DESIGN.md §5.3, §6.3): a caustic wash counts as verified
#: when the supply reached this temperature and the return this conductivity
#: (about 1.5 % NaOH) with flow proven for at least this long.
CIP_MIN_CAUSTIC_F: float = 160.0
CIP_MIN_RETURN_MS: float = 40.0
CIP_MIN_CONTACT_S: float = 600.0
#: Records kept in the federate (ground truth carries them on).
CIP_RECORDS: int = 20
WWN_SAMPLES: int = 48

#: Command defaults: every writable point, as the autopilot leaves them.
AUTO_COMMANDS: dict[str, Value] = {
    "chiller.enable": True,
    "chiller.supply_sp_f": 28.0,
    "glycol.pump_run": True,
    "glycol.pump_speed_pct": 70.0,
    "boiler.enable": True,
    "boiler.pressure_sp_psig": 15.0,
    "aircomp.enable": True,
    "co2.main_valve": True,
    "ro.run": False,
    "wwn.caustic_pump": False,
    "wwn.acid_pump": False,
    "wwn.discharge_valve": False,
    "cip.supply_pump": False,
    "cip.return_pump": False,
    "cip.caustic_dose": False,
    "cip.acid_dose": False,
    "cip.steam_valve_pct": 0.0,
}
#: What an unwritten command reads once the PLC has control.
PLC_DEFAULTS: dict[str, Value] = {
    k: (0.0 if isinstance(v, float) else False) for k, v in AUTO_COMMANDS.items()
}


class UtilitiesSettings(BaseModel):
    """Utilities sim settings (helics.json ``sim``)."""

    autopilot: Literal["auto", "on", "off"] = "auto"
    seed: int = 0


class Glycol(BaseModel):
    """Glycol reservoir and chiller."""

    mass_kg: float = 1800.0
    temp_c: float = -2.2
    return_c: float = 0.0
    stage1: bool = False
    stage2: bool = False
    failed: set[int] = set()
    chiller_kw: float = 0.0
    pump_psig: float = 0.0


class Boiler(BaseModel):
    """Electric steam boiler."""

    psig: float = 15.0
    power_kw: float = 0.0
    low_water: bool = False


class Air(BaseModel):
    """Air compressor and receiver."""

    psig: float = 110.0
    loaded: bool = False


class Co2(BaseModel):
    """Bulk CO2 tank."""

    kg: float = 1630.0
    capacity_kg: float = 2722.0
    supply_psig: float = 60.0


class PhSample(BaseModel):
    """One hourly pH sample of the neutralization tank (permit self-monitoring)."""

    time: str
    ph: float
    level_pct: float
    discharging: bool


class Wastewater(BaseModel):
    """Neutralization tank: volume and net base equivalents (mol), and the
    discharge record a pretreatment permit asks for."""

    liters: float = 2500.0
    capacity_l: float = 7570.0
    base_mol: float = 0.0
    discharged_l: float = 0.0
    #: The permit band the discharge valve is permitted in (DESIGN.md §6.3).
    permit_low: float = 6.0
    permit_high: float = 9.0
    day: str = ""
    discharged_l_day: float = 0.0
    discharge_ph_min: float = 14.0
    discharge_ph_max: float = 0.0
    #: Seconds the valve was commanded open while the pH was out of band (the
    #: permissive held it shut), and how many times that began.
    inhibited_s: float = 0.0
    inhibit_events: int = 0
    inhibited: bool = False
    samples: list[PhSample] = Field(default_factory=list)


class CipRecord(BaseModel):
    """One CIP circuit run: the verification record of DESIGN.md §5.3."""

    circuit: str
    started: str
    ended: str = ""
    duration_s: float = 0.0
    chemical: Literal["caustic", "acid", "none"] = "none"
    max_supply_f: float = 0.0
    max_return_ms: float = 0.0
    flow_proven_s: float = 0.0
    verified: bool = False


class Cip(BaseModel):
    """CIP skid."""

    supply_c: float = 20.0
    supply_ms: float = 0.5
    return_ms: float = 0.5
    chem: int = 0  # +1 caustic, -1 acid: the chemical last dosed
    caustic_pct: float = 80.0
    acid_pct: float = 80.0
    return_lpm: float = 0.0
    routes: dict[str, bool] = {}
    run: CipRecord | None = None
    records: list[CipRecord] = Field(default_factory=list)


def ph_of(base_molar: float, buffer_molar: float = 0.004) -> float:
    """pH of a buffered water with ``base_molar`` net base (negative: acid).

    A titration-shaped curve: 7 at neutral, flattened by the buffer, and
    approaching pH 12.5 (0.03 M NaOH) or 1.5 for strong excess.
    """
    return 7.0 + 5.5 * math.tanh(base_molar / (buffer_molar + 0.03 * abs(base_molar)))


class UtilitiesFederate(Federate):
    """The utilities federate."""

    name: ClassVar[str] = "utilities"
    settings: UtilitiesSettings
    auto: dict[str, Value]
    gly: Glycol
    blr: Boiler
    air: Air
    co2: Co2
    wwn: Wastewater
    cip: Cip
    ro_running: bool

    def __init__(self, cfg: FederateConfig, clock: SimClock | None = None) -> None:
        super().__init__(cfg, clock)
        self.settings = UtilitiesSettings.model_validate(cfg.sim)
        self.auto = dict(AUTO_COMMANDS)
        self.gly = Glycol()
        self.blr = Boiler()
        self.air = Air()
        self.co2 = Co2()
        self.wwn = Wastewater()
        self.cip = Cip(routes=dict.fromkeys(CIP_ROUTES, False))
        self.ro_running = False

    @property
    def autopilot(self) -> bool:
        """True while the built-in operator drives the commands."""
        mode = self.settings.autopilot
        return mode == "on" or (mode == "auto" and not self.plc_driving)

    def cmd(self, tag: str) -> Value:
        """The command in force."""
        if self.autopilot:
            return self.auto[tag]
        return self.commands.get(tag, PLC_DEFAULTS[tag])

    def on(self, tag: str) -> bool:
        """A binary command."""
        return bool(self.cmd(tag))

    def num(self, tag: str) -> float:
        """A numeric command."""
        return float(self.cmd(tag))

    def bus(self, name: str) -> bool:
        """Whether a power bus is energized."""
        return self.input(f"power/bus.{name}.energized", 1.0) >= 0.5

    def on_event(self, event: ScenarioEvent) -> None:
        """``chiller-fault`` fails or clears a compressor stage; ``cip-panel``
        sets the swing panel (``circuit``: bh, fv101, ..., or none)."""
        super().on_event(event)
        if event.type == "chiller-fault":
            stage = int(event.params.get("compressor", 1))  # 1 or 2 (simctl checks)
            if event.params.get("action", "fail") == "clear":
                self.gly.failed.discard(stage)
            else:
                self.gly.failed.add(stage)
        elif event.type == "cip-panel":
            circuit = str(event.params.get("circuit", "none")).lower().replace("-", "")
            for route in CIP_ROUTES:
                self.cip.routes[route] = route == f"route_{circuit}"
        elif event.type == "low-water":
            self.blr.low_water = event.params.get("action", "trip") == "trip"

    # -- dynamics ----------------------------------------------------------------------

    def advance(self, wall_dt_s: float) -> None:
        """Advance every utility ``wall_dt_s`` seconds."""
        dt = wall_dt_s
        amb_f = self.input("weather/ambient.temp_f", 75.0)
        if self.autopilot:
            self._operator()
        self._glycol(dt, amb_f)
        self._steam(dt)
        self._air(dt)
        self._co2(dt)
        self.ro_running = self.on("ro.run") and self.bus("msb")
        self._cip(dt, amb_f)
        self._wastewater(dt)

    def _operator(self) -> None:
        """Built-in wastewater neutralization (the rest holds its defaults)."""
        w = self.wwn
        ph = ph_of(w.base_mol / max(w.liters, 1.0))
        a = self.auto
        a["wwn.caustic_pump"] = ph < 6.5
        a["wwn.acid_pump"] = ph > 7.5
        a["wwn.discharge_valve"] = (
            w.permit_low <= ph <= w.permit_high and w.liters > 0.3 * w.capacity_l
        )

    def _glycol(self, dt: float, amb_f: float) -> None:
        g = self.gly
        sp = self.num("chiller.supply_sp_f")
        t_f = g.temp_c * 1.8 + 32
        powered = self.bus("ep1")
        enabled = self.on("chiller.enable") and powered
        g.stage1 = (
            enabled
            and 1 not in g.failed
            and (t_f > sp + 1.0 or (g.stage1 and t_f > sp - 0.5))
        )
        g.stage2 = (
            enabled
            and 2 not in g.failed
            and (t_f > sp + 3.0 or (g.stage2 and t_f > sp + 1.0))
        )
        cap_kw = 26.5 * max(1.0 - 0.008 * (amb_f - 95.0), 0.5)
        cop = min(max(3.2 + 0.04 * (95.0 - amb_f), 2.0), 5.0)
        q_chill = cap_kw * (int(g.stage1) + int(g.stage2))
        g.chiller_kw = q_chill / cop + (1.5 if q_chill > 0 else 0.0)
        running = self.on("glycol.pump_run") and powered
        speed = self.num("glycol.pump_speed_pct") / 100.0 if running else 0.0
        g.pump_psig = 45.0 * speed**2
        q_in = (
            self.input("cellar/glycol.load_kw", 5.0) * min(speed / 0.5, 1.0)
            + self.input("brewhouse/glycol.load_kw", 0.0)
            + 1.5 * speed
            + 0.1 * ((amb_f - 32) / 1.8 - g.temp_c)
        )
        g.temp_c += (q_in - q_chill) * 1e3 / (g.mass_kg * GLYCOL_CP) * dt
        flow_kg_s = max(3.0 * speed, 0.1)
        g.return_c = g.temp_c + q_in * 1e3 / (flow_kg_s * GLYCOL_CP)

    def _steam(self, dt: float) -> None:
        b = self.blr
        demand = (
            self.input("brewhouse/steam.demand_kw", 0.0)
            + self.num("cip.steam_valve_pct") / 100 * 60.0
        )
        firing = self.on("boiler.enable") and self.bus("boiler") and not b.low_water
        sp = self.num("boiler.pressure_sp_psig")
        q = min(max(200.0 * (sp + 1.0 - b.psig) / 2.0, 0.0), 200.0) if firing else 0.0
        b.power_kw = q / 0.98
        b.psig = min(max(b.psig + (q - demand - 2.0) / 400.0 * dt, 0.0), 20.0)

    def _air(self, dt: float) -> None:
        a = self.air
        enabled = self.on("aircomp.enable") and self.bus("msb")
        a.loaded = enabled and (a.psig < 100.0 or (a.loaded and a.psig < 120.0))
        supply = 60.0 if a.loaded else 0.0
        demand = self.input("packaging/air.demand_scfm", 10.0) + 5.0
        a.psig = min(max(a.psig + (supply - demand) / 60.0 * 0.5 * dt, 0.0), 125.0)

    def _co2(self, dt: float) -> None:
        c = self.co2
        slpm = self.input("cellar/co2.draw_slpm", 0.0) + self.input(
            "packaging/co2.draw_slpm", 0.0
        )
        if self.on("co2.main_valve") and c.kg > 0.01 * c.capacity_kg:
            c.kg = max(c.kg - slpm / 60.0 * CO2_KG_PER_SL * dt, 0.0)
            c.supply_psig = max(60.0 - 0.02 * slpm, 0.0)
        else:
            c.supply_psig = max(c.supply_psig - 2.0 * dt, 0.0)

    def _cip(self, dt: float, amb_f: float) -> None:
        c = self.cip
        if self.on("cip.caustic_dose"):
            c.chem, target = 1, 80.0
            c.caustic_pct = max(c.caustic_pct - 0.0005 * dt, 0.0)
        elif self.on("cip.acid_dose"):
            c.chem, target = -1, 20.0
            c.acid_pct = max(c.acid_pct - 0.0005 * dt, 0.0)
        else:
            target = 0.5
        c.supply_ms += (target - c.supply_ms) * min(dt / 90.0, 1.0)
        c.return_ms += (c.supply_ms - c.return_ms) * min(dt / 60.0, 1.0)
        route = next((r for r, on in c.routes.items() if on), None)
        flowing = (
            self.on("cip.supply_pump")
            and self.on("cip.return_pump")
            and route is not None
            and self.bus("mcc1")
        )
        c.return_lpm = 40.0 * LPM_PER_GPM if flowing else 0.0
        q = (
            self.num("cip.steam_valve_pct")
            / 100
            * 60e3
            * min(self.blr.psig / 12.0, 1.0)
        )
        loss = 300.0 * (c.supply_c - (amb_f - 32) / 1.8) + (15e3 if flowing else 0.0)
        c.supply_c += (q - loss) / (400.0 * WATER_CP) * dt
        self._cip_record(dt, route, flowing)

    def _cip_record(self, dt: float, route: str | None, flowing: bool) -> None:
        """A circuit run starts when both pumps run on a proven route and ends
        when either stops; the record is verified by temperature, return
        conductivity, and proven-flow time (DESIGN.md §5.3)."""
        c = self.cip
        if flowing:
            if c.run is None:
                c.run = CipRecord(
                    circuit=(route or "").removeprefix("route_"),
                    started=self.now.isoformat(),
                )
            r = c.run
            r.duration_s += dt
            r.flow_proven_s += dt if c.return_lpm > 5.0 * LPM_PER_GPM else 0.0
            r.max_supply_f = max(r.max_supply_f, c.supply_c * 1.8 + 32)
            r.max_return_ms = max(r.max_return_ms, c.return_ms)
            if self.on("cip.caustic_dose"):
                r.chemical = "caustic"
            elif self.on("cip.acid_dose") and r.chemical == "none":
                r.chemical = "acid"
            return
        if c.run is not None:
            r = c.run
            r.ended = self.now.isoformat()
            contact_ok = r.flow_proven_s >= CIP_MIN_CONTACT_S
            if r.chemical == "caustic":
                r.verified = (
                    contact_ok
                    and r.max_supply_f >= CIP_MIN_CAUSTIC_F
                    and r.max_return_ms >= CIP_MIN_RETURN_MS
                )
            else:
                r.verified = contact_ok and r.chemical != "none"
            c.records.append(r)
            del c.records[:-CIP_RECORDS]
            c.run = None

    def _wastewater(self, dt: float) -> None:
        w = self.wwn
        c = self.cip
        day = self.now.date().isoformat()
        if day != w.day:
            w.day = day
            w.discharged_l_day = w.inhibited_s = 0.0
            w.inhibit_events = 0
            w.discharge_ph_min, w.discharge_ph_max = 14.0, 0.0
        drain_lpm = self.input("brewhouse/drain.gpm", 0.0) * LPM_PER_GPM
        drain_mol = drain_lpm * -0.0005  # brewhouse rinse and trub: slightly acid
        if c.return_lpm > 0 and c.return_ms < 5.0:  # rinse water returns to drain
            drain_lpm += c.return_lpm
            drain_mol += c.return_lpm * c.chem * c.return_ms / MS_PER_MOLAR
        w.liters += drain_lpm / 60.0 * dt
        w.base_mol += drain_mol / 60.0 * dt
        if self.on("wwn.caustic_pump"):
            w.base_mol += 0.5 * dt  # 25 % NaOH at about 5 mL/s
        if self.on("wwn.acid_pump"):
            w.base_mol -= 0.5 * dt
        ph = ph_of(w.base_mol / max(w.liters, 1.0))
        commanded = self.on("wwn.discharge_valve")
        permitted = w.permit_low <= ph <= w.permit_high
        discharging = commanded and permitted and w.liters > 0
        if discharging:
            out = min(30.0 * LPM_PER_GPM / 60.0 * dt, w.liters)
            w.base_mol -= w.base_mol * out / max(w.liters, 1.0)
            w.liters -= out
            w.discharged_l += out
            w.discharged_l_day += out
            w.discharge_ph_min = min(w.discharge_ph_min, ph)
            w.discharge_ph_max = max(w.discharge_ph_max, ph)
        inhibited = commanded and not permitted
        if inhibited:
            w.inhibited_s += dt
            if not w.inhibited:
                w.inhibit_events += 1
        w.inhibited = inhibited
        w.liters = min(w.liters, w.capacity_l)
        if int(self.t) % 3600 == 0:
            w.samples.append(
                PhSample(
                    time=self.now.isoformat(),
                    ph=round(ph, 2),
                    level_pct=round(100.0 * w.liters / w.capacity_l, 1),
                    discharging=discharging,
                )
            )
            del w.samples[:-WWN_SAMPLES]

    # -- publications ------------------------------------------------------------------

    @property
    def ph(self) -> float:
        """Neutralization tank pH."""
        return ph_of(self.wwn.base_mol / max(self.wwn.liters, 1.0))

    def point_value(self, pub: Publication) -> Value:
        """A rio-utilities or glycol-chiller point."""
        tag = f"{pub.device}.{pub.point}"
        if tag in AUTO_COMMANDS:
            return self.cmd(tag)
        g, b, a, c, w, k = self.gly, self.blr, self.air, self.co2, self.wwn, self.cip
        if pub.device == "cippanel":
            return k.routes.get(pub.point or "", False)
        values: dict[str, Value] = {
            "chiller.supply_f": g.temp_c * 1.8 + 32,
            "chiller.return_f": g.return_c * 1.8 + 32,
            "chiller.power_kw": g.chiller_kw,
            "chiller.stage1_on": g.stage1,
            "chiller.stage2_on": g.stage2,
            "chiller.fault": bool(g.failed),
            "glycol.reservoir_pct": 85.0,
            "glycol.pump_psig": g.pump_psig,
            "glycol.pump_running": g.pump_psig > 1.0,
            "boiler.pressure_psig": b.psig,
            "boiler.power_kw": b.power_kw,
            "boiler.running": b.power_kw > 1.0,
            "boiler.low_water": b.low_water,
            "aircomp.header_psig": a.psig,
            "aircomp.running": a.loaded,
            "aircomp.dryer_fault": False,
            "co2.tank_psig": 300.0 if c.kg > 0 else 0.0,
            "co2.tank_level_pct": 100.0 * c.kg / c.capacity_kg,
            "co2.supply_psig": c.supply_psig,
            "ro.permeate_gpm": 10.0 if self.ro_running else 0.0,
            "ro.conductivity_us": 12.0 if self.ro_running else 0.0,
            "ro.running": self.ro_running,
            "wwn.level_pct": 100.0 * w.liters / w.capacity_l,
            "wwn.ph": self.ph,
            "wwn.high_level": w.liters > 0.9 * w.capacity_l,
            "cip.supply_f": k.supply_c * 1.8 + 32,
            "cip.return_cond_ms": k.return_ms,
            "cip.return_gpm": k.return_lpm / LPM_PER_GPM,
            "cip.caustic_level_pct": k.caustic_pct,
            "cip.acid_level_pct": k.acid_pct,
            "cip.return_flow": k.return_lpm > 5.0,
        }
        if tag not in values:
            raise KeyError(f"no model value for {pub.key}")
        value = values[tag]
        return value if isinstance(value, bool) else max(float(value), 0.0)

    def output_value(self, name: str) -> Value:
        """Outputs to the cellar, brewhouse, packaging, and power."""
        match name:
            case "glycol.supply_f":
                return self.gly.temp_c * 1.8 + 32
            case "steam.psig":
                return self.blr.psig
            case "air.psig":
                return self.air.psig
            case "co2.supply_psig":
                return self.co2.supply_psig
            case "load.boiler.kw":
                return self.blr.power_kw
            case "load.chiller.kw":
                speed = (
                    self.num("glycol.pump_speed_pct") / 100
                    if self.on("glycol.pump_run")
                    else 0.0
                )
                return self.gly.chiller_kw + 2.2 * speed**3
            case "load.air.kw":
                if not self.on("aircomp.enable"):
                    return 0.0
                return 12.0 if self.air.loaded else 3.5
            case "load.ro.kw":
                return 4.0 if self.ro_running else 0.3
            # Compliance counters for the KPI API (DESIGN.md §18A).
            case "wastewater.inhibit_events":
                return float(self.wwn.inhibit_events)
            case "cip.verified":
                return float(sum(1 for r in self.cip.records if r.verified))
            case "cip.unverified":
                return float(sum(1 for r in self.cip.records if not r.verified))
        raise KeyError(f"no output {name}")

    def truth(self) -> dict[str, Any]:
        """Published values plus faults, inventories, and the compliance records
        (the wastewater discharge log and the CIP verification records)."""
        doc = super().truth()
        w = self.wwn
        doc["utilities"] = {
            "chiller_failed_stages": sorted(self.gly.failed),
            "co2_kg": self.co2.kg,
            "wastewater_discharged_l": w.discharged_l,
            "autopilot": self.autopilot,
        }
        discharged = w.discharged_l_day > 0
        doc["wastewater"] = {
            "permit_band": [w.permit_low, w.permit_high],
            "day": w.day,
            "ph": round(self.ph, 2),
            "discharged_l_day": round(w.discharged_l_day, 1),
            "discharge_ph_min": round(w.discharge_ph_min, 2) if discharged else None,
            "discharge_ph_max": round(w.discharge_ph_max, 2) if discharged else None,
            "inhibited_s": round(w.inhibited_s, 1),
            "inhibit_events": w.inhibit_events,
            "samples": [s.model_dump() for s in w.samples[-24:]],
        }
        doc["cip"] = {
            "active": self.cip.run.model_dump() if self.cip.run else None,
            "records": [r.model_dump() for r in self.cip.records[-10:]],
        }
        return doc
