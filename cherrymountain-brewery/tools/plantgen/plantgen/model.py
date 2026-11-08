"""Validate ``plant.yaml`` with Pydantic and expand it for one variant.

Two layers of models:

- **Schema models** (``PlantFile`` and friends) mirror ``plant.yaml`` and
  reject unknown keys, bad types, and malformed IDs on load.
- **Expanded records** (``Device``, ``Tag``, ``Alarm``, ``Plant``) are the
  per-variant view the allocator and renderers use.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

type TagType = Literal[
    "analog-read", "analog-read-write", "binary-read", "binary-read-write"
]
type Priority = Literal["critical", "high", "medium", "low"]
type AlarmKind = Literal["deviation", "high", "low"]
type HostRole = Literal["sim", "rio", "plc", "scada", "service"]

TAG_TYPES: tuple[str, ...] = (
    "analog-read",
    "analog-read-write",
    "binary-read",
    "binary-read-write",
)
PRIORITIES: tuple[str, ...] = ("critical", "high", "medium", "low")
ALARM_KINDS: tuple[str, ...] = ("deviation", "high", "low")
RATIONALE_FIELDS: tuple[str, ...] = (
    "priority",
    "cause",
    "consequence",
    "action",
    "response_time",
)
MAX_REGISTER: int = 65535

_TAG_ID_RE: re.Pattern[str] = re.compile(r"^[A-Z]+[0-9]{2}\.[A-Z_]+$")
_DURATION_RE: re.Pattern[str] = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|m|h)$")
_DURATION_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600}


class PlantError(ValueError):
    """Raised for an invalid plant database."""


def parse_duration(text: str | int | float) -> float:
    """Parse ``30s``, ``15m``, or ``1h`` into seconds."""
    if isinstance(text, int | float):
        return float(text)
    m = _DURATION_RE.match(str(text).strip())
    if not m:
        raise PlantError(f"bad duration {text!r} (use e.g. 30s, 15m, 1h)")
    return float(m.group(1)) * _DURATION_UNITS[m.group(2)]


def is_analog(tag_type: str) -> bool:
    """True for analog tag types."""
    return tag_type.startswith("analog")


def is_writable(tag_type: str) -> bool:
    """True for read-write tag types."""
    return tag_type.endswith("read-write")


# ---------------------------------------------------------------------------
# Schema models (plant.yaml)
# ---------------------------------------------------------------------------


class Strict(BaseModel):
    """Base for schema models: unknown keys are errors."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class Site(Strict):
    """Site constants."""

    name: str
    elevation_ft: float
    ambient_psia: float


class FederateOutput(Strict):
    """A federate publication that is not a remote I/O point."""

    key: str
    type: Literal["double", "boolean"]
    units: str | None = None
    desc: str = ""


class FederateInput(Strict):
    """A subscription to another federate, with a default when it is absent."""

    key: str
    type: Literal["double", "boolean"]
    units: str | None = None
    default: float


class FederateSpec(Strict):
    """A HELICS federate run by cherrymountain_sim."""

    host: str
    period_s: float = 1.0
    endpoint: str = "updates"
    outputs: list[FederateOutput] = Field(default_factory=list)
    inputs: list[FederateInput] = Field(default_factory=list)


class PointSpec(Strict):
    """One point of an ot-sim device type."""

    type: TagType
    scaling: int | None = None


class Interface(Strict):
    """A host NIC: VLAN alias and CIDR address."""

    vlan: str
    ip: str


class ModbusEndpoint(Strict):
    """Where a host serves Modbus TCP."""

    interface: str = "IF0"
    port: int = 502
    unit_id: int = 1


class HostSpec(Strict):
    """A topology node relevant to plantgen."""

    role: HostRole
    interfaces: dict[str, Interface] = Field(default_factory=dict)
    federate: str | None = None
    modbus: ModbusEndpoint | None = None
    poll_ms: int = 100


class Rationale(Strict):
    """ISA-18.2 rationalization fields (all optional so alarms can override)."""

    priority: Priority | None = None
    cause: str | None = None
    consequence: str | None = None
    action: str | None = None
    response_time: str | float | None = None


class AlarmSpec(Rationale):
    """One alarm on a tag."""

    kind: AlarmKind
    limit: float | None = None
    delay: str | float = 0.0
    setpoint: str | None = None
    rationale: str | None = None


class TagSpec(Strict):
    """One tag as written in plant.yaml (``<INSTR>.<ATTR>``)."""

    id: str
    desc: str = ""
    io: str | None = None
    type: TagType | None = None
    units: str | None = None
    range: tuple[float, float] | None = None
    scaling: int | None = None
    alarms: list[AlarmSpec] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _TAG_ID_RE.match(v):
            raise ValueError(f"tag id {v!r} must look like TT01.PV")
        return v


class DeviceSpec(Strict):
    """An ot-sim device on the unit's remote I/O."""

    name: str
    type: str
    zone: Literal["side", "cone"] | None = None


class UnitSpec(Strict):
    """An ISA-88 unit (equipment) with its devices and tags."""

    id: str
    name: str
    model: str
    devices: list[DeviceSpec] = Field(default_factory=list)
    tags: list[TagSpec] = Field(default_factory=list)


class AreaSpec(Strict):
    """A process area and the hosts that own it."""

    id: str
    name: str
    plc: str
    rio: str
    federate: str
    units: list[UnitSpec]


class BrokerSpec(Strict):
    """HELICS broker location."""

    hostname: str
    interface: str


class VariantHelics(Strict):
    """Per-variant HELICS settings."""

    broker: BrokerSpec
    end_time_s: int = 36000


class InitialStateSpec(Strict):
    """Initial state of one vessel (mirrors cherrymountain_sim.InitialState)."""

    phase: Literal["empty", "fermenting"] = "empty"
    sim_hours: float = 0.0
    temp_f: float = 66.0
    dry_hop_lb_per_bbl: float = 0.0
    spund_sp_psig: float | None = None


class SimSpec(Strict):
    """Cellar federate settings for a variant."""

    compression: float = 48.0
    noise: bool = True
    seed: int = 0
    initial_state: dict[str, InitialStateSpec] = Field(default_factory=dict)


class NtpSource(Strict):
    """An NTP source host and interface."""

    hostname: str
    interface: str


class NtpHost(Strict):
    """ntp app per-host metadata."""

    server: str | None = None
    client: str | None = None
    source: NtpSource | None = None


class NtpSpec(Strict):
    """ntp app (app mode) for a variant."""

    default_source: NtpSource
    hosts: dict[str, NtpHost]


class VariantSpec(Strict):
    """A topology variant."""

    description: str = ""
    areas: list[str]
    federates: list[str]
    hosts: list[str]
    host_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    helics: VariantHelics
    ground_truth: bool = True
    sim: SimSpec = Field(default_factory=SimSpec)
    ntp: NtpSpec


class PlantFile(Strict):
    """Top level of plant.yaml."""

    schema_version: Literal[1]
    site: Site
    federates: dict[str, FederateSpec]
    device_types: dict[str, dict[str, PointSpec]]
    hosts: dict[str, HostSpec]
    alarm_rationale: dict[str, Rationale] = Field(default_factory=dict)
    areas: list[AreaSpec]
    variants: dict[str, VariantSpec]


# ---------------------------------------------------------------------------
# Expanded records
# ---------------------------------------------------------------------------


class Point(BaseModel):
    """A named point of a device type."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    type: TagType
    scaling: int | None = None


class Device(BaseModel):
    """One ot-sim infrastructure device on a remote I/O host."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str  # without federate prefix, e.g. fv101-side
    type: str
    federate: str
    rio: str
    area: str
    unit: str
    zone: str | None = None

    @property
    def topic(self) -> str:
        """HELICS topic, ``<federate>/<device>``."""
        return f"{self.federate}/{self.name}"


class Alarm(BaseModel):
    """A rationalized alarm (ISA-18.2) and the PLC bit that carries it."""

    name: str  # alarm bit tag name
    source: str  # monitored tag name
    kind: AlarmKind
    limit: float
    delay_s: float
    priority: str
    cause: str
    consequence: str
    action: str
    response_time_s: float
    setpoint: str | None = None


class Tag(BaseModel):
    """One plant tag, fully expanded."""

    name: str
    area: str
    unit: str
    instrument: str
    attr: str
    desc: str
    type: TagType
    plc: str
    units: str | None = None
    range: tuple[float, float] | None = None
    scaling: int = 0
    device: Device | None = None
    point: str | None = None
    rio: str | None = None
    federate: str | None = None
    alarms: list[Alarm] = Field(default_factory=list)
    generated: bool = False  # alarm bit tags are generated

    @property
    def source(self) -> str:
        """``io`` for remote-I/O tags, ``plc`` for PLC memory."""
        return "io" if self.device else "plc"

    @property
    def helics_key(self) -> str | None:
        """Global HELICS key, e.g. ``cellar/fv101-side.temp_f``."""
        return f"{self.device.topic}.{self.point}" if self.device else None


class Host(HostSpec):
    """A variant host (overrides applied)."""

    name: str

    def ip(self, interface: str) -> str:
        """Address (without prefix length) on ``interface``."""
        try:
            return self.interfaces[interface].ip.split("/")[0]
        except KeyError as exc:
            raise PlantError(f"{self.name} has no interface {interface}") from exc

    def vlans(self) -> set[str]:
        """VLAN aliases this host is attached to."""
        return {i.vlan for i in self.interfaces.values()}


class Plant(BaseModel):
    """The plant database expanded for one variant."""

    variant: str
    file: PlantFile
    spec: VariantSpec
    hosts: dict[str, Host]
    device_types: dict[str, dict[str, Point]]
    devices: list[Device]
    tags: list[Tag]
    federates: dict[str, FederateSpec]

    @property
    def alarms(self) -> list[Alarm]:
        """All alarms in tag order."""
        return [a for t in self.tags for a in t.alarms]

    def tag(self, name: str) -> Tag:
        """Look up a tag by full name."""
        for t in self.tags:
            if t.name == name:
                return t
        raise KeyError(name)

    def hosts_by_role(self, role: str) -> list[Host]:
        """Variant hosts with ``role``, in variant order."""
        return [h for h in self.hosts.values() if h.role == role]


# ---------------------------------------------------------------------------
# Loading and expansion
# ---------------------------------------------------------------------------


def load_file(path: Path) -> PlantFile:
    """Read and validate ``plant.yaml``."""
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    try:
        return PlantFile.model_validate(data)
    except ValidationError as exc:
        raise PlantError(f"{path}: {exc}") from exc


def load(path: Path, variant: str) -> Plant:
    """Load ``plant.yaml`` and expand it for ``variant``."""
    return expand(load_file(path), variant)


def expand(pf: PlantFile, variant: str) -> Plant:
    """Expand a validated plant file for ``variant``."""
    if variant not in pf.variants:
        raise PlantError(f"unknown variant {variant!r}; have {sorted(pf.variants)}")
    spec = pf.variants[variant]
    hosts = _hosts(pf, spec)
    device_types = {
        name: {
            pn: Point(name=pn, type=p.type, scaling=p.scaling) for pn, p in pts.items()
        }
        for name, pts in pf.device_types.items()
    }
    unknown = [f for f in spec.federates if f not in pf.federates]
    if unknown:
        raise PlantError(f"variant {variant}: unknown federates {unknown}")
    federates = {n: f for n, f in pf.federates.items() if n in spec.federates}
    devices: list[Device] = []
    tags: list[Tag] = []
    for area in pf.areas:
        if area.id not in spec.areas:
            continue
        for unit in area.units:
            unit_devices: dict[str, Device] = {}
            for d in unit.devices:
                if d.type not in device_types:
                    raise PlantError(f"device {d.name}: unknown type {d.type!r}")
                unit_devices[d.name] = Device(
                    name=d.name,
                    type=d.type,
                    federate=area.federate,
                    rio=area.rio,
                    area=area.id,
                    unit=unit.id,
                    zone=d.zone,
                )
            devices.extend(unit_devices.values())
            for ts in unit.tags:
                tag = _tag(area, unit, ts, unit_devices, device_types)
                tag.alarms = [_alarm(tag, a, pf.alarm_rationale) for a in ts.alarms]
                tags.append(tag)
                tags.extend(_alarm_tag(tag, a) for a in tag.alarms)
    return Plant(
        variant=variant,
        file=pf,
        spec=spec,
        hosts=hosts,
        device_types=device_types,
        devices=devices,
        tags=tags,
        federates=federates,
    )


def _hosts(pf: PlantFile, spec: VariantSpec) -> dict[str, Host]:
    out: dict[str, Host] = {}
    for name in spec.hosts:
        if name not in pf.hosts:
            raise PlantError(f"variant host {name!r} is not defined under hosts")
        data = pf.hosts[name].model_dump(exclude_none=True)
        for key, value in copy.deepcopy(spec.host_overrides.get(name, {})).items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key].update(value)
            else:
                data[key] = value
        try:
            out[name] = Host.model_validate({**data, "name": name})
        except ValidationError as exc:
            raise PlantError(f"host {name} (after overrides): {exc}") from exc
    return out


def _tag(
    area: AreaSpec,
    unit: UnitSpec,
    spec: TagSpec,
    devices: dict[str, Device],
    device_types: dict[str, dict[str, Point]],
) -> Tag:
    instrument, attr = spec.id.split(".")
    name = f"{area.id}.{unit.id}.{spec.id}"
    device: Device | None = None
    point: str | None = None
    if spec.io is not None:
        dname, _, point = spec.io.partition(".")
        if dname not in devices:
            raise PlantError(f"{name}: io device {dname!r} not in unit devices")
        device = devices[dname]
        pdef = device_types[device.type].get(point)
        if pdef is None:
            raise PlantError(f"{name}: {device.type} has no point {point!r}")
        if spec.type not in (None, pdef.type):
            raise PlantError(f"{name}: type disagrees with {device.type}.{point}")
        if spec.scaling not in (None, pdef.scaling):
            raise PlantError(f"{name}: scaling disagrees with {device.type}.{point}")
        ttype, scaling = pdef.type, pdef.scaling
    else:
        if spec.type is None:
            raise PlantError(f"{name}: PLC-memory tags need a type")
        ttype, scaling = spec.type, spec.scaling
    if is_analog(ttype) and (scaling is None or spec.range is None):
        raise PlantError(f"{name}: analog tags need a scaling and a range")
    return Tag(
        name=name,
        area=area.id,
        unit=unit.id,
        instrument=instrument,
        attr=attr,
        desc=spec.desc,
        type=ttype,
        plc=area.plc,
        units=spec.units,
        range=spec.range,
        scaling=scaling or 0,
        device=device,
        point=point,
        rio=device.rio if device else None,
        federate=device.federate if device else None,
    )


def _alarm(tag: Tag, spec: AlarmSpec, library: dict[str, Rationale]) -> Alarm:
    merged: dict[str, Any] = {}
    if spec.rationale is not None:
        if spec.rationale not in library:
            raise PlantError(f"{tag.name}: unknown rationale {spec.rationale!r}")
        merged.update(library[spec.rationale].model_dump(exclude_none=True))
    merged.update(spec.model_dump(exclude_none=True, exclude={"rationale"}))
    setpoint = merged.get("setpoint")
    return Alarm(
        name=f"{tag.area}.{tag.unit}.{tag.instrument}.ALM_{spec.kind.upper()}",
        source=tag.name,
        kind=spec.kind,
        limit=float(merged.get("limit", "nan")),
        delay_s=parse_duration(merged.get("delay", 0)),
        priority=str(merged.get("priority", "")),
        cause=str(merged.get("cause", "")).strip(),
        consequence=str(merged.get("consequence", "")).strip(),
        action=str(merged.get("action", "")).strip(),
        response_time_s=parse_duration(merged.get("response_time", 0)),
        setpoint=f"{tag.area}.{tag.unit}.{setpoint}" if setpoint else None,
    )


def _alarm_tag(tag: Tag, alarm: Alarm) -> Tag:
    return Tag(
        name=alarm.name,
        area=tag.area,
        unit=tag.unit,
        instrument=tag.instrument,
        attr=f"ALM_{alarm.kind.upper()}",
        desc=f"{tag.desc}: {alarm.kind} alarm ({alarm.priority})",
        type="binary-read",
        plc=tag.plc,
        generated=True,
    )
