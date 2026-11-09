"""The frame a browser receives, and how it is built from the sim-ctl snapshot.

The weather federate on sim-ctl serves the federation's values as
``GET /api/v1/state`` (``{"seq", "t_s", "sim_time", "wall_time", "values":
{"<federate>/<key>": value}}``, DESIGN.md §9.5). :func:`build_frame` turns
those flat HELICS keys into the scene-shaped :class:`Frame`: the cellar (air,
vessels), the plant power, and a list of :class:`Finding` the white cell should
notice (a purge fan commanded off while tier 1 is active, a relief valve
lifted, a carbonation stone open while the CO2 relay is open).

Everything here is ground truth. The operator's layer (what the HMI shows) is a
separate, optional :class:`OperatorView`: the offline harness can produce one,
and in the range it waits on a historian relay (DESIGN.md §15.10), so a frame
without it says so instead of pretending.

The functions are pure (no I/O), so the tests drive them with plain dicts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

#: psia per kPa.
PSI_PER_KPA: float = 0.1450377
#: Site ambient when the snapshot has no weather pressure (DESIGN.md §3.1).
SITE_PSIA: float = 12.1
#: Vessel MAWP and PSV set pressure, psig (DESIGN.md §6.3).
MAWP_PSIG: float = 15.0
#: Cellar CO2 alarm tiers on the sensed value, ppm (DESIGN.md §6.7).
CO2_TIERS: tuple[float, float, float] = (5000.0, 15000.0, 30000.0)
#: Fermenter and bright-tank publication keys: cellar/fv101.pressure_psia.
_VESSEL_RE: re.Pattern[str] = re.compile(r"^cellar/((?:fv|bbt)\d+)\.pressure_psia$")
_LOAD_RE: re.Pattern[str] = re.compile(r"^(\w+)/load\.([\w-]+)\.kw$")
_BUS_RE: re.Pattern[str] = re.compile(r"^power/bus\.(\w+)\.energized$")

type Value = float | bool | str


class Vessel(BaseModel):
    """One fermenter or bright tank, as the simulation knows it."""

    id: str  # FV101
    label: str  # FV-101
    kind: Literal["fv", "bbt"]
    #: Fill. Fermenters have no level transmitter, so for them this is truth
    #: only (``level_measured`` false); a bright tank's is its LT01 point.
    level_pct: float | None = None
    level_measured: bool = False
    temp_f: float | None = None
    temp_cone_f: float | None = None
    pressure_psig: float | None = None
    #: Glycol jacket solenoids (FV: side, cone; BBT: one jacket).
    glycol_valves: list[bool] = Field(default_factory=list)
    spund_open: bool = False
    spund_sp_psig: float | None = None
    spund_gps: float = 0.0
    psv_lifted: bool = False
    psv_gps: float = 0.0
    leak_gps: float = 0.0
    gravity_plato: float | None = None
    co2_vol: float | None = None
    carb_valve_pct: float | None = None


class Fan(BaseModel):
    """A cellar purge fan: what bas-vent commands and whether it turns."""

    commanded: bool
    running: bool


class CellarAir(BaseModel):
    """Cellar CO2 and ventilation (the life-safety path of DESIGN.md §6.7)."""

    co2_mixed_ppm: float | None = None  # well mixed; no sensor reads it
    sensors_ppm: list[float] = Field(default_factory=list)  # the four low sensors
    tiers: list[bool] = Field(default_factory=list)  # 5,000 / 15,000 / 30,000
    fans: list[Fan] = Field(default_factory=list)
    relay_ok: bool | None = None  # bas-vent's gas-detection relay
    relay_at_rio: bool | None = None  # the same contact as rio-cellar reads it
    #: True when every carbonation valve is shut while the relay is open;
    #: None while the relay is closed (no inhibit asked for).
    inhibit_effective: bool | None = None
    co2_into_room_gps: float = 0.0
    temp_f: float | None = None
    ep1_energized: bool | None = None


class Cellar(BaseModel):
    """The cellar room and its vessels."""

    air: CellarAir
    vessels: list[Vessel]
    glycol_supply_f: float | None = None
    kinetic_hours: float | None = None  # cellar/clock.sim_hours (48x)


class Load(BaseModel):
    """An electrical load and the bus that feeds it."""

    name: str
    bus: str
    kw: float


class Power(BaseModel):
    """Plant power: sources, the ATS, the buses, and the loads (DESIGN.md §7)."""

    utility_ok: bool | None = None
    utility_breaker_closed: bool | None = None
    utility_tripped: bool | None = None
    grid_kw: float | None = None  # import, negative for export
    pv_kw: float | None = None
    pv_limit_pct: float | None = None
    pv_connected: bool | None = None
    ghi_wm2: float | None = None
    bess_kw: float | None = None  # positive discharging
    bess_soc_pct: float | None = None
    bess_connected: bool | None = None
    gen_running: bool | None = None
    gen_kw: float | None = None
    gen_fuel_pct: float | None = None
    ats_on_emergency: bool | None = None
    ep1_kw: float | None = None
    feeders: dict[str, bool] = Field(default_factory=dict)  # MSB breakers
    buses: dict[str, bool] = Field(default_factory=dict)
    loads: list[Load] = Field(default_factory=list)
    load_kw: float = 0.0
    demand_kw: float | None = None
    peak_kw: float | None = None
    onpeak: bool | None = None
    rate_usd_kwh: float | None = None
    cost_usd: float | None = None


class Finding(BaseModel):
    """Something in the truth the white cell should notice."""

    level: Literal["info", "warn", "alarm"]
    scene: Literal["cellar", "fermenters", "power"]
    text: str


class OperatorView(BaseModel):
    """What the operator's HMI shows, beside the truth.

    ``source`` says where it came from (the offline harness's published field
    values, or a historian relay); ``frozen`` marks a replay in the harness.
    """

    source: str
    frozen: bool = False
    co2_sensors_ppm: list[float]
    tiers: list[bool]
    fans_running: list[bool]
    relay_ok: bool | None = None
    vessel_temp_f: dict[str, float] = Field(default_factory=dict)
    vessel_pressure_psig: dict[str, float] = Field(default_factory=dict)


class Frame(BaseModel):
    """One snapshot pushed to every browser."""

    seq: int
    source: str
    variant: str = ""
    sim_t_s: float
    sim_time: str | None
    #: Wall clock when the source took the state (epoch ms); the page shows
    #: the age and greys out when it grows.
    sampled_wall_ms: float
    sent_wall_ms: float = 0.0
    #: The source has stopped advancing (a stalled federate, a lost API).
    stale: bool = False
    speed: float = 1.0
    cellar: Cellar
    power: Power
    findings: list[Finding] = Field(default_factory=list)
    operator: OperatorView | None = None
    notes: list[str] = Field(default_factory=list)


# -- building ------------------------------------------------------------------------


class _Values:
    """Typed reads from the flat snapshot; a missing key reads None."""

    v: Mapping[str, Value]

    def __init__(self, values: Mapping[str, Value]) -> None:
        self.v = values

    def num(self, key: str) -> float | None:
        x = self.v.get(key)
        if x is None or isinstance(x, str):
            return None
        return float(x)

    def flag(self, key: str) -> bool | None:
        x = self.v.get(key)
        if x is None or isinstance(x, str):
            return None
        return bool(x) if isinstance(x, bool) else float(x) >= 0.5

    def num0(self, key: str) -> float:
        return self.num(key) or 0.0


def _label(unit: str) -> str:
    """``FV101`` -> ``FV-101``."""
    return re.sub(r"^([A-Z]+)(\d+)$", r"\1-\2", unit)


def _vessels(r: _Values, ambient: float) -> list[Vessel]:
    ids = sorted(
        (m.group(1) for k in r.v if (m := _VESSEL_RE.match(k))),
        key=lambda u: (not u.startswith("fv"), u),
    )
    out: list[Vessel] = []
    for u in ids:
        base = f"cellar/{u}"
        psia = r.num(f"{base}.pressure_psia")
        sp = r.num(f"{base}.spund_sp_psia")
        common = {
            "id": u.upper(),
            "label": _label(u.upper()),
            "pressure_psig": None if psia is None else psia - ambient,
            "psv_lifted": bool(r.flag(f"cellar/{u}-psv.lifted"))
            or r.num0(f"{base}.psv_gps") > 0,
            "psv_gps": r.num0(f"{base}.psv_gps"),
            "leak_gps": r.num0(f"{base}.leak_gps"),
        }
        if u.startswith("fv"):
            out.append(
                Vessel(
                    kind="fv",
                    level_pct=r.num(f"{base}.level_pct"),
                    level_measured=False,
                    temp_f=r.num(f"{base}-side.temp_f"),
                    temp_cone_f=r.num(f"{base}-cone.temp_f"),
                    glycol_valves=[
                        bool(r.flag(f"{base}-side.valve")),
                        bool(r.flag(f"{base}-cone.valve")),
                    ],
                    spund_open=bool(r.flag(f"{base}.spund_open")),
                    spund_sp_psig=None if sp is None else sp - ambient,
                    spund_gps=r.num0(f"{base}.spund_gps"),
                    gravity_plato=r.num(f"{base}.gravity_plato"),
                    **common,
                )
            )
        else:
            out.append(
                Vessel(
                    kind="bbt",
                    level_pct=r.num(f"{base}.level_pct"),
                    level_measured=True,
                    temp_f=r.num(f"{base}.temp_f"),
                    glycol_valves=[bool(r.flag(f"{base}.valve"))],
                    co2_vol=r.num(f"{base}.co2_vol"),
                    carb_valve_pct=r.num(f"{base}.co2_valve_pct"),
                    **common,
                )
            )
    return out


def _air(r: _Values, vessels: list[Vessel]) -> CellarAir:
    sensors = [
        x for i in range(1, 5) if (x := r.num(f"building/bas-vent.co2_{i}_ppm")) is not None
    ]
    tiers = [
        bool(r.flag(f"building/bas-vent.co2_tier_{i}"))
        for i in range(1, 4)
        if f"building/bas-vent.co2_tier_{i}" in r.v
    ]
    fans = [
        Fan(
            commanded=bool(r.flag(f"building/bas-vent.purge_fan_{i}")),
            running=bool(r.flag(f"building/bas-vent.purge_fan_{i}_status")),
        )
        for i in (1, 2)
        if f"building/bas-vent.purge_fan_{i}" in r.v
    ]
    relay = r.flag("building/vent.co2_relay_ok")
    inhibit: bool | None = None
    if relay is False:
        inhibit = all((v.carb_valve_pct or 0.0) <= 0.0 for v in vessels if v.kind == "bbt")
    return CellarAir(
        co2_mixed_ppm=r.num("building/cellar.co2_ppm"),
        sensors_ppm=sensors,
        tiers=tiers,
        fans=fans,
        relay_ok=relay,
        relay_at_rio=r.flag("cellar/gasrelay.co2_ok"),
        inhibit_effective=inhibit,
        co2_into_room_gps=r.num0("cellar/co2.vent_gps"),
        temp_f=r.num("building/cellar.temp_f"),
        ep1_energized=r.flag("power/bus.ep1.energized"),
    )


def _power(r: _Values, load_buses: Mapping[str, str]) -> Power:
    loads: list[Load] = []
    for key in sorted(r.v):
        m = _LOAD_RE.match(key)
        if m:
            name = m.group(2)
            loads.append(Load(name=name, bus=load_buses.get(name, "?"), kw=r.num0(key)))
    buses = {
        m.group(1): bool(r.flag(k)) for k in sorted(r.v) if (m := _BUS_RE.match(k))
    }
    feeders = {
        name: bool(r.flag(f"power/msb.{name}_closed"))
        for name in ("boiler", "pkg", "rtu")
        if f"power/msb.{name}_closed" in r.v
    }
    return Power(
        utility_ok=r.flag("power/gen-ats.utility_ok"),
        utility_breaker_closed=r.flag("power/relay-main.breaker_closed"),
        utility_tripped=r.flag("power/relay-main.tripped"),
        grid_kw=r.num("power/grid.kw"),
        pv_kw=r.num("power/pv-inverter.p_kw"),
        pv_limit_pct=r.num("power/pv-inverter.limit_pct"),
        pv_connected=r.flag("power/pv-inverter.connected"),
        ghi_wm2=r.num("weather/ghi.wm2"),
        bess_kw=r.num("power/bess-pcs.p_kw"),
        bess_soc_pct=r.num("power/bess-pcs.soc_pct"),
        bess_connected=r.flag("power/bess-pcs.connected"),
        gen_running=r.flag("power/gen-ats.gen_running"),
        gen_kw=r.num("power/gen-ats.gen_kw"),
        gen_fuel_pct=r.num("power/gen-ats.fuel_pct"),
        ats_on_emergency=r.flag("power/gen-ats.on_emergency"),
        ep1_kw=r.num("power/gen-ats.ep1_kw"),
        feeders=feeders,
        buses=buses,
        loads=loads,
        load_kw=sum(ld.kw for ld in loads if buses.get(ld.bus, True)),
        demand_kw=r.num("power/demand.kw"),
        peak_kw=r.num("power/demand.peak_kw"),
        onpeak=r.flag("power/tariff.onpeak"),
        rate_usd_kwh=r.num("power/tariff.rate_usd_kwh"),
        cost_usd=r.num("power/tariff.cost_usd"),
    )


def findings(cellar: Cellar, power: Power) -> list[Finding]:
    """What the truth shows that the white cell should notice."""
    out: list[Finding] = []
    air = cellar.air
    if air.tiers and air.tiers[0]:
        for i, fan in enumerate(air.fans, 1):
            if not fan.commanded:
                out.append(
                    Finding(
                        level="alarm",
                        scene="cellar",
                        text=f"Purge fan {i} is commanded off while CO2 tier 1 is "
                        "active (bas-vent's own logic would run it: a BACnet write?)",
                    )
                )
            elif not fan.running:
                out.append(
                    Finding(
                        level="alarm",
                        scene="cellar",
                        text=f"Purge fan {i} is commanded on but not running (EP-1 dead?)",
                    )
                )
    if air.inhibit_effective is False:
        out.append(
            Finding(
                level="alarm",
                scene="cellar",
                text="A carbonation valve is open while the CO2 relay is open: "
                "the inhibit is defeated",
            )
        )
    if air.relay_ok is not None and air.relay_at_rio is not None and air.relay_ok != air.relay_at_rio:
        out.append(
            Finding(
                level="warn",
                scene="cellar",
                text="rio-cellar reads the CO2 relay differently from bas-vent",
            )
        )
    for v in cellar.vessels:
        if v.psv_lifted:
            out.append(
                Finding(
                    level="alarm",
                    scene="fermenters",
                    text=f"{v.label} relief valve lifted, venting {v.psv_gps:.1f} g/s into the cellar",
                )
            )
        if v.spund_sp_psig is not None and v.spund_sp_psig > MAWP_PSIG:
            out.append(
                Finding(
                    level="warn",
                    scene="fermenters",
                    text=f"{v.label} spunding setpoint {v.spund_sp_psig:.1f} psig is above "
                    f"the {MAWP_PSIG:.0f} psig MAWP",
                )
            )
        if v.leak_gps > 0:
            out.append(
                Finding(
                    level="alarm",
                    scene="cellar",
                    text=f"CO2 release at {v.label}: {v.leak_gps:.0f} g/s",
                )
            )
    if power.utility_ok is False:
        out.append(Finding(level="warn", scene="power", text="Utility service lost"))
    if power.ats_on_emergency:
        out.append(
            Finding(level="info", scene="power", text="ATS on emergency: EP-1 on the genset")
        )
    if power.utility_breaker_closed is False and power.utility_ok:
        out.append(
            Finding(
                level="alarm",
                scene="power",
                text="Main utility breaker open with the utility healthy (a breaker command?)",
            )
        )
    return out


def build_frame(
    values: Mapping[str, Value],
    *,
    seq: int,
    source: str,
    sim_t_s: float,
    sim_time: str | None,
    sampled_wall_ms: float,
    variant: str = "",
    load_buses: Mapping[str, str] | None = None,
    operator: OperatorView | None = None,
    stale: bool = False,
    speed: float = 1.0,
    notes: list[str] | None = None,
) -> Frame:
    """The frame for one snapshot of HELICS values."""
    r = _Values(values)
    kpa = r.num("weather/pressure.kpa")
    ambient = kpa * PSI_PER_KPA if kpa else SITE_PSIA
    vessels = _vessels(r, ambient)
    cellar = Cellar(
        air=_air(r, vessels),
        vessels=vessels,
        glycol_supply_f=r.num("utilities/glycol.supply_f"),
        kinetic_hours=r.num("cellar/clock.sim_hours"),
    )
    power = _power(r, load_buses or {})
    return Frame(
        seq=seq,
        source=source,
        variant=variant,
        sim_t_s=sim_t_s,
        sim_time=sim_time,
        sampled_wall_ms=sampled_wall_ms,
        stale=stale,
        speed=speed,
        cellar=cellar,
        power=power,
        findings=findings(cellar, power),
        operator=operator,
        notes=notes or [],
    )


def operator_from_values(
    values: Mapping[str, Value], source: str, *, frozen: bool = False
) -> OperatorView:
    """The operator's view as the field devices publish it (what an honest HMI
    shows): the sensors, tiers, fan status, the relay, the vessel readings."""
    r = _Values(values)
    kpa = r.num("weather/pressure.kpa")
    ambient = kpa * PSI_PER_KPA if kpa else SITE_PSIA
    temps: dict[str, float] = {}
    pressures: dict[str, float] = {}
    for k in values:
        m = _VESSEL_RE.match(k)
        if not m:
            continue
        u = m.group(1)
        t = r.num(f"cellar/{u}-side.temp_f") if u.startswith("fv") else r.num(f"cellar/{u}.temp_f")
        if t is not None:
            temps[u.upper()] = t
        pressures[u.upper()] = r.num0(k) - ambient
    return OperatorView(
        source=source,
        frozen=frozen,
        co2_sensors_ppm=[r.num0(f"building/bas-vent.co2_{i}_ppm") for i in range(1, 5)],
        tiers=[bool(r.flag(f"building/bas-vent.co2_tier_{i}")) for i in range(1, 4)],
        fans_running=[bool(r.flag(f"building/bas-vent.purge_fan_{i}_status")) for i in (1, 2)],
        relay_ok=r.flag("cellar/gasrelay.co2_ok"),
        vessel_temp_f=temps,
        vessel_pressure_psig=pressures,
    )
