"""Cellar HELICS federate (DESIGN.md §9.1, §9.2).

Publishes and subscribes exactly the keys plantgen lists in
``plant/generated/<variant>/helics.json``. The ot-sim ``io`` module on
rio-cellar subscribes ``cellar/<device>.<point>`` and sends writes (PLC
commands) to the ``cellar/updates`` endpoint as a JSON list of
``{"tag": "<device>.<point>", "value": ...}``.

``CellarFederate`` holds all the logic and has no HELICS dependency, so it
is unit tested directly. ``run`` wraps it in a HELICS combination federate;
``helics`` is imported only there.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from cherrymountain_sim.cellar import Cellar, Environment, InitialState, Sensor
from cherrymountain_sim.timebase import DEFAULT_COMPRESSION, TimeBase

LOG: logging.Logger = logging.getLogger("cherrymountain_sim.cellar")
PA_PER_PSI: float = 6894.757
#: Device types modeled as fermenters.
FERMENTER_TYPES: frozenset[str] = frozenset({"fv-zone", "fv-vessel"})
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


class Publication(BaseModel):
    """One publication from plantgen's helics.json."""

    key: str
    type: Literal["double", "boolean"]
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
    type: Literal["double", "boolean"]
    units: str | None = None


class SimSettings(BaseModel):
    """Cellar model settings for a variant."""

    compression: float = DEFAULT_COMPRESSION
    noise: bool = True
    seed: int = 0
    initial_state: dict[str, InitialState] = Field(default_factory=dict)


class FederateConfig(BaseModel):
    """One federate entry of helics.json."""

    host: str
    period_s: float = 1.0
    endpoint: str
    accepted_updates: list[str]
    publications: list[Publication]
    subscriptions: list[Subscription] = Field(default_factory=list)
    input_defaults: dict[str, float] = Field(default_factory=dict)
    ambient_psia: float = 12.1
    sim: SimSettings = Field(default_factory=SimSettings)


class BrokerConfig(BaseModel):
    """HELICS broker location."""

    host: str
    interface: str
    address: str
    core_type: str = "zmq"


class HelicsConfig(BaseModel):
    """The parts of plantgen's helics.json the federates use."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    variant: str
    broker: BrokerConfig
    end_time_s: float = 36000.0
    federates: dict[str, FederateConfig]

    @classmethod
    def load(cls, path: Path) -> HelicsConfig:
        """Read and validate helics.json."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class CellarFederate:
    """Transport-free cellar federate: inputs, updates, step, publications."""

    cfg: FederateConfig
    cellar: Cellar
    sensor: Sensor
    devices: dict[str, Publication]

    def __init__(self, cfg: FederateConfig) -> None:
        self.cfg = cfg
        env = Environment(ambient_psia=cfg.ambient_psia)
        for key, value in cfg.input_defaults.items():
            self._apply(env, key, value)
        units: list[str] = []
        self.devices = {}
        for pub in cfg.publications:
            if pub.device_type in FERMENTER_TYPES and pub.unit not in units:
                units.append(pub.unit or "")
            if pub.device:
                self.devices.setdefault(pub.device, pub)
        self.cellar = Cellar(
            units,
            tb=TimeBase(compression=cfg.sim.compression),
            env=env,
            initial=cfg.sim.initial_state,
        )
        self.sensor = Sensor(cfg.sim.seed, cfg.sim.noise)

    @staticmethod
    def _apply(env: Environment, key: str, value: float) -> None:
        if key not in INPUT_BINDINGS:
            LOG.warning("ignoring unknown input %s", key)
            return
        attr, scale = INPUT_BINDINGS[key]
        setattr(env, attr, value * scale)

    def apply_input(self, key: str, value: float) -> None:
        """Apply a subscription value (e.g. glycol supply temperature)."""
        self._apply(self.cellar.env, key, value)

    def handle_message(self, payload: str) -> list[str]:
        """Apply an ot-sim update message; returns the tags applied."""
        try:
            updates = json.loads(payload)
        except json.JSONDecodeError:
            LOG.warning("dropping malformed update %r", payload)
            return []
        applied = []
        for upd in updates if isinstance(updates, list) else [updates]:
            tag = str(upd.get("tag", ""))
            if tag not in self.cfg.accepted_updates:
                LOG.warning("rejecting write to %s", tag)
                continue
            self.write(tag, upd.get("value"))
            applied.append(tag)
        return applied

    def write(self, tag: str, value: Any) -> None:
        """Apply one write to ``<device>.<point>``."""
        device, _, point = tag.partition(".")
        pub = self.devices[device]
        fv = self.cellar.fermenters[pub.unit or ""]
        if pub.device_type == "fv-zone" and point == "valve":
            fv.set_valve(pub.zone or "side", bool(value))
        elif pub.device_type == "fv-vessel" and point == "spund_sp_psia":
            fv.spund_sp_psia = float(value)
        else:
            raise KeyError(f"no writable point {tag}")

    def step(self, wall_dt_s: float) -> None:
        """Advance the cellar model."""
        self.cellar.step(wall_dt_s)

    def value(self, pub: Publication) -> float | bool:
        """Current value for one publication (with measurement noise)."""
        if pub.output == "co2.vent_gps":
            return self.cellar.co2_vent_gps
        if pub.output == "glycol.load_kw":
            return self.cellar.glycol_load_kw
        if pub.device_type == "sim-clock":
            return self.cellar.sim_hours
        fv = self.cellar.fermenters[pub.unit or ""]
        point = pub.point or ""
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

    def values(self) -> dict[str, float | bool]:
        """Every publication key and its current value."""
        return {p.key: self.value(p) for p in self.cfg.publications}


def run(
    config: Path,
    name: str = "cellar",
    broker_address: str | None = None,
    duration_s: float | None = None,
) -> None:
    """Run the federate under HELICS (real-time mode) until end time."""
    import helics as h  # noqa: PLC0415 - optional dependency, imported lazily

    hcfg = HelicsConfig.load(config)
    fcfg = hcfg.federates[name]
    logic = CellarFederate(fcfg)
    end = min(hcfg.end_time_s, duration_s or hcfg.end_time_s)

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
        s.key: h.helicsFederateRegisterSubscription(fed, s.key, s.units or "")
        for s in fcfg.subscriptions
    }
    ep = h.helicsFederateRegisterGlobalEndpoint(fed, fcfg.endpoint, "")
    h.helicsFederateEnterExecutingMode(fed)
    LOG.info("cellar federate executing: %d pubs, %d subs", len(pubs), len(subs))
    t = 0.0
    try:
        while t < end:
            t = h.helicsFederateRequestTime(fed, t + fcfg.period_s)
            for key, sub in subs.items():
                if h.helicsInputIsUpdated(sub):
                    logic.apply_input(key, h.helicsInputGetDouble(sub))
            while h.helicsEndpointHasMessage(ep):
                msg = h.helicsEndpointGetMessage(ep)
                logic.handle_message(h.helicsMessageGetString(msg))
            logic.step(fcfg.period_s)
            for pub, spec in pubs.values():
                value = logic.value(spec)
                if spec.type == "boolean":
                    h.helicsPublicationPublishBoolean(pub, bool(value))
                else:
                    h.helicsPublicationPublishDouble(pub, float(value))
    finally:
        h.helicsFederateDisconnect(fed)
        h.helicsFederateFree(fed)
        h.helicsCloseLibrary()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``cherrymountain-cellar`` / ``python -m cherrymountain_sim``."""
    ap = argparse.ArgumentParser(prog="cherrymountain-cellar", description=__doc__)
    ap.add_argument("--config", type=Path, required=True, help="plantgen helics.json")
    ap.add_argument("--federate", default="cellar")
    ap.add_argument("--broker-address", help="override the broker address")
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(name)s %(message)s")
    run(args.config, args.federate, args.broker_address, args.duration)
    return 0
