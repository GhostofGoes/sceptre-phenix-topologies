"""HELICS federates of the CherryMountain simulation (DESIGN.md §9.1, §9.2).

Every federate publishes and subscribes exactly the keys plantgen lists in
``plant/generated/<variant>/helics.json``. The ot-sim ``io`` module on each
field host subscribes ``<federate>/<device>.<point>`` and sends writes (PLC
and BAS commands) to the ``<federate>/updates`` endpoint as a JSON list of
``{"tag": "<device>.<point>", "value": ...}``. Scenario events from the
sim-ctl event API arrive on the same endpoint as ``{"event": {...}}``.

:class:`Federate` holds the transport-free logic (inputs, writes, events,
stepping, publication values) and has no HELICS dependency, so every model is
unit tested directly. :func:`run` wraps any federate in a HELICS combination
federate; ``helics`` is imported only there.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cherrymountain_sim.cellar import Cellar, Environment, InitialState, Sensor
from cherrymountain_sim.timebase import DEFAULT_COMPRESSION, TimeBase

LOG: logging.Logger = logging.getLogger("cherrymountain_sim")
PA_PER_PSI: float = 6894.757
#: Shared cellar equipment with simple command feedback.
SHARED_TYPES: frozenset[str] = frozenset({"hop-doser", "transfer-panel", "gas-relay"})
#: Device types modeled as fermenters.
FERMENTER_TYPES: frozenset[str] = frozenset({"fv-zone", "fv-vessel"})
#: The bas-vent gas-detection relay, an input from the building federate.
CO2_RELAY_INPUT: str = "building/vent.co2_relay_ok"
#: Measurement noise (standard deviation) per point, when noise is enabled.
NOISE_SIGMA: dict[str, float] = {
    "temp_f": 0.02,
    "pressure_psia": 0.01,
    "gravity_plato": 0.02,
}
#: Subscription key -> (Environment field, multiplier).
INPUT_BINDINGS: dict[str, tuple[str, float]] = {
    "utilities/glycol.supply_f": ("glycol_supply_f", 1.0),
    "building/cellar.temp_f": ("ambient_f", 1.0),
    "weather/pressure.kpa": ("ambient_psia", 1000.0 / PA_PER_PSI),
}
#: Federate name -> "module:Class" (imported lazily, see federate_class).
FEDERATES: dict[str, str] = {
    "cellar": "cherrymountain_sim.federate:CellarFederate",
    "brewhouse": "cherrymountain_sim.brewhouse:BrewhouseFederate",
    "utilities": "cherrymountain_sim.utilities:UtilitiesFederate",
    "packaging": "cherrymountain_sim.packaging:PackagingFederate",
    "power": "cherrymountain_sim.power:PowerFederate",
    "building": "cherrymountain_sim.building:BuildingFederate",
    "weather": "cherrymountain_sim.weather:WeatherFederate",
}

type Value = float | bool | str


class Publication(BaseModel):
    """One publication from plantgen's helics.json."""

    key: str
    type: Literal["double", "boolean", "string"]
    units: str | None = None
    tag: str | None = None
    unit: str | None = None
    device: str | None = None
    device_type: str | None = None
    zone: str | None = None
    point: str | None = None
    output: str | None = None


class Subscription(BaseModel):
    """One subscription from plantgen's helics.json."""

    key: str
    type: Literal["double", "boolean", "string"]
    units: str | None = None


class LoadConfig(BaseModel):
    """An electrical load the power federate subscribes to."""

    name: str
    bus: str
    rated_kw: float
    key: str
    desc: str = ""


class SimSettings(BaseModel):
    """Cellar model settings for a variant."""

    compression: float = DEFAULT_COMPRESSION
    noise: bool = True
    seed: int = 0
    initial_state: dict[str, InitialState] = Field(default_factory=dict)


class FederateConfig(BaseModel):
    """One federate entry of helics.json.

    ``sim`` is federate specific: the cellar's is :class:`SimSettings`, the
    others parse it into their own settings model.
    """

    host: str
    period_s: float = 1.0
    endpoint: str
    accepted_updates: list[str]
    publications: list[Publication]
    subscriptions: list[Subscription] = Field(default_factory=list)
    input_defaults: dict[str, float] = Field(default_factory=dict)
    ambient_psia: float = 12.1
    loads: list[LoadConfig] = Field(default_factory=list)
    sim: dict[str, Any] = Field(default_factory=dict)


class BrokerConfig(BaseModel):
    """HELICS broker location."""

    host: str
    interface: str
    address: str
    core_type: str = "zmq"


class ClockConfig(BaseModel):
    """Simulation clock (DESIGN.md §9.4)."""

    start: str
    timezone: str = "America/Denver"
    speedup: float = 1.0


class GroundTruthConfig(BaseModel):
    """In-experiment OpenSearch for ground truth (DESIGN.md §9.5)."""

    endpoint: str
    index_base: str = "cherrymountain-truth"


class IoFederateConfig(BaseModel):
    """An ot-sim I/O federate (for the federate count and tests)."""

    federate_name: str
    subscriptions: list[str]
    endpoint: str | None = None
    sends: list[dict[str, str]] = Field(default_factory=list)


class HelicsConfig(BaseModel):
    """The parts of plantgen's helics.json the federates use."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    variant: str
    broker: BrokerConfig
    end_time_s: float = 36000.0
    clock: ClockConfig | None = None
    ground_truth: GroundTruthConfig | None = None
    federates: dict[str, FederateConfig]
    io_federates: dict[str, IoFederateConfig] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> HelicsConfig:
        """Read and validate helics.json."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


#: Default clock when helics.json has none: a summer weekday, 04:30 MDT.
DEFAULT_START: str = "2026-07-15T04:30:00-06:00"


class SimClock(BaseModel):
    """Maps federate seconds to the simulated wall-clock date and time."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    start: dt.datetime
    speedup: float = 1.0

    @classmethod
    def of(cls, cfg: ClockConfig | None) -> SimClock:
        """The clock from helics.json (or the design default)."""
        if cfg is None:
            return cls(start=dt.datetime.fromisoformat(DEFAULT_START))
        return cls(start=dt.datetime.fromisoformat(cfg.start), speedup=cfg.speedup)

    def at(self, t_s: float) -> dt.datetime:
        """Simulated local date and time ``t_s`` federate seconds after start."""
        return self.start + dt.timedelta(seconds=t_s * self.speedup)


class ScenarioEvent(BaseModel):
    """A scenario event from the sim-ctl event API (DESIGN.md §9.5)."""

    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    id: str = ""


class Federate:
    """Transport-free federate: inputs, writes, events, step, publications.

    Subclasses set :attr:`name` and implement :meth:`advance`,
    :meth:`point_value`, :meth:`output_value`, and, when they accept writes or
    events, :meth:`on_write` and :meth:`on_event`. Writes are recorded in
    :attr:`commands` (``"<device>.<point>" -> value``); :attr:`external`
    counts them and :attr:`last_write_t` stamps the latest. Models hand
    control from their built-in operator (autopilot) to the PLC while
    :attr:`plc_driving` is true, and take it back once the writes stop for
    :attr:`HANDOFF_TIMEOUT_S` (a stopped or disconnected PLC, or a one-off
    write from another host). This assumes a running PLC rewrites its outputs
    at least that often (OpenPLC's Modbus master writes each polling cycle,
    and ot-sim forwards each write); not yet verified on a VM.
    """

    name: ClassVar[str] = ""
    #: Federate seconds without a write before the autopilot takes back control.
    HANDOFF_TIMEOUT_S: ClassVar[float] = 300.0
    cfg: FederateConfig
    clock: SimClock
    t: float
    inputs: dict[str, Value]
    commands: dict[str, Value]
    devices: dict[str, Publication]
    external: int
    last_write_t: float | None
    events: list[ScenarioEvent]
    outbox: list[tuple[str, str]]
    peers: dict[str, str]

    def __init__(self, cfg: FederateConfig, clock: SimClock | None = None) -> None:
        self.cfg = cfg
        self.clock = clock or SimClock.of(None)
        self.t = 0.0
        self.inputs = dict(cfg.input_defaults)
        self.commands = {}
        self.devices = {}
        for pub in cfg.publications:
            if pub.device:
                self.devices.setdefault(pub.device, pub)
        self.external = 0
        self.last_write_t = None
        self.events = []
        self.outbox = []
        self.peers = {}  # other federates' endpoints, set by build()

    # -- inputs, writes, events --------------------------------------------------------

    def apply_input(self, key: str, value: Value) -> None:
        """Apply a subscription value."""
        self.inputs[key] = value

    def input(self, key: str, default: float = 0.0) -> float:
        """A numeric input (subscription or default)."""
        value = self.inputs.get(key, default)
        return float(value) if not isinstance(value, str) else default

    def handle_message(self, payload: str) -> list[str]:
        """Apply an endpoint message: ot-sim writes or a scenario event."""
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            LOG.warning("%s: dropping malformed message %r", self.name, payload)
            return []
        if isinstance(data, dict) and "event" in data:
            try:
                event = ScenarioEvent.model_validate(data["event"])
            except ValidationError as exc:
                LOG.warning("%s: dropping malformed event %r: %s", self.name, payload, exc)
                return []
            return [f"event:{event.type}"] if self.dispatch_event(event) else []
        applied = []
        for upd in data if isinstance(data, list) else [data]:
            try:
                tag = str(upd.get("tag", ""))
                if tag not in self.cfg.accepted_updates:
                    LOG.warning("%s: rejecting write to %s", self.name, tag)
                    continue
                self.write(tag, upd.get("value"))
            except Exception:
                # One bad write must not kill the federate or the writes after it.
                LOG.exception("%s: dropping bad write %r", self.name, upd)
                continue
            applied.append(tag)
        return applied

    def dispatch_event(self, event: ScenarioEvent) -> bool:
        """Apply a scenario event; True when applied.

        A bad event (a value :meth:`on_event` cannot use) is logged with its
        traceback and dropped, so it cannot stop the federate.
        """
        try:
            self.on_event(event)
        except Exception:
            LOG.exception("%s: dropping event %s %s", self.name, event.type, event.params)
            return False
        self.events.append(event)
        return True

    def write(self, tag: str, value: Any) -> None:
        """Apply one write to ``<device>.<point>``."""
        device, _, point = tag.partition(".")
        if device not in self.devices:
            raise KeyError(f"no device {device}")
        self.commands[tag] = value
        self.external += 1
        self.last_write_t = self.t
        self.on_write(device, point, value)

    @property
    def plc_driving(self) -> bool:
        """True while writes keep arriving (within :attr:`HANDOFF_TIMEOUT_S`)."""
        return (
            self.last_write_t is not None
            and self.t - self.last_write_t <= self.HANDOFF_TIMEOUT_S
        )

    def command(self, tag: str, default: Value) -> Value:
        """The last value written to ``tag`` (or ``default``)."""
        return self.commands.get(tag, default)

    def on_write(self, device: str, point: str, value: Any) -> None:
        """React to a write (default: nothing beyond recording it)."""

    def on_event(self, event: ScenarioEvent) -> None:
        """React to a scenario event (default: log it)."""
        LOG.info("%s: event %s %s", self.name, event.type, event.params)

    # -- time and values ---------------------------------------------------------------

    @property
    def now(self) -> dt.datetime:
        """Simulated local date and time."""
        return self.clock.at(self.t)

    def step(self, wall_dt_s: float) -> None:
        """Advance ``wall_dt_s`` federate seconds."""
        self.advance(wall_dt_s)
        self.t += wall_dt_s

    def advance(self, wall_dt_s: float) -> None:
        """Advance the models (subclasses)."""
        raise NotImplementedError

    def point_value(self, pub: Publication) -> Value:
        """Value of one device point (subclasses)."""
        raise KeyError(f"no model value for {pub.key}")

    def output_value(self, name: str) -> Value:
        """Value of one non-device output (subclasses)."""
        raise KeyError(f"no output {name}")

    def value(self, pub: Publication) -> Value:
        """Current value for one publication."""
        if pub.output is not None:
            return self.output_value(pub.output)
        return self.point_value(pub)

    def values(self) -> dict[str, Value]:
        """Every publication key and its current value."""
        return {p.key: self.value(p) for p in self.cfg.publications}

    def truth(self) -> dict[str, Any]:
        """Ground-truth record: every published value (subclasses add state)."""
        return {"values": self.values()}


class CellarFederate(Federate):
    """Cellar: fermenters (and, in the full plant, bright beer tanks)."""

    name: ClassVar[str] = "cellar"
    cellar: Cellar
    sensor: Sensor
    settings: SimSettings

    def __init__(self, cfg: FederateConfig, clock: SimClock | None = None) -> None:
        super().__init__(cfg, clock)
        self.settings = SimSettings.model_validate(cfg.sim)
        env = Environment(ambient_psia=cfg.ambient_psia)
        for key, value in cfg.input_defaults.items():
            self._apply(env, key, value)
        units: list[str] = []
        bbts: list[str] = []
        for pub in cfg.publications:
            if pub.device_type in FERMENTER_TYPES and pub.unit not in units:
                units.append(pub.unit or "")
            if pub.device_type == "bbt" and pub.unit not in bbts:
                bbts.append(pub.unit or "")
        self.cellar = Cellar(
            units,
            tb=TimeBase(compression=self.settings.compression),
            env=env,
            initial=self.settings.initial_state,
            bbts=bbts,
        )
        self.sensor = Sensor(self.settings.seed, self.settings.noise)

    @staticmethod
    def _apply(env: Environment, key: str, value: float) -> None:
        if key in INPUT_BINDINGS:
            attr, scale = INPUT_BINDINGS[key]
            setattr(env, attr, value * scale)
        elif key == "brewhouse/knockout.gpm":
            env.knockout_gpm = value
        elif key == "packaging/beer.draw_gpm":
            env.beer_draw_gpm = value
        elif key == CO2_RELAY_INPUT:
            pass  # the gas-relay point reads it back from self.inputs
        else:
            LOG.warning("ignoring unknown input %s", key)

    def apply_input(self, key: str, value: Value) -> None:
        """Apply a subscription value (e.g. glycol supply temperature)."""
        super().apply_input(key, value)
        self._apply(self.cellar.env, key, float(value))

    def on_write(self, device: str, point: str, value: Any) -> None:
        """Apply one write to ``<device>.<point>``."""
        pub = self.devices[device]
        if pub.device_type == "bbt":
            self.cellar.bbts[pub.unit or ""].write(point, value)
            return
        if pub.device_type in SHARED_TYPES:
            return  # recorded in self.commands; point_value reads it back
        fv = self.cellar.fermenters.get(pub.unit or "")
        if fv is None:
            raise KeyError(f"no writable point {device}.{point}")
        if pub.device_type == "fv-zone" and point == "valve":
            fv.set_valve(pub.zone or "side", bool(value))
        elif pub.device_type == "fv-vessel" and point == "spund_sp_psia":
            fv.spund_sp_psia = float(value)
        else:
            raise KeyError(f"no writable point {device}.{point}")

    def on_event(self, event: ScenarioEvent) -> None:
        """``co2-release``: a leak at a fermenter (DESIGN.md §15.6)."""
        super().on_event(event)
        if event.type == "co2-release":
            unit = str(event.params.get("fv", "FV-102")).replace("-", "")
            rate = event.params.get("rate", "high")
            gps = {"low": 2.0, "medium": 10.0, "high": 40.0}.get(str(rate), None)
            gps = float(rate) if gps is None else gps
            seconds = float(event.params.get("duration", 600.0))
            self.cellar.add_leak(unit, gps, seconds)

    def advance(self, wall_dt_s: float) -> None:
        """Advance the cellar model."""
        self.cellar.step(wall_dt_s)

    def output_value(self, name: str) -> Value:
        """Federate outputs: CO2 vented, glycol load, CO2 drawn."""
        if name == "co2.vent_gps":
            return self.cellar.co2_vent_gps
        if name == "glycol.load_kw":
            return self.cellar.glycol_load_kw
        if name == "co2.draw_slpm":
            return self.cellar.co2_draw_slpm
        raise KeyError(f"no output {name}")

    def point_value(self, pub: Publication) -> Value:
        """Current value for one device point (with measurement noise)."""
        if pub.device_type == "sim-clock":
            return self.cellar.sim_hours
        point = pub.point or ""
        if pub.device_type in SHARED_TYPES:
            return self._shared(pub.device or "", point)
        if pub.device_type == "bbt":
            bbt = self.cellar.bbts[pub.unit or ""]
            raw_bbt = bbt.read(point)
            if isinstance(raw_bbt, bool):
                return raw_bbt
            return max(self.sensor.read(raw_bbt, NOISE_SIGMA.get(point, 0.0)), 0.0)
        fv = self.cellar.fermenters[pub.unit or ""]
        raw: float | bool
        match (pub.device_type, point):
            case ("fv-zone", "temp_f"):
                raw = fv.zone_temp_f(pub.zone or "side")
            case ("fv-zone", "valve"):
                return fv.valve(pub.zone or "side")
            case ("fv-vessel", "pressure_psia"):
                raw = fv.pressure_psia
            case ("fv-vessel", "gravity_plato"):
                raw = fv.gravity_plato
            case ("fv-vessel", "spund_sp_psia"):
                return fv.spund_sp_psia
            case ("fv-vessel", "spund_open"):
                return fv.spund_open
            case _:
                raise KeyError(f"no model value for {pub.key}")
        noisy = self.sensor.read(float(raw), NOISE_SIGMA.get(point, 0.0))
        return max(noisy, 0.0)  # registers are unsigned

    def _shared(self, device: str, point: str) -> Value:
        """Dry-hop doser, transfer panel, and gas relay: commands and feedback.

        The hop port is closed unless a dose is in progress; the transfer
        route is verified (swing panel in place) and the pump moves 30 gal/min
        when it runs.
        """
        cmd = self.commands
        match point:
            case "co2_ok":
                # The bas-vent relay contact, hardwired into rio-cellar
                # (DESIGN.md §6.7): healthy only while the building federate
                # reports it energized; open until it first does.
                return self.input(CO2_RELAY_INPUT, 0.0) >= 0.5
            case "port_closed":
                return not bool(cmd.get(f"{device}.dose_valve", False))
            case "route_ok":
                return True
            case "pump_running":
                return bool(cmd.get(f"{device}.pump_run", False))
            case "flow_gpm":
                return 30.0 if cmd.get(f"{device}.pump_run", False) else 0.0
        return bool(cmd.get(f"{device}.{point}", False))

    def truth(self) -> dict[str, Any]:
        """Published values plus each fermenter's kinetic state."""
        doc = super().truth()
        doc["sim_hours"] = self.cellar.sim_hours
        doc["fermenters"] = {
            unit: {
                "gravity_plato": fv.gravity_plato,
                "abv": fv.cfg.wort.abv(fv.state.ethanol) if fv.state else 0.0,
                "diacetyl": fv.state.diacetyl if fv.state else 0.0,
                "co2_generated_g": fv.co2_generated_g,
            }
            for unit, fv in self.cellar.fermenters.items()
        }
        return doc


def federate_class(name: str) -> type[Federate]:
    """The federate class for a federate name (imported lazily)."""
    target = FEDERATES.get(name)
    if target is None:
        raise KeyError(f"no federate named {name}; have {sorted(FEDERATES)}")
    module, _, cls = target.partition(":")
    found: type[Federate] = getattr(importlib.import_module(module), cls)
    return found


def build(hcfg: HelicsConfig, name: str) -> Federate:
    """Instantiate the named federate from a helics.json config."""
    fed = federate_class(name)(hcfg.federates[name], SimClock.of(hcfg.clock))
    fed.peers = {n: f.endpoint for n, f in hcfg.federates.items() if n != name}
    return fed


def run(
    config: Path,
    name: str = "cellar",
    broker_address: str | None = None,
    duration_s: float | None = None,
) -> None:
    """Run a federate under HELICS (real-time mode) until end time."""
    import helics as h  # noqa: PLC0415 - optional dependency, imported lazily

    from cherrymountain_sim.groundtruth import TruthWriter  # noqa: PLC0415

    hcfg = HelicsConfig.load(config)
    fcfg = hcfg.federates[name]
    logic = build(hcfg, name)
    start_api = getattr(logic, "start_api", None)
    if start_api is not None and logic.cfg.sim.get("event_api", True):
        start_api(hcfg.variant)
    end = min(hcfg.end_time_s, duration_s or hcfg.end_time_s)
    truth = TruthWriter.of(hcfg.ground_truth, name, hcfg.variant)

    fi = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreTypeFromString(fi, hcfg.broker.core_type)
    addr = broker_address or hcfg.broker.address
    h.helicsFederateInfoSetCoreInitString(fi, f"--federates=1 --broker_address={addr}")
    h.helicsFederateInfoSetTimeProperty(
        fi, h.HELICS_PROPERTY_TIME_PERIOD, fcfg.period_s
    )
    h.helicsFederateInfoSetFlagOption(fi, h.HELICS_FLAG_REALTIME, True)
    h.helicsFederateInfoSetTimeProperty(fi, h.HELICS_PROPERTY_TIME_RT_LAG, 1.0)
    h.helicsFederateInfoSetTimeProperty(fi, h.HELICS_PROPERTY_TIME_RT_LEAD, 1.0)
    fed = h.helicsCreateCombinationFederate(name, fi)

    pubs = {
        p.key: (
            h.helicsFederateRegisterGlobalTypePublication(
                fed, p.key, p.type, p.units or ""
            ),
            p,
        )
        for p in fcfg.publications
    }
    subs = {
        s.key: (h.helicsFederateRegisterSubscription(fed, s.key, s.units or ""), s)
        for s in fcfg.subscriptions
    }
    ep = h.helicsFederateRegisterGlobalEndpoint(fed, fcfg.endpoint, "")
    h.helicsFederateEnterExecutingMode(fed)
    LOG.info("%s federate executing: %d pubs, %d subs", name, len(pubs), len(subs))
    t = 0.0
    try:
        while t < end:
            t = h.helicsFederateRequestTime(fed, t + fcfg.period_s)
            for key, (sub, spec) in subs.items():
                if h.helicsInputIsUpdated(sub):
                    if spec.type == "boolean":
                        logic.apply_input(key, h.helicsInputGetBoolean(sub))
                    elif spec.type == "string":
                        logic.apply_input(key, h.helicsInputGetString(sub))
                    else:
                        logic.apply_input(key, h.helicsInputGetDouble(sub))
            while h.helicsEndpointHasMessage(ep):
                msg = h.helicsEndpointGetMessage(ep)
                try:
                    logic.handle_message(h.helicsMessageGetString(msg))
                except Exception:
                    LOG.exception("%s: dropping a message that could not be handled", name)
            logic.step(fcfg.period_s)
            for dest, payload in logic.outbox:
                h.helicsEndpointSendBytesTo(ep, payload.encode(), dest)
            logic.outbox.clear()
            for pub, spec in pubs.values():
                value = logic.value(spec)
                if spec.type == "boolean":
                    h.helicsPublicationPublishBoolean(pub, bool(value))
                elif spec.type == "string":
                    h.helicsPublicationPublishString(pub, str(value))
                else:
                    h.helicsPublicationPublishDouble(pub, float(value))
            truth.record(logic)
    finally:
        truth.close()
        close_api = getattr(getattr(logic, "api", None), "close", None)
        if close_api is not None:
            close_api()
        h.helicsFederateDisconnect(fed)
        h.helicsFederateFree(fed)
        h.helicsCloseLibrary()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m cherrymountain_sim`` and the console scripts."""
    ap = argparse.ArgumentParser(prog="cherrymountain-sim", description=__doc__)
    ap.add_argument("--config", type=Path, required=True, help="plantgen helics.json")
    ap.add_argument("--federate", default="cellar", choices=sorted(FEDERATES))
    ap.add_argument("--broker-address", help="override the broker address")
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(name)s %(message)s")
    run(args.config, args.federate, args.broker_address, args.duration)
    return 0
