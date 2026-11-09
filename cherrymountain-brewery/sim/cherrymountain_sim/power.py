"""Power federate: the 480 V plant feeder in OpenDSS (DESIGN.md §7, §9.3).

**Circuit** (DESIGN.md §7.1): a 12.47 kV utility source behind the 500 kVA
T-1 (5.75 % Z), the 52-M main breaker, the 480 V MSB with switchable feeders
(boiler, packaging) and MCC-1, the 75 kVA T-2 to the 208 V LP-1 with the RTU-1
breaker, the ATS between the MSB and the emergency panel EP-1, and the 100 kW
genset as a second ``Vsource`` on EP-1's island, enabled only while the genset
carries EP-1 (OpenDSS needs a source in every island). The 100 kWac PV is a
``PVSystem`` and the 100 kW / 200 kWh BESS a ``Storage`` element, both on the
MSB. Loads are ``Load`` elements whose kW come from the other federates.

The energized state of each bus comes from the breaker and ATS logic, not from
OpenDSS: elements on a de-energized bus are disabled before the solve, because
OpenDSS cannot solve an island without a source.

**Devices.** relay-main (meter, 52-M, long-time overcurrent trip at 1,000 A
for 10 s), the MSB feeder breakers, the PV inverter (power limit =
min(site limit, utility limit); IEEE 1547 trip within 2 s of losing the MSB
and a 300 s return-to-service delay), the BESS PCS (state of charge with
95 % one-way efficiency, held between 10 % and 95 %), and the genset and ATS
(start on utility loss, transfer about 10 s after it, retransfer after a
120 s stable-source timer, 300 s cool-down).

**Tariff.** :class:`cherrymountain_sim.tariff.Meter` turns the service import
into 15-minute demand, the monthly peak, and the SV-SGT bill.

Until plc-power writes, a built-in controller does its job: feeders closed,
inverters enabled, and the BESS dispatch of DESIGN.md §7.3 (hold 15-minute
demand under the target, recharge overnight to the brew-day minimum, shift
energy into the weekday 17:00-21:00 window, soak up PV surplus). Authority is
per point: a point the PLC or the DER gateway has written follows the write.

``utility-breaker`` events open and close the utility source.
"""

from __future__ import annotations

import logging
import math
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
from cherrymountain_sim.tariff import Meter

LOG: logging.Logger = logging.getLogger("cherrymountain_sim.power")
NOMINAL_V: float = 480.0
#: ANSI C84.1 Range A service voltage band (per unit).
RANGE_A: tuple[float, float] = (0.95, 1.05)
TRIP_AMPS: float = 1000.0
TRIP_DELAY_S: float = 10.0
#: Bus -> (OpenDSS bus, kV) for loads.
BUS_KV: dict[str, float] = {
    "msb": 0.48,
    "mcc1": 0.48,
    "boiler": 0.48,
    "pkg": 0.48,
    "ep1": 0.48,
    "lp1": 0.208,
    "rtu": 0.208,
}
#: Power factor by load (motors 0.85, resistive 1.0).
LOAD_PF: dict[str, float] = {
    "boiler": 1.0,
    "kegwasher": 0.98,
    "lighting": 0.95,
    "otracks": 0.95,
}

AUTO_COMMANDS: dict[str, Value] = {
    "relay-main.close": True,
    "msb.boiler_close": True,
    "msb.pkg_close": True,
    "msb.rtu_close": True,
    "pv-inverter.enable": True,
    "pv-inverter.site_limit_pct": 100.0,
    "pv-inverter.utility_limit_pct": 100.0,
    "bess-pcs.enable": True,
    "bess-pcs.p_sp_kw": 0.0,
    "bess-pcs.utility_p_sp_kw": 0.0,
    "gen-ats.gen_start": False,
}

CIRCUIT: str = """
clear
new circuit.cherrymountain basekv=12.47 pu=1.0 phases=3 bus1=src MVAsc3=200 MVAsc1=180
new line.utility bus1=src bus2=pcc phases=3 switch=yes
new transformer.t1 phases=3 windings=2 buses=[pcc, lv] conns=[delta wye]
~ kvs=[12.47 0.48] kvas=[500 500] %r=1.0 xhl=5.75
new line.main bus1=lv bus2=msb phases=3 switch=yes
new line.fdr_boiler bus1=msb bus2=boiler phases=3 switch=yes
new line.fdr_pkg bus1=msb bus2=pkg phases=3 switch=yes
new line.fdr_mcc1 bus1=msb bus2=mcc1 phases=3 r1=0.005 x1=0.002 length=1 units=none
new transformer.t2 phases=3 windings=2 buses=[msb, lp1] conns=[delta wye]
~ kvs=[0.48 0.208] kvas=[75 75] %r=1.2 xhl=3.5
new line.fdr_rtu bus1=lp1 bus2=rtu phases=3 switch=yes
new line.ats_normal bus1=msb bus2=ep1 phases=3 switch=yes
new line.ats_emergency bus1=gen bus2=ep1 phases=3 switch=yes
new vsource.genset bus1=gen phases=3 basekv=0.48 pu=1.0 MVAsc3=1.5 MVAsc1=1.5
new pvsystem.pv bus1=msb phases=3 kv=0.48 kva=110 pmpp=100 irradiance=0
~ %cutin=0.1 %cutout=0.1
new storage.bess bus1=msb phases=3 kv=0.48 kwrated=100 kwhrated=200 %stored=50
~ dispmode=external state=idling
set voltagebases=[12.47 0.48 0.208]
calcvoltagebases
"""


class PowerSettings(BaseModel):
    """Power sim settings (helics.json ``sim``)."""

    demand_target_kw: float = 250.0
    bess_min_soc_pct: float = 50.0
    bess_soc_pct: float = 60.0
    retransfer_s: float = 120.0
    return_to_service_s: float = 300.0
    #: A utility BESS dispatch (rtu-der AO 3) stands for this long after its
    #: last write, 0 kW included; then the site dispatch resumes. rtu-der writes
    #: only when the DERMS operates the point (ot-sim's FEP Modbus client writes
    #: on a bus update, never at startup), so no write means no utility command.
    utility_dispatch_hold_s: float = 900.0


class Grid(BaseModel):
    """Breaker, ATS, inverter, and battery state."""

    utility_ok: bool = True
    tripped: bool = False
    overcurrent_s: float = 0.0
    last_close: bool = True
    on_emergency: bool = False
    gen_running: bool = False
    gen_start_s: float = -1.0
    utility_lost_s: float = -1.0
    utility_back_s: float = -1.0
    gen_cooldown_s: float = -1.0
    pv_connected: bool = True
    bess_connected: bool = True
    msb_back_s: float = 0.0
    soc_pct: float = 60.0
    fuel_pct: float = 85.0
    bess_kw: float = 0.0
    pv_kw: float = 0.0
    p_kw: float = 0.0
    q_kvar: float = 0.0
    v_pu: float = 1.0
    amps: float = 0.0
    ep1_kw: float = 0.0


class Flow(BaseModel):
    """One power-flow result."""

    p_kw: float
    q_kvar: float
    amps: float
    v_pu: dict[str, float]


class DssFlow:
    """The plant circuit in OpenDSS (OpenDSSDirect.py ~=0.8.4)."""

    dss: Any
    loads: dict[str, str]

    def __init__(self, loads: dict[str, str]) -> None:
        import opendssdirect as dss  # noqa: PLC0415 - optional, imported lazily

        self.dss = dss
        self.loads = loads
        for line in CIRCUIT.strip().splitlines():
            dss.Text.Command(line.strip())
        for name, bus in loads.items():
            pf = LOAD_PF.get(name, 0.85)
            kv = BUS_KV[bus]
            dss.Text.Command(
                f"new load.{name} bus1={bus} phases=3 kv={kv} kw=0 pf={pf} model=1"
            )
        dss.Text.Command("calcvoltagebases")

    def solve(
        self,
        energized: dict[str, bool],
        switches: dict[str, bool],
        load_kw: dict[str, float],
        pv_kw: float,
        bess_kw: float,
        genset: bool,
    ) -> Flow:
        """Solve one snapshot; returns the service flow and bus voltages."""
        cmd = self.dss.Text.Command
        for line, closed in switches.items():
            cmd(f"{'close' if closed else 'open'} line.{line} term=1")
        cmd(f"vsource.genset.enabled={'true' if genset else 'false'}")
        cmd(f"line.ats_emergency.enabled={'true' if genset else 'false'}")
        for name, bus in self.loads.items():
            on = energized.get(bus, False) and load_kw.get(name, 0.0) > 0
            cmd(f"load.{name}.enabled={'true' if on else 'false'}")
            if on:
                cmd(f"load.{name}.kw={load_kw[name]:.3f}")
        msb = energized.get("msb", False)
        cmd(f"pvsystem.pv.enabled={'true' if msb and pv_kw > 0 else 'false'}")
        if msb and pv_kw > 0:
            cmd(f"pvsystem.pv.irradiance={pv_kw / 100.0:.4f}")
        if msb and abs(bess_kw) > 0.01:
            state = "discharging" if bess_kw > 0 else "charging"
            cmd(f"storage.bess.enabled=true state={state} kw={bess_kw:.3f}")
        else:
            cmd("storage.bess.enabled=false")
        self.dss.Solution.Solve()
        v: dict[str, float] = {}
        for bus in ("msb", "lp1", "ep1", "mcc1"):
            self.dss.Circuit.SetActiveBus(bus)
            mags = self.dss.Bus.puVmagAngle()[0::2]
            v[bus] = sum(mags) / len(mags) if mags and energized.get(bus) else 0.0
        self.dss.Circuit.SetActiveElement("Line.main")
        powers = self.dss.CktElement.Powers()
        currents = self.dss.CktElement.CurrentsMagAng()
        p = sum(powers[0:6:2])
        q = sum(powers[1:6:2])
        amps = max(currents[0:6:2]) if msb else 0.0
        if not msb:
            p = q = 0.0
        return Flow(p_kw=p, q_kvar=q, amps=amps, v_pu=v)


class SimpleFlow:
    """Fallback without OpenDSS: power balance and a T-1 impedance drop."""

    loads: dict[str, str]

    def __init__(self, loads: dict[str, str]) -> None:
        self.loads = loads

    def solve(
        self,
        energized: dict[str, bool],
        switches: dict[str, bool],
        load_kw: dict[str, float],
        pv_kw: float,
        bess_kw: float,
        genset: bool,
    ) -> Flow:
        """Balance the MSB side; approximate the voltage drop across T-1."""
        msb_kw = sum(
            kw
            for n, kw in load_kw.items()
            if energized.get(self.loads[n], False)
            and not (genset and self.loads[n] == "ep1")
        )
        p = msb_kw - pv_kw - bess_kw if energized.get("msb") else 0.0
        q = 0.5 * max(msb_kw, 0.0)
        drop = (0.01 * p + 0.0575 * q) / 500.0
        v = 1.0 - drop
        volts = {
            b: (v if energized.get(b) else 0.0) for b in ("msb", "lp1", "ep1", "mcc1")
        }
        amps = math.hypot(p, q) * 1e3 / (math.sqrt(3) * NOMINAL_V * max(v, 0.1))
        return Flow(p_kw=p, q_kvar=q, amps=amps, v_pu=volts)


class PowerFederate(Federate):
    """The power federate."""

    name: ClassVar[str] = "power"
    settings: PowerSettings
    grid: Grid
    meter: Meter
    flow_model: DssFlow | SimpleFlow
    load_bus: dict[str, str]
    load_key: dict[str, str]
    auto: dict[str, Value]
    energized: dict[str, bool]
    utility_write_t: float | None

    def __init__(self, cfg: FederateConfig, clock: SimClock | None = None) -> None:
        super().__init__(cfg, clock)
        self.settings = PowerSettings.model_validate(cfg.sim)
        self.utility_write_t = None
        self.grid = Grid(soc_pct=self.settings.bess_soc_pct)
        self.meter = Meter()
        self.load_bus = {ld.name: ld.bus for ld in cfg.loads}
        self.load_key = {ld.name: ld.key for ld in cfg.loads}
        try:
            self.flow_model = DssFlow(self.load_bus)
        except ImportError:
            LOG.warning(
                "OpenDSSDirect.py is not installed; using the simple flow model"
            )
            self.flow_model = SimpleFlow(self.load_bus)
        self.auto = dict(AUTO_COMMANDS)
        self.energized = dict.fromkeys(BUS_KV, True)

    def cmd(self, tag: str) -> Value:
        """Per-point authority: a written point follows its last write."""
        return self.commands.get(tag, self.auto[tag])

    def on(self, tag: str) -> bool:
        """A binary command."""
        return bool(self.cmd(tag))

    def num(self, tag: str) -> float:
        """A numeric command."""
        return float(self.cmd(tag))

    def on_event(self, event: ScenarioEvent) -> None:
        """``utility-breaker``: open or close the utility source."""
        super().on_event(event)
        if event.type == "utility-breaker":
            self.grid.utility_ok = event.params.get("action") == "close"

    # -- dynamics ----------------------------------------------------------------------

    def load_kw(self, name: str) -> float:
        """A load's kW from its publisher (or the default)."""
        return self.input(self.load_key[name], 0.0)

    def advance(self, wall_dt_s: float) -> None:
        """Breaker and ATS logic, inverters, power flow, meter."""
        g = self.grid
        dt = wall_dt_s
        t = self.t
        close = self.on("relay-main.close")
        if close and not g.last_close:
            g.tripped = False  # a close command after an open resets the trip
        g.last_close = close
        main = close and not g.tripped
        msb = g.utility_ok and main
        e = self.energized
        e["msb"] = e["mcc1"] = e["lp1"] = msb
        e["boiler"] = msb and self.on("msb.boiler_close")
        e["pkg"] = msb and self.on("msb.pkg_close")
        e["rtu"] = msb and self.on("msb.rtu_close")
        self._ats(msb, t, dt)
        e["ep1"] = (msb and not g.on_emergency) or (g.on_emergency and g.gen_running)
        self._inverters(msb, t, dt)
        loads = {n: self.load_kw(n) for n in self.load_bus}
        flow = self.flow_model.solve(
            e,
            {
                "utility": g.utility_ok,
                "main": main,
                "fdr_boiler": self.on("msb.boiler_close"),
                "fdr_pkg": self.on("msb.pkg_close"),
                "fdr_rtu": self.on("msb.rtu_close"),
                "ats_normal": not g.on_emergency,
                "ats_emergency": g.on_emergency,
            },
            loads,
            g.pv_kw,
            g.bess_kw,
            g.on_emergency and g.gen_running,
        )
        g.p_kw, g.q_kvar, g.amps = flow.p_kw, flow.q_kvar, flow.amps
        g.v_pu = flow.v_pu.get("msb", 0.0)
        g.ep1_kw = sum(
            kw for n, kw in loads.items() if self.load_bus[n] == "ep1" and e["ep1"]
        )
        if g.gen_running and g.on_emergency:
            g.fuel_pct = max(g.fuel_pct - g.ep1_kw * 0.00002 * dt, 0.0)
        g.overcurrent_s = g.overcurrent_s + dt if g.amps > TRIP_AMPS else 0.0
        if g.overcurrent_s >= TRIP_DELAY_S:
            g.tripped = True
        self.meter.add(t, self.now, g.p_kw, dt)

    def _ats(self, msb: bool, t: float, dt: float) -> None:
        g = self.grid
        s = self.settings
        if not msb:
            g.utility_back_s = -1.0
            if g.utility_lost_s < 0:
                g.utility_lost_s = t
            lost = t - g.utility_lost_s
            if lost >= 1.0 and not g.gen_running and g.fuel_pct > 0:
                g.gen_running, g.gen_start_s = True, t
            if g.gen_running and t - g.gen_start_s >= 8.0 and lost >= 10.0:
                g.on_emergency = True
        else:
            g.utility_lost_s = -1.0
            if g.on_emergency:
                if g.utility_back_s < 0:
                    g.utility_back_s = t
                if t - g.utility_back_s >= s.retransfer_s:
                    g.on_emergency = False
                    g.gen_cooldown_s = t
            exercise = self.on("gen-ats.gen_start")
            if exercise and not g.gen_running:
                g.gen_running, g.gen_start_s = True, t
            if (
                g.gen_running
                and not g.on_emergency
                and not exercise
                and g.gen_cooldown_s >= 0
                and t - g.gen_cooldown_s >= 300.0
            ):
                g.gen_running, g.gen_cooldown_s = False, -1.0
            elif (
                g.gen_running
                and not g.on_emergency
                and not exercise
                and g.gen_cooldown_s < 0
            ):
                g.gen_cooldown_s = t

    def _inverters(self, msb: bool, t: float, dt: float) -> None:
        g = self.grid
        s = self.settings
        if not msb:
            g.pv_connected = g.bess_connected = False  # IEEE 1547 trip (< 2 s)
            g.msb_back_s = -1.0
        elif not g.pv_connected:
            if g.msb_back_s < 0:
                g.msb_back_s = t
            if t - g.msb_back_s >= s.return_to_service_s:
                g.pv_connected = g.bess_connected = True
        ghi = self.input("weather/ghi.wm2", 0.0)
        amb_c = (self.input("weather/ambient.temp_f", 75.0) - 32) / 1.8
        cell_c = amb_c + 0.03 * ghi
        avail = 100.0 * ghi / 1000.0 * (1 - 0.004 * (cell_c - 25.0))
        limit = min(
            self.num("pv-inverter.site_limit_pct"),
            self.num("pv-inverter.utility_limit_pct"),
        )
        pv_on = g.pv_connected and self.on("pv-inverter.enable")
        g.pv_kw = min(max(avail, 0.0), limit) if pv_on else 0.0
        if "bess-pcs.p_sp_kw" not in self.commands:
            self.auto["bess-pcs.p_sp_kw"] = self._dispatch()
        utility_active: bool = (
            self.utility_write_t is not None
            and t - self.utility_write_t <= s.utility_dispatch_hold_s
        )
        sp = (
            self.num("bess-pcs.utility_p_sp_kw")
            if utility_active
            else self.num("bess-pcs.p_sp_kw")
        )
        if not (g.bess_connected and self.on("bess-pcs.enable")):
            sp = 0.0
        sp = min(max(sp, -100.0), 100.0)
        if sp > 0 and g.soc_pct <= 10.0 or sp < 0 and g.soc_pct >= 95.0:
            sp = 0.0
        g.bess_kw = sp
        energy = sp * dt / 3600.0
        g.soc_pct -= (energy / 0.95 if sp > 0 else energy * 0.95) / 200.0 * 100.0

    def on_write(self, device: str, point: str, value: Value) -> None:
        """Note when the utility last dispatched the BESS (any value, 0 kW too)."""
        if (device, point) == ("bess-pcs", "utility_p_sp_kw"):
            self.utility_write_t = self.t

    def _dispatch(self) -> float:
        """BESS dispatch of DESIGN.md §7.3 (the built-in plc-power logic)."""
        g = self.grid
        s = self.settings
        now = self.now
        site_kw = g.p_kw + g.bess_kw  # import without the battery
        if site_kw > s.demand_target_kw - 10.0 and g.soc_pct > 10.0:
            return min(site_kw - (s.demand_target_kw - 10.0), 100.0)
        if site_kw < 0 and g.soc_pct < 95.0:  # PV surplus
            return max(site_kw, -100.0)
        if self.meter.tariff.onpeak(now) and g.soc_pct > s.bess_min_soc_pct:
            return 50.0
        if now.hour < 5 and g.soc_pct < 95.0:
            return -30.0  # overnight off-peak recharge
        return 0.0

    # -- publications ------------------------------------------------------------------

    def point_value(self, pub: Publication) -> Value:
        """A field point of relay-main, pv-inverter, bess-pcs, or gen-ats."""
        tag = f"{pub.device}.{pub.point}"
        if tag in AUTO_COMMANDS:
            return self.cmd(tag)
        g = self.grid
        e = self.energized
        values: dict[str, Value] = {
            "relay-main.p_kw": g.p_kw,
            "relay-main.q_kvar": g.q_kvar,
            "relay-main.v_ll": g.v_pu * NOMINAL_V,
            "relay-main.i_a": g.amps,
            "relay-main.freq_hz": 60.0 if e["msb"] else 0.0,
            "relay-main.demand_kw": self.meter.last_demand_kw,
            "relay-main.breaker_closed": self.on("relay-main.close") and not g.tripped,
            "relay-main.tripped": g.tripped,
            "msb.boiler_closed": self.on("msb.boiler_close"),
            "msb.pkg_closed": self.on("msb.pkg_close"),
            "msb.rtu_closed": self.on("msb.rtu_close"),
            "pv-inverter.p_kw": g.pv_kw,
            "pv-inverter.limit_pct": min(
                self.num("pv-inverter.site_limit_pct"),
                self.num("pv-inverter.utility_limit_pct"),
            ),
            "pv-inverter.connected": g.pv_connected,
            "bess-pcs.p_kw": g.bess_kw,
            "bess-pcs.soc_pct": g.soc_pct,
            "bess-pcs.connected": g.bess_connected,
            "bess-pcs.fault": False,
            "gen-ats.gen_kw": g.ep1_kw if g.on_emergency and g.gen_running else 0.0,
            "gen-ats.ep1_kw": g.ep1_kw,
            "gen-ats.fuel_pct": g.fuel_pct,
            "gen-ats.gen_running": g.gen_running,
            "gen-ats.on_emergency": g.on_emergency,
            "gen-ats.utility_ok": g.utility_ok,
        }
        if tag not in values:
            raise KeyError(f"no model value for {pub.key}")
        return values[tag]

    def output_value(self, name: str) -> Value:
        """Bus states, service import, demand, bill, energy."""
        if name.startswith("bus.") and name.endswith(".energized"):
            return self.energized.get(name.split(".")[1], False)
        match name:
            case "grid.kw":
                return self.grid.p_kw
            case "demand.kw":
                return self.meter.running_demand_kw(self.t)
            case "demand.peak_kw":
                return self.meter.run_peak_kw
            case "tariff.cost_usd":
                return self.meter.month_to_date_usd
            case "energy.kwh":
                return self.meter.total_kwh
        raise KeyError(f"no output {name}")

    def truth(self) -> dict[str, Any]:
        """Published values plus the breaker, ATS, and tariff state."""
        doc = super().truth()
        doc["power"] = {
            "utility_ok": self.grid.utility_ok,
            "energized": dict(self.energized),
            "msb_v_pu": self.grid.v_pu,
            "month_peak_kw": self.meter.month_peak_kw,
            "month_to_date_usd": self.meter.month_to_date_usd,
        }
        return doc
