"""Pydantic models for the cherrymountain app's scenario metadata.

One model per metadata shape. Open shapes (the app block and each host's
metadata) subclass :class:`Block`: an unknown key is kept, and validation
warns about it with a did-you-mean hint. Closed shapes subclass
:class:`Closed` and reject unknown keys. Host metadata is a discriminated
union on ``type``.

Keys are camelCase, as in phenix's own apps (``startupDelay``); the Python
fields are snake_case and use the camelCase alias. Values are validated in
strict mode, so ``"false"`` is not a bool and ``"502"`` is not a port.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, ClassVar, Literal

from phenix_apps.apps.scorch.app import Hostname, Interface, Name, Seconds
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    SecretStr,
    Tag,
    field_validator,
)
from pydantic.alias_generators import to_camel

#: A TCP or UDP port.
Port = Annotated[int, Field(ge=1, le=65535)]
#: An absolute POSIX path on the phenix host, of plain characters only, so it is
#: safe in a minimega injection (no ``:``, ``$``, spaces, or ``..``).
AbsPath = Annotated[str, Field(pattern=r"^/(?:[\w.+@-]+/?)*$")]
#: A path relative to the topology directory, with the same character rules.
RelPath = Annotated[str, Field(pattern=r"^[\w+@-][\w.+@-]*(?:/[\w+@-][\w.+@-]*)*$")]
#: A FUXA view name or URL path segment.
ViewName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")]
#: A vendor technician's short name; the accounts are ``ridgeline-<name>``.
Technician = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]{1,15}$")]


def _no_parent(value: str) -> str:
    if ".." in value.split("/"):
        raise ValueError("must not contain a '..' component")
    return value


SafeAbsPath = Annotated[AbsPath, AfterValidator(_no_parent)]
SafeRelPath = Annotated[RelPath, AfterValidator(_no_parent)]


class Block(BaseModel):
    """An open metadata shape: unknown keys are kept and warned about."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="allow",
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
    )


class Closed(BaseModel):
    """A closed metadata shape: unknown keys are errors."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
    )


# ---------------------------------------------------------------- app level


class SimCtl(Closed):
    """Where the sim-ctl event API runs (DESIGN.md 9.5)."""

    hostname: Hostname = "sim-ctl"
    #: Guest port of the event API; the host port is looked up per call.
    event_port: Port = 8080
    #: Seconds to wait for one event API request.
    timeout: Seconds = 10.0
    #: Guest port of cm-viz, the white cell's 3D view (DESIGN.md 15.10), on
    #: 127.0.0.1 next to the event API; the autotunnel forwards it.
    viz_port: Port = 8090


class MgmtPolicy(Closed):
    """The nftables policy on every MGMT member (DESIGN.md 10.1)."""

    enabled: bool = True
    #: The topology's name of the MGMT interface. It is not the guest's device
    #: name (phenix does not rename NICs): the startup hook finds the device by
    #: the host's MGMT address.
    interface: Interface = "mgmt"
    #: HELICS zmq broker ports on sim-ctl (the default zmq core ports).
    broker_ports: Annotated[list[Port], Field(min_length=1)] = Field(
        default_factory=lambda: [23404, 23405, 23406]
    )
    #: Ports the federates' zmq cores bind, which the broker connects back to.
    #: Nothing pins them: the broker hands each core one, counted up per host
    #: from just above its own (23414 in HELICS 3.6, the default broker port
    #: plus 10; 23492 was also seen), so the range starts right after the
    #: broker ports. A range that starts above 23414 stops the federation.
    federate_port_min: Port = 23407
    federate_port_max: Port = 23999
    opensearch_port: Port = 9200
    #: Hosts with a MGMT NIC that keep no policy.
    exclude: list[Hostname] = Field(default_factory=list)


class Historian(Closed):
    """InfluxDB 2, Telegraf, Grafana, and Mosquitto settings (DESIGN.md 12.4)."""

    org: Name = "cherrymountain"
    buckets: Annotated[list[Name], Field(min_length=1)] = Field(
        default_factory=lambda: ["scada", "bas", "oee"]
    )
    #: Retention for every bucket, in hours (720 h is 30 days); 0 keeps data forever.
    retention_hours: Annotated[int, Field(ge=0)] = 720
    #: The records bucket (DESIGN.md 12.4): a daily copy of the series a TTB
    #: and Colorado excise audit asks for, kept three years (27 CFR 25.300,
    #: C.R.S. 44-3-503(6)) while the raw buckets keep their 30 days. An empty
    #: name turns it off.
    records_bucket: Name | Literal[""] = "records"
    #: 26,280 h is three years.
    records_retention_hours: Annotated[int, Field(ge=0)] = 26280
    #: The tags copied (a Flux regular expression on FUXA's tag name): the
    #: packaging counts and rejects that the barrel figures come from, the
    #: bright-tank levels (beer on hand), and the fermenters' extract (production).
    records_tags: Annotated[str, Field(pattern=r"^[^/\n]+$")] = (
        r"^(PKG\.(LINE|FILLER|KEG)\.PACKML\.(PROCESSED|DEFECTIVE)|CEL\.BBT20[12]\.LT01\.PV|CEL\.FV10[1-4]\.DT01\.PV)$"
    )
    influx_port: Port = 8086
    grafana_port: Port = 3000
    mqtt_port: Port = 1883
    #: MQTT topic root of the PackTags/OEE publisher (ISA-95 path).
    mqtt_topic_root: Annotated[str, Field(pattern=r"^[A-Za-z0-9_/-]+$")] = (
        "cherrymountain/frontrange"
    )


class Wazuh(Closed):
    """Wazuh agent enrollment (DESIGN.md 14)."""

    manager: Hostname = "wazuh"
    interface: Interface = "IF0"
    enrollment_port: Port = 1515
    #: Hosts that run an agent and get the enrollment password: an explicit
    #: allowlist (plant.yaml derives it from the conduits to the manager).
    #: Empty means none.
    agents: list[Hostname] = Field(default_factory=list)


#: Characters an operator-set secret may use. The rendered env files are also
#: sourced by shell scripts, so shell metacharacters are refused up front.
SECRET_CHARS: re.Pattern[str] = re.compile(r"[A-Za-z0-9._~@%+=:,-]+")


class Secrets(Closed):
    """Range-only credentials. Each one that is not set is generated once per
    experiment and kept in the experiment directory, never logged."""

    @field_validator("*")
    @classmethod
    def _safe_chars(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not SECRET_CHARS.fullmatch(value.get_secret_value()):
            raise ValueError(
                "a secret may use only letters, digits, and . _ ~ @ % + = : , -"
            )
        return value

    openplc_password: SecretStr | None = None
    fuxa_password: SecretStr | None = None
    fuxa_jwt_secret: SecretStr | None = None
    influx_password: SecretStr | None = None
    influx_token: SecretStr | None = None
    grafana_password: SecretStr | None = None
    guacamole_password: SecretStr | None = None
    wazuh_enrollment: SecretStr | None = None
    #: The vendor persona (DESIGN.md 13): the Guacamole account, its TOTP key
    #: (RFC 6238, Base32), and the EWS account's password.
    vendor_guac_password: SecretStr | None = None
    vendor_totp_secret: SecretStr | None = None
    vendor_ews_password: SecretStr | None = None


class Verify(Closed):
    """How post-start and running check provisioning."""

    #: Seconds to wait for each check run on a VM.
    timeout: Seconds = 15.0
    #: Seconds to wait for a VM's C2 client before a check is skipped.
    client_grace: Seconds = 5.0


class AppMetadata(Block):
    """The ``metadata`` block of the cherrymountain app."""

    #: plant.yaml variant: ci, lite, or full.
    variant: Name
    #: The cherrymountain-brewery directory inside the phenix container.
    source_dir: SafeAbsPath = "/phenix/topologies/cherrymountain-brewery"
    sim_ctl: SimCtl = Field(default_factory=SimCtl)
    mgmt: MgmtPolicy = Field(default_factory=MgmtPolicy)
    historian: Historian = Field(default_factory=Historian)
    wazuh: Wazuh | None = None
    secrets: Secrets = Field(default_factory=Secrets)
    #: WireGuard keys of the scenario's ``wireguard`` app: ``generate`` replaces
    #: the committed (plantgen, fixed-seed) keys with per-experiment ones at
    #: configure; ``scenario`` keeps them.
    wireguard_keys: Literal["generate", "scenario"] = "generate"
    #: Ridgeline's technicians (DESIGN.md 13): each gets a named Guacamole
    #: account with its own TOTP key and a named EWS account, instead of one
    #: shared vendor login. The first is the one vendor-ws's scripted session
    #: signs in as; it uses ``secrets.vendor*``, the others get generated ones.
    vendor_technicians: Annotated[list[Technician], Field(min_length=1, max_length=8)] = Field(
        default_factory=lambda: ["akim", "jdiaz"]
    )
    verify: Verify = Field(default_factory=Verify)
    #: Export FUXA alarm history, DAQ data, and an InfluxDB backup at cleanup.
    export_on_cleanup: bool = True

    @field_validator("vendor_technicians")
    @classmethod
    def _unique_technicians(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("vendorTechnicians must not repeat a name")
        return value


# ---------------------------------------------------------------- hosts


class HostBase(Block):
    """Keys every host's metadata may carry."""

    #: Written by configure; phenix's soh app reads it (appMetadataProfileKey).
    soh_profile: dict[str, Any] | None = None


class OpcUa(Closed):
    """The OpenPLC v4 ``opcua`` plugin (DESIGN.md 12.1)."""

    port: Port = 4840
    #: The interface whose address the endpoint binds; the template's
    #: localhost endpoint refuses remote clients.
    interface: Interface = "IF0"
    #: Debug map of the compiled program, relative to the topology directory.
    #: Without it the app reads ``debug_map.json`` from the program ZIP, then
    #: from ``plc/<program>/debug_map.json``.
    debug_map: SafeRelPath | None = None
    cycle_time_ms: Annotated[int, Field(ge=10, le=60000)] = 100


class S7Comm(Closed):
    """The OpenPLC v4 ``s7comm`` plugin."""

    port: Port = 102


class OpenPlcHost(HostBase):
    """A PLC running the OpenPLC v4 runtime (cm-openplc)."""

    type: Literal["openplc"]
    #: Program name: the ZIP is phenix-injects/openplc/<program>.zip and the
    #: plantgen configs are under plant/generated/<variant>/openplc/<program>/.
    program: Name | None = None
    #: The northbound plugin. Default: plant.yaml's ``northbound`` for the host
    #: (``modbus`` is the ``modbus_slave`` plugin).
    northbound: Literal["modbus_slave", "opcua", "s7comm"] | None = None
    opcua: OpcUa = Field(default_factory=OpcUa)
    s7comm: S7Comm = Field(default_factory=S7Comm)
    rest_port: Port = 8443
    compile_timeout: Annotated[int, Field(ge=60, le=7200)] = 900


class FuxaHost(HostBase):
    """A FUXA instance (cm-fuxa)."""

    type: Literal["fuxa"]
    role: Literal["scada", "bas"] = "scada"
    #: A committed project JSON, relative to the topology directory. Without it
    #: the app builds the host's project with fuxa/build_project.py.
    project: SafeRelPath | None = None
    #: Device names the imported project must contain (verified after start).
    #: Without it, every device of the project.
    expect_devices: list[Name] | None = None
    #: Where DAQ goes: FUXA's SQLite, or the historian's InfluxDB 2.
    daq: Literal["auto", "sqlite", "influxdb"] = "auto"
    #: The InfluxDB bucket for DAQ; default: the role (scada or bas).
    bucket: Name | None = None
    port: Port = 1881


class Telnet(Closed):
    """The ot-sim ``telnet`` module (an admin shell, DESIGN.md 10.4), which the
    phenix ot-sim app does not render: this app adds it to the device's
    config.xml at pre-start."""

    interface: Interface = "IF0"
    port: Port = 23
    banner: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,32}$")] = "default"


class RioHost(HostBase):
    """An ot-sim device (remote I/O, field, BAS, FEP, or DNP3 master); the
    ot-sim app configures it, and this app checks it."""

    type: Literal["rio"]
    telnet: Telnet | None = None


class EwsHost(HostBase):
    """The engineering workstation (cm-ews): the OpenPLC REST credentials for
    cm_plc_tool.py, in /etc/default/cm-ews (DESIGN.md 12.4)."""

    type: Literal["ews"]


class SimCtlHost(HostBase):
    """sim-ctl: HELICS root broker, event API, ground truth."""

    type: Literal["sim-ctl"]
    #: Federates hosted on sim-ctl (the weather player in full); default: every
    #: plant.yaml federate whose host is this one.
    federates: list[Name] | None = None


class SimFederateHost(HostBase):
    """A simulation node that runs cherrymountain_sim federates."""

    type: Literal["sim-federate"]
    federates: list[Name] | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class HistorianHost(HostBase):
    """The historian (cm-historian): InfluxDB 2, Telegraf, Grafana, Mosquitto."""

    type: Literal["historian"]
    interface: Interface = "IF0"


class HmiHost(HostBase):
    """A Chromium kiosk (cm-desktop)."""

    type: Literal["hmi"]
    #: The FUXA host the kiosk shows, and the view to open.
    fuxa: Hostname = "scada-fuxa"
    view: ViewName = "Overview"
    #: The FUXA host's interface the kiosk connects to.
    interface: Interface = "IF0"


class GuacConnection(Closed):
    """One Guacamole connection."""

    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")]
    protocol: Literal["rdp", "ssh", "vnc"] = "rdp"
    hostname: Hostname
    interface: Interface = "IF0"
    port: Port | None = None
    username: Name | None = None


class GuacamoleHost(HostBase):
    """The DMZ jump host (cm-guacamole)."""

    type: Literal["guacamole"]
    connections: list[GuacConnection] = Field(
        default_factory=lambda: [
            GuacConnection.model_validate({"name": "ews-rdp", "hostname": "ews"}),
            GuacConnection.model_validate(
                {"name": "ews-ssh", "hostname": "ews", "protocol": "ssh"}
            ),
        ]
    )
    #: Record sessions (blue-team evidence, DESIGN.md 13).
    recording: bool = True
    totp: bool = True


class WazuhManagerHost(HostBase):
    """The Wazuh manager (cm-wazuh)."""

    type: Literal["wazuh-manager"]


class BbmdHost(HostBase):
    """A BACpypes3 BBMD (cm-bbmd.service on ot-svc and bas-bbmd). The
    topology injects its static settings from phenix-injects/bbmd/; the app
    only checks it."""

    type: Literal["bbmd"]


#: metadata.type -> model.
HOST_TYPES: dict[str, type[HostBase]] = {
    "openplc": OpenPlcHost,
    "fuxa": FuxaHost,
    "rio": RioHost,
    "sim-ctl": SimCtlHost,
    "sim-federate": SimFederateHost,
    "historian": HistorianHost,
    "hmi": HmiHost,
    "guacamole": GuacamoleHost,
    "wazuh-manager": WazuhManagerHost,
    "bbmd": BbmdHost,
    "ews": EwsHost,
}


def _host_kind(value: Any) -> str | None:
    kind = (
        value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    )
    return kind if isinstance(kind, str) and kind in HOST_TYPES else None


HostMetadata = Annotated[
    Annotated[OpenPlcHost, Tag("openplc")]
    | Annotated[FuxaHost, Tag("fuxa")]
    | Annotated[RioHost, Tag("rio")]
    | Annotated[SimCtlHost, Tag("sim-ctl")]
    | Annotated[SimFederateHost, Tag("sim-federate")]
    | Annotated[HistorianHost, Tag("historian")]
    | Annotated[HmiHost, Tag("hmi")]
    | Annotated[GuacamoleHost, Tag("guacamole")]
    | Annotated[WazuhManagerHost, Tag("wazuh-manager")]
    | Annotated[BbmdHost, Tag("bbmd")]
    | Annotated[EwsHost, Tag("ews")],
    Discriminator(
        _host_kind,
        custom_error_type="unknown_host_type",
        custom_error_message="not a known host type",
    ),
]


class AppHost(BaseModel):
    """One entry of the app's ``hosts`` list."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    hostname: Hostname
    metadata: HostMetadata


class AppHosts(BaseModel):
    """The app's ``hosts`` list."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    hosts: list[AppHost] = Field(default_factory=list)
