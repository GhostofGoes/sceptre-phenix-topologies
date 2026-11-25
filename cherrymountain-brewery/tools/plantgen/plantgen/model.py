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
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

type TagType = Literal[
    "analog-read", "analog-read-write", "binary-read", "binary-read-write"
]
type Priority = Literal["critical", "high", "medium", "low"]
type AlarmKind = Literal["deviation", "high", "low", "highhigh", "lowlow", "state"]
type HostRole = Literal[
    "sim",  # cherrymountain_sim federates (MGMT only)
    "rio",  # remote I/O: ot-sim Modbus fd-server, HELICS-backed
    "field",  # other ot-sim Modbus fd-servers (OEM controllers, power devices)
    "bas",  # ot-sim fd-server with the BACnet module (building automation)
    "fep",  # ot-sim front-end processor (rtu-der)
    "dnp3-master",  # ot-sim fd-client (utility-derms)
    "plc",
    "scada",  # FUXA
    "service",
    "router",
    "firewall",
    "hmi",
    "historian",
    "ews",
    "workstation",
    "vpn",
    "jump",
    "bbmd",
    "soc",
]
type Northbound = Literal["modbus", "opcua", "s7comm"]
type BacnetObjectType = Literal[
    "analog-input",
    "analog-output",
    "analog-value",
    "binary-input",
    "binary-output",
    "binary-value",
    "multi-state-value",
]

PRIORITIES: tuple[str, ...] = ("critical", "high", "medium", "low")
ALARM_KINDS: tuple[str, ...] = (
    "deviation",
    "high",
    "low",
    "highhigh",
    "lowlow",
    "state",
)
#: Host roles that run an ot-sim I/O module (HELICS-backed points).
IO_ROLES: tuple[str, ...] = ("rio", "field", "bas")
#: Host roles that serve their points over Modbus TCP.
MODBUS_IO_ROLES: tuple[str, ...] = ("rio", "field")
#: BACnet object type -> (tag types it may carry, BACnet object type number).
BACNET_OBJECTS: dict[str, tuple[tuple[str, ...], int]] = {
    "analog-input": (("analog-read",), 0),
    "analog-output": (("analog-read-write",), 1),
    "analog-value": (("analog-read", "analog-read-write"), 2),
    "binary-input": (("binary-read",), 3),
    "binary-output": (("binary-read-write",), 4),
    "binary-value": (("binary-read", "binary-read-write"), 5),
    "multi-state-value": (("analog-read", "analog-read-write"), 19),
}
INT16_MIN: int = -32768
INT16_MAX: int = 32767
RATIONALE_FIELDS: tuple[str, ...] = (
    "priority",
    "cause",
    "consequence",
    "action",
    "response_time",
)
MAX_REGISTER: int = 65535

#: ``<INSTR>.<ATTR>``: an ISA-5.1 loop (``TT01``) or the PackTags group
#: ``PACKML`` (``PKG.FILLER.PACKML.STATE``, DESIGN.md §6.4).
_TAG_ID_RE: re.Pattern[str] = re.compile(r"^(?:[A-Z]+[0-9]{2}|PACKML)\.[A-Z_0-9]+$")
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
    """A federate publication that is not a remote I/O point.

    ``variants`` limits the output to those variants (default: all).
    """

    key: str
    type: Literal["double", "boolean", "string"]
    units: str | None = None
    desc: str = ""
    variants: list[str] | None = None


class FederateInput(Strict):
    """A subscription to another federate, with a default when it is absent.

    ``variants`` limits the input to those variants (default: all).
    """

    key: str
    type: Literal["double", "boolean", "string"]
    units: str | None = None
    default: float
    variants: list[str] | None = None


class UnitOutput(Strict):
    """A per-unit federate output: one publication ``<federate>/<unit>.<key>``
    for every unit of the variant whose ``model`` is listed (unit ids in lower
    case, ``cellar/fv101.level_pct``). For truth values no transmitter
    measures (a fermenter's fill), so no tag carries them.
    """

    key: str
    type: Literal["double", "boolean"]
    units: str | None = None
    desc: str = ""
    models: list[str]


class FederateSpec(Strict):
    """A HELICS federate run by cherrymountain_sim."""

    host: str
    period_s: float = 1.0
    endpoint: str = "updates"
    outputs: list[FederateOutput] = Field(default_factory=list)
    inputs: list[FederateInput] = Field(default_factory=list)
    unit_outputs: list[UnitOutput] = Field(default_factory=list)


class SnapshotSpec(Strict):
    """The ground-truth snapshot (DESIGN.md §15.10): ``federate`` subscribes to
    every publication of the variant's other federates whose key matches one
    of ``keys`` (fnmatch patterns), and serves them as ``GET /api/v1/state``.
    A variant without that federate has no snapshot.
    """

    federate: str
    keys: list[str]


class BacnetPoint(Strict):
    """BACnet object attributes of a point (ot-sim BACnet module, DESIGN.md §16).

    ``units`` is a BACnet engineering-units enumeration name (for example
    ``degreesFahrenheit``); ``states`` lists the state texts of a
    multi-state value (state 1 is the first entry).
    """

    object_type: BacnetObjectType
    units: str | None = None
    states: list[str] | None = None

    @property
    def type_number(self) -> int:
        """BACnet object type enumeration (FUXA addresses use it)."""
        return BACNET_OBJECTS[self.object_type][1]


class PointSpec(Strict):
    """One point of an ot-sim device type.

    ``signed`` points are int16 registers (two's complement) instead of uint16.
    ``written_by`` names the host that writes a holding register (for example
    the DER gateway); the owning PLC then only reads it (FC3 into ``%IW``).
    ``bacnet`` makes the point a BACnet object instead of a Modbus register.
    """

    type: TagType
    scaling: int | None = None
    signed: bool = False
    written_by: str | None = None
    bacnet: BacnetPoint | None = None

    @model_validator(mode="after")
    def _check(self) -> PointSpec:
        if self.written_by and self.type != "analog-read-write":
            raise ValueError("written_by applies to analog-read-write points only")
        if self.bacnet:
            allowed = BACNET_OBJECTS[self.bacnet.object_type][0]
            if self.type not in allowed:
                raise ValueError(
                    f"{self.bacnet.object_type} cannot carry a {self.type} point"
                )
            if self.signed or self.written_by:
                raise ValueError("signed and written_by are Modbus-only")
        return self


class Interface(Strict):
    """A host NIC: VLAN alias and CIDR address."""

    vlan: str
    ip: str


class ModbusEndpoint(Strict):
    """Where a host serves Modbus TCP."""

    interface: str = "IF0"
    port: int = 502
    unit_id: int = 1


class BacnetEndpoint(Strict):
    """A BACnet/IP device served by the ot-sim BACnet module."""

    interface: str = "IF0"
    port: int = 47808
    instance: int = Field(ge=0, le=4194302)
    name: str


class Dnp3Endpoint(Strict):
    """Where an ot-sim FEP serves DNP3 (outstation)."""

    interface: str = "IF0"
    port: int = 20000


class FuxaSource(Strict):
    """A device a FUXA instance polls, and which of its tags (fnmatch)."""

    host: str
    tags: list[str] = Field(default_factory=lambda: ["*"])


class HostSpec(Strict):
    """A topology node relevant to plantgen."""

    role: HostRole
    interfaces: dict[str, Interface] = Field(default_factory=dict)
    federate: str | None = None
    modbus: ModbusEndpoint | None = None
    poll_ms: int = 100
    #: PLC northbound protocol (DESIGN.md §12.1).
    northbound: Northbound = "modbus"
    bacnet: BacnetEndpoint | None = None
    dnp3: Dnp3Endpoint | None = None
    #: FEP: field devices it polls (``upstream``); DNP3 master: FEPs it polls.
    upstream: list[str] = Field(default_factory=list)
    #: FUXA: what it polls. Default: every PLC in the variant.
    fuxa: list[FuxaSource] | None = None
    #: VyOS hosts use the ``ntp`` client and no guest agent processes.
    vyos: bool = False
    #: Host metadata for the ``cherrymountain`` app; without it the app's
    #: type follows the role (see plantgen.apps.APP_TYPES).
    app: dict[str, Any] | None = None


class Rationale(Strict):
    """ISA-18.2 rationalization fields (all optional so alarms can override)."""

    priority: Priority | None = None
    cause: str | None = None
    consequence: str | None = None
    action: str | None = None
    response_time: str | float | None = None


class AlarmSpec(Rationale):
    """One alarm on a tag.

    ``state`` alarms are active while the tag equals ``limit`` (1 or 0 for a
    bit, an enumeration value such as PackML Held = 11 for a word).
    ``suppress`` names a bit tag (same unit or full name) that suppresses the
    alarm while it is 0, for state-based suppression (ISA-18.2).
    """

    kind: AlarmKind
    limit: float | None = None
    delay: str | float = 0.0
    setpoint: str | None = None
    rationale: str | None = None
    suppress: str | None = None
    #: Hysteresis in engineering units: the alarm clears only once the value is
    #: this far back inside the limit. Default: 1 % of the tag's range span
    #: (ISA-18.2 deadband), and at most a quarter of a deviation limit;
    #: ``state`` alarms have none.
    deadband: float | None = None
    #: Off-delay: the alarm stays active this long after the condition clears.
    off_delay: str | float = 0.0


class TagSpec(Strict):
    """One tag as written in plant.yaml (``<INSTR>.<ATTR>``)."""

    id: str
    desc: str = ""
    io: str | None = None
    #: A bit another PLC computes (its full tag name), which this area's PLC
    #: reads over Modbus (east-west peer interlock, DESIGN.md 6.6 #4).
    peer: str | None = None
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
    zone: str | None = None


class UnitSpec(Strict):
    """An ISA-88 unit (equipment) with its devices and tags.

    ``rio`` and ``federate`` override the area defaults, for units whose
    devices live on another field host (the chiller's OEM controller, each
    power device, each BAS controller).
    """

    id: str
    name: str
    model: str
    rio: str | None = None
    federate: str | None = None
    devices: list[DeviceSpec] = Field(default_factory=list)
    tags: list[TagSpec] = Field(default_factory=list)


class AreaSpec(Strict):
    """A process area and the hosts that own it.

    An area without a ``plc`` (Facilities) has its tags owned by the BACnet
    controllers themselves; its alarms are evaluated by FUXA.
    """

    id: str
    name: str
    plc: str | None = None
    rio: str | None = None
    federate: str
    units: list[UnitSpec]


class BrokerSpec(Strict):
    """HELICS broker location."""

    hostname: str
    interface: str


class VariantHelics(Strict):
    """Per-variant HELICS settings.

    ``app_block`` emits the scenario's ``helics`` app block
    (``phenix/helics.yaml``); the ci scenario writes its own.
    """

    broker: BrokerSpec
    end_time_s: int = 36000
    app_block: bool = False


class InitialStateSpec(Strict):
    """Initial state of one vessel (mirrors cherrymountain_sim.InitialState).

    Fermenters use ``empty`` or ``fermenting``; bright beer tanks use
    ``empty``, ``carbonating``, or ``serving`` with a level and dissolved CO2.
    """

    phase: Literal["empty", "fermenting", "carbonating", "serving"] = "empty"
    sim_hours: float = 0.0
    temp_f: float = 66.0
    dry_hop_lb_per_bbl: float = 0.0
    spund_sp_psig: float | None = None
    level_pct: float | None = None
    co2_vol: float | None = None


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
    """ntp app (app mode) for a variant.

    A host's entry is ``hosts[name]`` if present, else ``roles[role]``.
    """

    default_source: NtpSource
    hosts: dict[str, NtpHost] = Field(default_factory=dict)
    roles: dict[str, NtpHost] = Field(default_factory=dict)


class ClockSpec(Strict):
    """Simulation clock (DESIGN.md §9.4): ISO 8601 start with UTC offset."""

    start: str
    timezone: str = "America/Denver"
    speedup: float = Field(default=1.0, ge=1.0)


class VariantSpec(Strict):
    """A topology variant.

    ``units`` limits an area to the listed units (an allow-list per area id);
    an area that is not listed keeps all its units. ``federate_sim`` passes
    per-federate model settings through to helics.json (the cellar uses
    ``sim``). ``soh``, ``wireguard``, and ``scorch`` turn on the app blocks
    and lists of the same names.
    """

    description: str = ""
    areas: list[str]
    units: dict[str, list[str]] = Field(default_factory=dict)
    federates: list[str]
    hosts: list[str]
    host_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    helics: VariantHelics
    ground_truth: bool = True
    sim: SimSpec = Field(default_factory=SimSpec)
    federate_sim: dict[str, dict[str, Any]] = Field(default_factory=dict)
    clock: ClockSpec | None = None
    ntp: NtpSpec
    soh: bool = False
    wireguard: bool = False
    scorch: bool = False
    #: Extra app-level metadata of the ``cherrymountain`` app block.
    app: dict[str, Any] = Field(default_factory=dict)


class Network(Strict):
    """A VLAN alias: subnet and router. ``gateway`` None means unrouted."""

    subnet: str
    gateway: str | None = None


class LoadSpec(Strict):
    """An electrical load the power federate subscribes to (DESIGN.md §7.2).

    The publishing federate publishes ``<federate>/load.<name>.kw``; the power
    federate subscribes to it when both are in the variant, and uses
    ``default_kw`` otherwise. The publisher subscribes to
    ``power/bus.<bus>.energized``.
    """

    name: str
    federate: str
    bus: str
    rated_kw: float
    default_kw: float = 0.0
    desc: str = ""


class SohProfile(Strict):
    """Per-role SoH expectations (pgrep patterns, listeners, containers)."""

    processes: list[str] = Field(default_factory=list)
    listeners: list[str] = Field(default_factory=list)
    containers: list[str] = Field(default_factory=list)


class Conduit(Strict):
    """A testable flow for SoH ``testCustomReachability`` (DESIGN.md §10.3)."""

    src: str
    dst: str  # hostname; the interface defaults to the first plant NIC
    interface: str | None = None
    proto: Literal["tcp", "udp"] = "tcp"
    port: int
    desc: str = ""


class SohSpec(Strict):
    """soh app settings (DESIGN.md §15.4); plantgen derives most flows."""

    startup_delay: str = "10m"
    c2_timeout: str = "15m"
    wait: str = "5m"
    skip_hosts: list[str] = Field(default_factory=list)
    profiles: dict[str, SohProfile] = Field(default_factory=dict)
    host_profiles: dict[str, SohProfile] = Field(default_factory=dict)
    conduits: list[Conduit] = Field(default_factory=list)


class WireguardSpec(Strict):
    """Vendor WireGuard tunnel (DESIGN.md §13): range-only throwaway keys."""

    server: str
    client: str
    server_address: str
    client_address: str
    listen_port: int = 51820
    endpoint: str
    client_allowed_ips: list[str]
    keepalive: int = 25
    seed: str


class ScorchSpec(Strict):
    """SCORCH pcap capture lists per run (DESIGN.md §15.6): ``host|IF``."""

    captures: dict[str, list[str]]


class PlantFile(Strict):
    """Top level of plant.yaml."""

    schema_version: Literal[1]
    site: Site
    networks: dict[str, Network] = Field(default_factory=dict)
    federates: dict[str, FederateSpec]
    loads: list[LoadSpec] = Field(default_factory=list)
    device_types: dict[str, dict[str, PointSpec]]
    hosts: dict[str, HostSpec]
    alarm_rationale: dict[str, Rationale] = Field(default_factory=dict)
    areas: list[AreaSpec]
    soh: SohSpec | None = None
    wireguard: WireguardSpec | None = None
    scorch: ScorchSpec | None = None
    snapshot: SnapshotSpec | None = None
    variants: dict[str, VariantSpec]

    @model_validator(mode="after")
    def _unit_output_models_exist(self) -> PlantFile:
        """Every ``unit_outputs`` model is a unit model some area uses, so a
        typo cannot silently publish nothing."""
        models = {u.model for a in self.areas for u in a.units}
        for fname, fed in self.federates.items():
            for uo in fed.unit_outputs:
                unknown = sorted(set(uo.models) - models)
                if unknown:
                    raise ValueError(
                        f"federates.{fname}.unit_outputs {uo.key!r}: unknown unit "
                        f"model(s) {unknown}; have {sorted(models)}"
                    )
        return self


# ---------------------------------------------------------------------------
# Expanded records
# ---------------------------------------------------------------------------


class Point(BaseModel):
    """A named point of a device type."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    type: TagType
    scaling: int | None = None
    signed: bool = False
    written_by: str | None = None
    bacnet: BacnetPoint | None = None

    @property
    def commandable(self) -> bool:
        """A BACnet object with a priority array: an output, or a value that
        takes writes (a setpoint, a mode). A command overrides the controller's
        own logic for the point until it is relinquished (DESIGN.md §8.1)."""
        return self.bacnet is not None and is_writable(self.type)


class Device(BaseModel):
    """One ot-sim infrastructure device on a field host.

    ``rio`` is the field host that serves the device (a remote I/O, an OEM
    controller, a power device, or a BACnet controller).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str  # without federate prefix, e.g. fv101-side
    type: str
    federate: str
    rio: str
    area: str
    unit: str
    zone: str | None = None
    protocol: Literal["modbus", "bacnet"] = "modbus"

    @property
    def topic(self) -> str:
        """HELICS topic, ``<federate>/<device>``."""
        return f"{self.federate}/{self.name}"


class Alarm(BaseModel):
    """A rationalized alarm (ISA-18.2) and the PLC bit that carries it.

    Alarms on tags without a PLC (BACnet points) have no bit; FUXA evaluates
    them from the source tag.
    """

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
    suppress: str | None = None
    has_bit: bool = True
    deadband: float = 0.0
    off_delay_s: float = 0.0


class Tag(BaseModel):
    """One plant tag, fully expanded."""

    name: str
    area: str
    unit: str
    instrument: str
    attr: str
    desc: str
    type: TagType
    plc: str | None
    units: str | None = None
    range: tuple[float, float] | None = None
    scaling: int = 0
    signed: bool = False
    written_by: str | None = None
    bacnet: BacnetPoint | None = None
    device: Device | None = None
    point: str | None = None
    rio: str | None = None
    federate: str | None = None
    alarms: list[Alarm] = Field(default_factory=list)
    generated: bool = False  # alarm bit tags are generated
    peer: str | None = None  # source tag on another PLC (peer read)

    @property
    def source(self) -> str:
        """``io`` for remote-I/O tags, ``peer`` for bits read from another PLC,
        ``plc`` for PLC memory."""
        return "io" if self.device else ("peer" if self.peer else "plc")

    @property
    def owner(self) -> str | None:
        """The PLC, or for BACnet points the controller that serves them."""
        return self.plc or (self.device.rio if self.device else None)

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
    #: (area id, unit) of every unit in the variant, in plant.yaml order.
    units: list[tuple[str, UnitSpec]] = Field(default_factory=list)

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

    def hosts_by_role(self, *roles: str) -> list[Host]:
        """Variant hosts with any of ``roles``, in variant order."""
        return [h for h in self.hosts.values() if h.role in roles]

    @property
    def plcs(self) -> list[str]:
        """PLCs that own tags, in variant host order."""
        owned = {t.plc for t in self.tags if t.plc}
        return [h for h in self.hosts if h in owned]

    def ntp_entry(self, host: str) -> NtpHost | None:
        """The ntp app metadata of a variant host (host entry, else role)."""
        ntp = self.spec.ntp
        if host in ntp.hosts:
            return ntp.hosts[host]
        role = self.hosts[host].role if host in self.hosts else None
        return ntp.roles.get(role) if role else None

    def network(self, vlan: str) -> Network | None:
        """The network definition of a VLAN alias, if plant.yaml has one."""
        return self.file.networks.get(vlan)


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
            pn: Point(
                name=pn,
                type=p.type,
                scaling=p.scaling,
                signed=p.signed,
                written_by=p.written_by,
                bacnet=p.bacnet,
            )
            for pn, p in pts.items()
        }
        for name, pts in pf.device_types.items()
    }
    protocols = {name: _protocol(name, pts) for name, pts in device_types.items()}
    unknown = [f for f in spec.federates if f not in pf.federates]
    if unknown:
        raise PlantError(f"variant {variant}: unknown federates {unknown}")
    known_areas = {a.id: a for a in pf.areas}
    for area_id, unit_ids in spec.units.items():
        if area_id not in known_areas:
            raise PlantError(f"variant {variant}: units for unknown area {area_id}")
        missing = set(unit_ids) - {u.id for u in known_areas[area_id].units}
        if missing:
            raise PlantError(f"variant {variant}: unknown {area_id} units {missing}")
    federates = {n: f for n, f in pf.federates.items() if n in spec.federates}
    devices: list[Device] = []
    tags: list[Tag] = []
    units: list[tuple[str, UnitSpec]] = []
    for area in pf.areas:
        if area.id not in spec.areas:
            continue
        allowed = spec.units.get(area.id)
        for unit in area.units:
            if allowed is not None and unit.id not in allowed:
                continue
            units.append((area.id, unit))
            rio = unit.rio or area.rio
            federate = unit.federate or area.federate
            unit_devices: dict[str, Device] = {}
            for d in unit.devices:
                if d.type not in device_types:
                    raise PlantError(f"device {d.name}: unknown type {d.type!r}")
                if rio is None:
                    raise PlantError(f"device {d.name}: unit {unit.id} has no rio")
                unit_devices[d.name] = Device(
                    name=d.name,
                    type=d.type,
                    federate=federate,
                    rio=rio,
                    area=area.id,
                    unit=unit.id,
                    zone=d.zone,
                    protocol=protocols[d.type],
                )
            devices.extend(unit_devices.values())
            for ts in unit.tags:
                tag = _tag(area, unit, ts, unit_devices, device_types)
                tag.alarms = [_alarm(tag, a, pf.alarm_rationale) for a in ts.alarms]
                tags.append(tag)
                tags.extend(_alarm_tag(tag, a) for a in tag.alarms if a.has_bit)
    return Plant(
        variant=variant,
        file=pf,
        spec=spec,
        hosts=hosts,
        device_types=device_types,
        devices=devices,
        tags=tags,
        federates=federates,
        units=units,
    )


def _protocol(name: str, points: dict[str, Point]) -> Literal["modbus", "bacnet"]:
    """A device type is all BACnet objects or all Modbus registers."""
    kinds = {p.bacnet is not None for p in points.values()}
    if len(kinds) > 1:
        raise PlantError(f"device type {name} mixes BACnet and Modbus points")
    return "bacnet" if kinds == {True} else "modbus"


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
        signed, written_by, bacnet = pdef.signed, pdef.written_by, pdef.bacnet
    else:
        if spec.peer is not None and spec.type not in (None, "binary-read"):
            raise PlantError(f"{name}: a peer read is a binary-read bit")
        if spec.peer is not None:
            spec = spec.model_copy(update={"type": "binary-read"})
        if spec.type is None:
            raise PlantError(f"{name}: PLC-memory tags need a type")
        if area.plc is None:
            raise PlantError(f"{name}: area {area.id} has no PLC for memory tags")
        ttype, scaling = spec.type, spec.scaling
        signed, written_by, bacnet = False, None, None
    if is_analog(ttype) and spec.range is None:
        raise PlantError(f"{name}: analog tags need a range")
    if is_analog(ttype) and scaling is None and bacnet is None:
        raise PlantError(f"{name}: analog Modbus tags need a scaling")
    return Tag(
        name=name,
        area=area.id,
        unit=unit.id,
        instrument=instrument,
        attr=attr,
        desc=spec.desc,
        type=ttype,
        plc=area.plc if bacnet is None else None,
        units=spec.units,
        range=spec.range,
        scaling=scaling or 0,
        signed=signed,
        written_by=written_by,
        bacnet=bacnet,
        device=device,
        point=point,
        rio=device.rio if device else None,
        federate=device.federate if device else None,
        peer=spec.peer,
    )


def _alarm(tag: Tag, spec: AlarmSpec, library: dict[str, Rationale]) -> Alarm:
    merged: dict[str, Any] = {}
    if spec.rationale is not None:
        if spec.rationale not in library:
            raise PlantError(f"{tag.name}: unknown rationale {spec.rationale!r}")
        merged.update(library[spec.rationale].model_dump(exclude_none=True))
    merged.update(spec.model_dump(exclude_none=True, exclude={"rationale"}))
    setpoint = merged.get("setpoint")
    suppress = merged.get("suppress")
    if suppress and suppress.count(".") == 1:  # INSTR.ATTR in the same unit
        suppress = f"{tag.area}.{tag.unit}.{suppress}"
    elif suppress and suppress.count(".") == 2:  # UNIT.INSTR.ATTR in the area
        suppress = f"{tag.area}.{suppress}"
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
        suppress=suppress,
        has_bit=tag.plc is not None,
        deadband=_deadband(tag, spec),
        off_delay_s=parse_duration(merged.get("off_delay", 0)),
    )


def _deadband(tag: Tag, spec: AlarmSpec) -> float:
    """The alarm's deadband in engineering units, rounded to the tag's
    resolution (at least one count): explicit, else 1 % of the range span."""
    if spec.kind == "state":
        if spec.deadband:
            raise PlantError(f"{tag.name}: a state alarm has no deadband")
        return 0.0
    step: float = 10.0 ** -tag.scaling
    if spec.deadband is not None:
        if spec.deadband < 0:
            raise PlantError(f"{tag.name}: alarm deadband must not be negative")
        raw: float = spec.deadband
    elif tag.range is not None:
        raw = (tag.range[1] - tag.range[0]) / 100.0
        if spec.kind == "deviation" and spec.limit:
            # A deviation band is narrow next to the span: at most a quarter of it.
            raw = min(raw, spec.limit / 4.0)
    else:
        raw = step
    counts: int = max(round(raw / step), 1)
    if spec.deadband is None and spec.kind == "deviation" and spec.limit:
        counts = min(counts, max(int(spec.limit / 4.0 / step + 1e-9), 1))
    return round(counts * step, 6)


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
