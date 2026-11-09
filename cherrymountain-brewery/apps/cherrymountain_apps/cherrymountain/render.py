"""Pre-start rendering: the files the app writes and the injections that put
them on the VMs.

Every injection source is a file under ``<experiment dir>/cherrymountain/
<host>/``, including the repository files the app reuses (provisioning
scripts, plantgen configs, program ZIPs): minimega injects from a path both
containers share, and a copy keeps a running experiment independent of later
edits to the topology directory. Files that hold a credential are written
mode 0600 and injected mode 0600.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from pathlib import Path
from typing import Any, ClassVar, Final

import yaml
from phenix_apps.common import utils
from phenix_apps.common.logger import logger
from pydantic import BaseModel, ConfigDict

from cherrymountain_apps.cherrymountain.credentials import RangeSecrets, write_private
from cherrymountain_apps.cherrymountain.metadata import (
    AppMetadata,
    BbmdHost,
    FuxaHost,
    GuacamoleHost,
    HistorianHost,
    HmiHost,
    HostBase,
    OpenPlcHost,
    SimCtlHost,
    SimFederateHost,
)
from cherrymountain_apps.cherrymountain.sources import DebugMap, SourceError, Sources

TEMPLATES: Final[Path] = Path(__file__).parent / "templates"
STARTUP: Final[str] = "/etc/phenix/startup"
CONF_DIR: Final[str] = "/etc/cherrymountain"
OPENPLC_DIR: Final[str] = "/opt/cherrymountain/openplc"
#: The OpenPLC plugin config file names the runtime looks for in conf/.
PLC_PLUGINS: Final[tuple[str, ...]] = (
    "modbus_master",
    "modbus_slave",
    "opcua",
    "s7comm",
)
#: Ounces per US beer barrel (DESIGN.md 12.2).
OZ_PER_BBL: Final[float] = 3968.0


class Injection(BaseModel):
    """One phenix injection."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    src: Path
    dst: str
    description: str
    permissions: str = "0644"

    def as_phenix(self) -> dict[str, str]:
        return {
            "src": str(self.src),
            "dst": self.dst,
            "description": self.description,
            "permissions": self.permissions,
        }


class Context:
    """What every renderer needs."""

    def __init__(
        self,
        app: AppMetadata,
        hosts: dict[str, HostBase],
        sources: Sources,
        creds: RangeSecrets,
        out_dir: Path,
        nodes: dict[str, Any],
    ) -> None:
        self.app: AppMetadata = app
        self.hosts: dict[str, HostBase] = hosts
        self.sources: Sources = sources
        self.creds: RangeSecrets = creds
        self.out_dir: Path = out_dir
        self.nodes: dict[str, Any] = nodes

    def host_dir(self, hostname: str) -> Path:
        path: Path = utils.safe_join(self.out_dir, utils.validate_hostname(hostname))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ip(self, hostname: str, iface: str) -> str:
        node: Any = self.nodes.get(hostname)
        if node is None:
            raise SourceError(f"host {hostname} is not in the topology")
        for i in (node.get("network") or {}).get("interfaces") or []:
            if i.get("name") == iface:
                if not i.get("address"):
                    raise SourceError(f"interface {iface} of {hostname} has no address")
                return str(i["address"])
        raise SourceError(f"host {hostname} has no interface {iface}")

    def hosts_of(self, kind: type[HostBase]) -> dict[str, Any]:
        return {name: md for name, md in self.hosts.items() if isinstance(md, kind)}

    def federates_of(
        self, hostname: str, md: SimCtlHost | SimFederateHost
    ) -> list[str]:
        if md.federates is not None:
            return list(md.federates)
        plant: Any = self.sources.plant()
        return [name for name, spec in plant.federates.items() if spec.host == hostname]


# ---------------------------------------------------------------- helpers


def template(*parts: str) -> Path:
    return TEMPLATES.joinpath(*parts)


def mako(name: str, **kwargs: Any) -> str:
    return utils.mako_render(str(template("mako", name)), **kwargs)


def write(path: Path, text: str, private: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if private:
        return write_private(path, text)
    path.write_text(text, encoding="utf-8")
    return path


def copy(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def env_file(values: dict[str, str | int]) -> str:
    """A systemd EnvironmentFile that a shell can also source."""
    lines: list[str] = ["# Rendered by the cherrymountain app. Do not edit."]
    for key, value in values.items():
        text: str = str(value)
        if any(c in text for c in "\n\"'\\$`"):
            raise ValueError(
                f"environment value for {key} has a character the file cannot hold"
            )
        lines.append(f'{key}="{text}"' if " " in text else f"{key}={text}")
    return "\n".join(lines) + "\n"


def to_json(doc: Any) -> str:
    return json.dumps(doc, indent=2) + "\n"


# ---------------------------------------------------------------- OpenPLC


def opcua_config(
    dmap: DebugMap,
    tags: list[dict[str, Any]],
    access: dict[str, str],
    st_name: Any,
    endpoint: str,
    cycle_time_ms: int,
) -> list[dict[str, Any]]:
    """``conf/opcua.json`` for the v4 ``opcua`` plugin (DESIGN.md 12.1).

    Each variable is addressed by ``arr``/``elem`` in the runtime's debug
    arrays, from the compiled program's debug map, not by name. Format
    version 2 needs a ``size`` per variable.
    """
    leaves = dmap.by_variable()
    variables: list[dict[str, Any]] = []
    missing: list[str] = []
    for tag in tags:
        leaf = leaves.get(st_name(tag["name"]))
        if leaf is None:
            missing.append(tag["name"])
            continue
        writable: bool = access.get(tag["name"]) == "read-write"
        variables.append(
            {
                "node_id": tag["name"],
                "browse_name": tag["name"],
                "display_name": tag["name"],
                "datatype": leaf.type,
                "description": tag.get("desc", ""),
                "arr": leaf.array_idx,
                "elem": leaf.elem_idx,
                "permissions": {
                    "viewer": "r",
                    "operator": "rw" if writable else "r",
                    "engineer": "rw" if writable else "r",
                },
                "size": leaf.size,
            }
        )
    if missing:
        raise SourceError(
            f"{len(missing)} plant tag(s) are not in the compiled program's debug map "
            f"(first: {missing[0]}); rebuild the program ZIP and its debug map"
        )
    return [
        {
            "name": "opcua_server",
            "protocol": "OPC-UA",
            "config": {
                "server": {
                    "name": "OpenPLC OPC UA Server",
                    "application_uri": "urn:freeopcua:python:server",
                    "product_uri": "urn:openplc:runtime:product",
                    "endpoint_url": endpoint,
                    "security_profiles": [
                        {
                            "name": "insecure",
                            "enabled": True,
                            "security_policy": "None",
                            "security_mode": "None",
                            "auth_methods": ["Anonymous"],
                        }
                    ],
                },
                "security": {
                    "server_certificate_strategy": "auto_self_signed",
                    "server_certificate_custom": None,
                    "server_private_key_custom": None,
                    "trusted_client_certificates": [],
                },
                "users": [],
                "cycle_time_ms": cycle_time_ms,
                "address_space": {
                    "namespace_uri": "urn:cherrymountain:opcua",
                    "variables": variables,
                    "structures": [],
                    "arrays": [],
                },
                "format_version": 2,
            },
        }
    ]


def s7comm_config(layout: dict[str, Any], port: int) -> dict[str, Any]:
    """``conf/s7comm.json``: DB1 is the %QW image, DB2 the %IW image.

    TODO(verify): the plugin's mapping types for %MW and the bit images; the
    openplc-v4 spike only exercised ``int_output`` and ``int_input``.
    """
    qw: int = int(layout["holding_registers"]["qw_count"])
    iw: int = int(layout["input_registers"]["iw_count"])
    return {
        "server": {"enabled": True, "bind_address": "0.0.0.0", "port": port},
        "data_blocks": [
            {
                "db_number": 1,
                "description": "QW",
                "size_bytes": max(qw, 1) * 2,
                "mapping": {"type": "int_output", "start_buffer": 0},
            },
            {
                "db_number": 2,
                "description": "IW",
                "size_bytes": max(iw, 1) * 2,
                "mapping": {"type": "int_input", "start_buffer": 0},
            },
        ],
    }


def render_openplc(ctx: Context, hostname: str, md: OpenPlcHost) -> list[Injection]:
    src: Sources = ctx.sources
    program: str = md.program or hostname
    out: Path = ctx.host_dir(hostname) / "openplc"
    conf_out: Path = out / "conf"
    if conf_out.exists():
        shutil.rmtree(conf_out)
    injects: list[Injection] = []

    zip_path: Path = copy(src.program_zip(program), out / f"{program}.zip")
    injects.append(
        Injection(
            src=zip_path,
            dst=f"{OPENPLC_DIR}/{program}.zip",
            description=f"{program} program ZIP (plc/{program}/build_zip.sh)",
        )
    )

    plantgen_dir: Path = src.plc_conf_dir(program)
    for plugin in ("modbus_master", "modbus_slave"):
        path: Path = plantgen_dir / f"{plugin}.json"
        if (
            plugin == "modbus_slave"
            and md.northbound != "modbus_slave"
            and not path.is_file()
        ):
            continue
        copied: Path = copy(src.require(path), conf_out / f"{plugin}.json")
        injects.append(
            Injection(
                src=copied,
                dst=f"{OPENPLC_DIR}/conf/{plugin}.json",
                description=f"plantgen {plugin} plugin config",
            )
        )

    if md.northbound == "opcua":
        dmap: DebugMap = src.debug_map(program, md.opcua.debug_map)
        md5: str | None = src.zip_md5(program)
        if md5 is not None and dmap.md5 != md5:
            raise SourceError(
                f"the debug map of {program} is not from the program in its ZIP (PROGRAM_MD5 differs); rebuild both"
            )
        tags: list[dict[str, Any]] = [
            t
            for t in src.generated_json("tags.json")["tags"]
            if t.get("plc") == hostname
        ]
        access: dict[str, str] = {
            r["tag"]: r["scada_access"]
            for r in src.generated_json("registers.json")["registers"]
            if r.get("plc") == hostname
        }
        endpoint: str = f"opc.tcp://{ctx.ip(hostname, md.opcua.interface)}:{md.opcua.port}/openplc/opcua"
        doc: Any = opcua_config(
            dmap, tags, access, src.st_name, endpoint, md.opcua.cycle_time_ms
        )
        path = write(conf_out / "opcua.json", to_json(doc))
        injects.append(
            Injection(
                src=path,
                dst=f"{OPENPLC_DIR}/conf/opcua.json",
                description="opcua plugin config (from the debug map)",
            )
        )
    elif md.northbound == "s7comm":
        layouts: dict[str, Any] = src.generated_json("registers.json").get(
            "slave_layouts", {}
        )
        if hostname not in layouts:
            raise SourceError(
                f"plant/generated/{src.variant}/registers.json has no slave layout for {hostname}"
            )
        path = write(
            conf_out / "s7comm.json",
            to_json(s7comm_config(layouts[hostname], md.s7comm.port)),
        )
        injects.append(
            Injection(
                src=path,
                dst=f"{OPENPLC_DIR}/conf/s7comm.json",
                description="s7comm plugin config",
            )
        )

    env: Path = write(
        out / "cm-openplc.env",
        env_file(
            {
                "OPENPLC_USER": "admin",
                "OPENPLC_PASSWORD": ctx.creds.openplc_password.get_secret_value(),
                "OPENPLC_PORT": md.rest_port,
                "OPENPLC_COMPILE_TIMEOUT": md.compile_timeout,
                "CM_PLC_ZIP": f"{OPENPLC_DIR}/{program}.zip",
                "CM_PLC_CONF": f"{OPENPLC_DIR}/conf",
            }
        ),
        private=True,
    )
    injects.append(
        Injection(
            src=env,
            dst="/etc/default/cm-openplc",
            description="OpenPLC provisioning settings",
            permissions="0600",
        )
    )
    injects += _shared(
        ctx,
        hostname,
        "openplc",
        [
            (
                "cm-openplc-provision.sh",
                "/usr/local/bin/cm-openplc-provision",
                "0755",
                "repack, upload, compile, and start the program",
            ),
            (
                "cm-openplc-provision.service",
                "/etc/systemd/system/cm-openplc-provision.service",
                "0644",
                "provisioning unit",
            ),
            (
                "70-openplc-provision.sh",
                f"{STARTUP}/70-openplc-provision.sh",
                "0755",
                "start provisioning after the network is up",
            ),
        ],
    )
    return injects


def _shared(
    ctx: Context, hostname: str, subdir: str, files: list[tuple[str, str, str, str]]
) -> list[Injection]:
    """Copies of phenix-injects/<subdir>/ files, injected as they are."""
    out: Path = ctx.host_dir(hostname) / subdir
    return [
        Injection(
            src=copy(ctx.sources.inject_file(subdir, name), out / name),
            dst=dst,
            description=desc,
            permissions=mode,
        )
        for name, dst, mode, desc in files
    ]


def _packaged(
    ctx: Context, hostname: str, files: list[tuple[tuple[str, ...], str, str, str]]
) -> list[Injection]:
    """Copies of this package's templates, injected as they are."""
    out: Path = ctx.host_dir(hostname) / "static"
    return [
        Injection(
            src=copy(template(*parts), out / parts[-1]),
            dst=dst,
            description=desc,
            permissions=mode,
        )
        for parts, dst, mode, desc in files
    ]


# ---------------------------------------------------------------- FUXA


def fuxa_daq(ctx: Context, md: FuxaHost) -> str:
    if md.daq != "auto":
        return md.daq
    return "influxdb" if ctx.hosts_of(HistorianHost) else "sqlite"


def fuxa_settings(ctx: Context, hostname: str, md: FuxaHost) -> dict[str, Any]:
    """FUXA ``mysettings.json`` (DESIGN.md 12.2)."""
    settings: dict[str, Any] = {
        "secureEnabled": True,
        "secretCode": ctx.creds.fuxa_jwt_secret.get_secret_value(),
        "tokenExpiresIn": "12h",
        "nodeRedEnabled": False,
        "swaggerEnabled": False,
        "uiPort": md.port,
        "language": "en",
    }
    if fuxa_daq(ctx, md) == "influxdb":
        historians: dict[str, Any] = ctx.hosts_of(HistorianHost)
        if not historians:
            raise SourceError(
                f"{hostname} sends DAQ to InfluxDB, but the app has no historian host"
            )
        hist_name, hist_md = next(iter(historians.items()))
        # TODO(verify): FUXA's InfluxDB 2 DAQ store keys against server/runtime/storage.
        settings["daqstore"] = {
            "type": "influxDB",
            "url": f"http://{ctx.ip(hist_name, hist_md.interface)}:{ctx.app.historian.influx_port}",
            "organization": ctx.app.historian.org,
            "bucket": md.bucket or md.role,
            "credentials": {"token": ctx.creds.influx_token.get_secret_value()},
        }
    else:
        settings["daqstore"] = {"type": "SQlite"}
    return settings


def project_devices(project: dict[str, Any]) -> list[str]:
    devices: Any = project.get("devices") or {}
    return sorted(
        str(d.get("name"))
        for d in devices.values()
        if isinstance(d, dict) and d.get("name")
    )


def render_fuxa(ctx: Context, hostname: str, md: FuxaHost) -> list[Injection]:
    if md.project is None and md.role != "scada":
        raise SourceError(
            f"{hostname}: a {md.role} FUXA needs metadata.project; fuxa/build_project.py builds the scada project only"
        )
    out: Path = ctx.host_dir(hostname) / "fuxa"
    project: dict[str, Any] = ctx.sources.fuxa_project(md.project)
    devices: list[str] = project_devices(project)
    expect: list[str] = md.expect_devices if md.expect_devices is not None else devices
    missing: list[str] = [d for d in expect if d not in devices]
    if missing:
        raise SourceError(
            f"{hostname}: the FUXA project has no device {missing[0]} (metadata.expectDevices)"
        )

    project_path: Path = write(out / "project.json", to_json(project))
    settings: Path = write(
        out / "mysettings.json", to_json(fuxa_settings(ctx, hostname, md)), private=True
    )
    env: Path = write(
        out / "cm-fuxa.env",
        env_file(
            {
                "FUXA_URL": f"http://127.0.0.1:{md.port}",
                "FUXA_SEED_PASSWORD": "123456",
                "FUXA_ADMIN_PASSWORD": ctx.creds.fuxa_password.get_secret_value(),
                "FUXA_PROJECT": f"{CONF_DIR}/fuxa/project.json",
                # Read by cm-fuxa-provision once it checks a device list instead of plc-cellar.
                "FUXA_EXPECT_DEVICES": " ".join(expect),
            }
        ),
        private=True,
    )
    injects: list[Injection] = [
        Injection(
            src=settings,
            dst="/opt/fuxa/server/_appdata/mysettings.json",
            description="FUXA settings (secure mode, DAQ store, no Node-RED)",
            permissions="0600",
        ),
        Injection(
            src=project_path,
            dst=f"{CONF_DIR}/fuxa/project.json",
            description="FUXA project",
        ),
        Injection(
            src=env,
            dst="/etc/default/cm-fuxa",
            description="FUXA provisioning settings",
            permissions="0600",
        ),
    ]
    injects += _shared(
        ctx,
        hostname,
        "fuxa",
        [
            (
                "cm-fuxa-provision.sh",
                "/usr/local/bin/cm-fuxa-provision",
                "0755",
                "set the admin password and import the project",
            ),
            (
                "cm-fuxa-provision.service",
                "/etc/systemd/system/cm-fuxa-provision.service",
                "0644",
                "provisioning unit",
            ),
            (
                "70-fuxa-provision.sh",
                f"{STARTUP}/70-fuxa-provision.sh",
                "0755",
                "start provisioning after the network is up",
            ),
        ],
    )
    injects += _packaged(
        ctx,
        hostname,
        [
            (
                ("scripts", "cm-export-fuxa"),
                "/usr/local/bin/cm-export-fuxa",
                "0755",
                "alarm history and DAQ export (cleanup)",
            )
        ],
    )
    return injects


# ---------------------------------------------------------------- simulation


def helics_annotation(ctx: Context, count: int) -> list[dict[str, Any]]:
    return [
        {
            "broker": f"{ctx.app.sim_ctl.hostname}|{ctx.app.mgmt.interface}",
            "fed-count": count,
        }
    ]


def render_sim(
    ctx: Context, hostname: str, md: SimCtlHost | SimFederateHost
) -> list[Injection]:
    federates: list[str] = ctx.federates_of(hostname, md)
    if not federates:
        return []
    out: Path = ctx.host_dir(hostname) / "sim"
    broker: str = ctx.ip(ctx.app.sim_ctl.hostname, ctx.app.mgmt.interface)
    level: str = md.log_level if isinstance(md, SimFederateHost) else "INFO"
    injects: list[Injection] = [
        Injection(
            src=copy(ctx.sources.generated_file("helics.json"), out / "helics.json"),
            dst=f"{CONF_DIR}/helics.json",
            description="plantgen HELICS config",
        ),
        Injection(
            src=write(out / "federates", "\n".join(federates) + "\n"),
            dst=f"{CONF_DIR}/federates",
            description="federates this node runs",
        ),
    ]
    for fed in federates:
        env: Path = write(
            out / f"cm-federate-{fed}.env",
            env_file(
                {
                    "CM_HELICS_CONFIG": f"{CONF_DIR}/helics.json",
                    "HELICS_BROKER_ADDRESS": broker,
                    "CM_LOG_LEVEL": level,
                }
            ),
        )
        injects.append(
            Injection(
                src=env,
                dst=f"/etc/default/cm-federate-{fed}",
                description=f"{fed} federate settings",
            )
        )
    injects += _packaged(
        ctx,
        hostname,
        [
            (
                ("systemd", "cm-federate@.service"),
                "/etc/systemd/system/cm-federate@.service",
                "0644",
                "HELICS federate unit",
            ),
            (
                ("scripts", "60-cm-federates.sh"),
                f"{STARTUP}/60-cm-federates.sh",
                "0755",
                "start the federates once the broker answers",
            ),
        ],
    )
    return injects


# ---------------------------------------------------------------- historian


def grafana_datasources(ctx: Context) -> dict[str, Any]:
    h = ctx.app.historian
    return {
        "apiVersion": 1,
        "datasources": [
            {
                "name": f"InfluxDB {bucket}",
                "uid": f"cm-influx-{bucket}",
                "type": "influxdb",
                "access": "proxy",
                "url": f"http://127.0.0.1:{h.influx_port}",
                "isDefault": index == 0,
                "jsonData": {
                    "version": "Flux",
                    "organization": h.org,
                    "defaultBucket": bucket,
                },
                # Grafana expands $__env{...} at load time; the token stays in
                # /etc/default/cm-historian.
                "secureJsonData": {"token": "$__env{INFLUX_TOKEN}"},
            }
            for index, bucket in enumerate(h.buckets)
        ],
    }


def grafana_dashboard_provider() -> dict[str, Any]:
    return {
        "apiVersion": 1,
        "providers": [
            {
                "name": "cherrymountain",
                "folder": "CherryMountain",
                "type": "file",
                "disableDeletion": True,
                "allowUiUpdates": False,
                "options": {"path": "/var/lib/grafana/dashboards/cherrymountain"},
            }
        ],
    }


def _panel(
    pid: int, title: str, bucket: str, flux: str, unit: str, y: int
) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "datasource": {"type": "influxdb", "uid": f"cm-influx-{bucket}"},
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "targets": [{"refId": "A", "query": flux}],
    }


def grafana_dashboard(ctx: Context) -> dict[str, Any]:
    """A starter plant dashboard. TODO(verify): the measurement and field
    names FUXA's InfluxDB DAQ store writes; the queries filter on the tag
    name, which FUXA stores with each value."""
    h = ctx.app.historian
    scada: str = "scada" if "scada" in h.buckets else h.buckets[0]
    oee: str = "oee" if "oee" in h.buckets else h.buckets[0]
    ferm: str = (
        f'from(bucket: "{scada}") |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._value != "" and r.tag =~ /^CEL\\.FV10[0-9]\\.TT0[12]\\.PV$/)'
    )
    gravity: str = (
        f'from(bucket: "{scada}") |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        "  |> filter(fn: (r) => r.tag =~ /^CEL\\.FV10[0-9]\\.DT01\\.PV$/)"
    )
    packtags: str = (
        f'from(bucket: "{oee}") |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "packtags")'
    )
    return {
        "uid": "cm-plant",
        "title": "CherryMountain plant",
        "tags": ["cherrymountain"],
        "timezone": "browser",
        "schemaVersion": 39,
        "time": {"from": "now-6h", "to": "now"},
        "panels": [
            _panel(
                1,
                "Fermentation temperatures (side and cone)",
                scada,
                ferm,
                "fahrenheit",
                0,
            ),
            _panel(2, "Apparent extract", scada, gravity, "none", 8),
            _panel(3, "Packaging PackTags (OEE)", oee, packtags, "none", 16),
        ],
    }


def render_historian(ctx: Context, hostname: str, md: HistorianHost) -> list[Injection]:
    h = ctx.app.historian
    out: Path = ctx.host_dir(hostname) / "historian"
    bind: str = ctx.ip(hostname, md.interface)
    env: Path = write(
        out / "cm-historian.env",
        env_file(
            {
                "INFLUX_HOST": f"http://127.0.0.1:{h.influx_port}",
                "INFLUX_ORG": h.org,
                "INFLUX_TOKEN": ctx.creds.influx_token.get_secret_value(),
                "INFLUX_PASSWORD": ctx.creds.influx_password.get_secret_value(),
                "INFLUX_BUCKETS": " ".join(h.buckets),
                "INFLUX_RETENTION": f"{h.retention_hours}h"
                if h.retention_hours
                else "0",
                "GF_SECURITY_ADMIN_PASSWORD": ctx.creds.grafana_password.get_secret_value(),
                "GF_SERVER_HTTP_PORT": h.grafana_port,
            }
        ),
        private=True,
    )
    dropin: str = "[Service]\nEnvironmentFile=/etc/default/cm-historian\n"
    telegraf: Path = write(
        out / "telegraf.conf",
        mako(
            "telegraf.conf.mako",
            influx_port=h.influx_port,
            org=h.org,
            bucket="oee" if "oee" in h.buckets else h.buckets[-1],
            mqtt_port=h.mqtt_port,
            topic_root=h.mqtt_topic_root,
        ),
    )
    mosquitto: Path = write(
        out / "mosquitto.conf", mako("mosquitto.conf.mako", port=h.mqtt_port, bind=bind)
    )
    datasources: Path = write(
        out / "datasources.yaml",
        yaml.safe_dump(grafana_datasources(ctx), sort_keys=False),
    )
    provider: Path = write(
        out / "dashboards.yaml",
        yaml.safe_dump(grafana_dashboard_provider(), sort_keys=False),
    )
    dashboard: Path = write(out / "cm-plant.json", to_json(grafana_dashboard(ctx)))
    dropin_path: Path = write(out / "cherrymountain.conf", dropin)
    injects: list[Injection] = [
        Injection(
            src=env,
            dst="/etc/default/cm-historian",
            description="historian credentials and settings",
            permissions="0600",
        ),
        Injection(
            src=telegraf,
            dst="/etc/telegraf/telegraf.conf",
            description="Telegraf: PackTags from MQTT and host metrics",
        ),
        Injection(
            src=mosquitto,
            dst="/etc/mosquitto/conf.d/cherrymountain.conf",
            description="Mosquitto listeners",
        ),
        Injection(
            src=datasources,
            dst="/etc/grafana/provisioning/datasources/cherrymountain.yaml",
            description="Grafana InfluxDB data sources",
        ),
        Injection(
            src=provider,
            dst="/etc/grafana/provisioning/dashboards/cherrymountain.yaml",
            description="Grafana dashboard provider",
        ),
        Injection(
            src=dashboard,
            dst="/var/lib/grafana/dashboards/cherrymountain/cm-plant.json",
            description="Grafana plant dashboard",
        ),
    ]
    for unit in ("telegraf", "grafana-server"):
        injects.append(
            Injection(
                src=dropin_path,
                dst=f"/etc/systemd/system/{unit}.service.d/cherrymountain.conf",
                description=f"{unit} reads the historian settings",
            )
        )
    injects += _packaged(
        ctx,
        hostname,
        [
            (
                ("scripts", "cm-historian-provision"),
                "/usr/local/bin/cm-historian-provision",
                "0755",
                "InfluxDB setup and buckets",
            ),
            (
                ("systemd", "cm-historian-provision.service"),
                "/etc/systemd/system/cm-historian-provision.service",
                "0644",
                "InfluxDB provisioning unit",
            ),
            (
                ("scripts", "70-historian-provision.sh"),
                f"{STARTUP}/70-historian-provision.sh",
                "0755",
                "start InfluxDB provisioning",
            ),
            (
                ("scripts", "cm-export-historian"),
                "/usr/local/bin/cm-export-historian",
                "0755",
                "InfluxDB backup (cleanup)",
            ),
        ],
    )
    return injects


# ---------------------------------------------------------------- HMI


def kiosk_url(ctx: Context, md: HmiHost) -> str:
    fuxa_md: Any = ctx.hosts.get(md.fuxa)
    port: int = fuxa_md.port if isinstance(fuxa_md, FuxaHost) else 1881
    iface: str = md.interface
    return f"http://{ctx.ip(md.fuxa, iface)}:{port}/home/{md.view.replace(' ', '%20')}"


def render_hmi(ctx: Context, hostname: str, md: HmiHost) -> list[Injection]:
    out: Path = ctx.host_dir(hostname) / "kiosk"
    env: Path = write(out / "cm-kiosk.env", env_file({"KIOSK_URL": kiosk_url(ctx, md)}))
    return [
        Injection(src=env, dst="/etc/default/cm-kiosk", description="HMI kiosk URL"),
        *_packaged(
            ctx,
            hostname,
            [
                (
                    ("scripts", "cm-kiosk"),
                    "/usr/local/bin/cm-kiosk",
                    "0755",
                    "HMI kiosk launcher",
                ),
                (
                    ("desktop", "cm-kiosk.desktop"),
                    "/etc/xdg/autostart/cm-kiosk.desktop",
                    "0644",
                    "start the kiosk at login",
                ),
            ],
        ),
    ]


# ---------------------------------------------------------------- Guacamole


def _sql(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def guacamole_sql(ctx: Context, md: GuacamoleHost, salt: bytes) -> str:
    """Connections and the range admin for Guacamole's PostgreSQL schema.

    Guacamole stores ``SHA-256(password + uppercase hex(salt))``, so only the
    salted hash is written. Runs after the stock schema (initdb order 50).
    TODO(verify): against the cm-guacamole image's pinned schema version.
    """
    password: str = ctx.creds.guacamole_password.get_secret_value()
    digest: str = hashlib.sha256((password + salt.hex().upper()).encode()).hexdigest()
    lines: list[str] = [
        "-- Rendered by the cherrymountain app (DESIGN.md 13). Do not edit.",
        "BEGIN;",
        "INSERT INTO guacamole_entity (name, type) VALUES ('cmadmin', 'USER') ON CONFLICT DO NOTHING;",
        "INSERT INTO guacamole_user (entity_id, password_hash, password_salt, password_date)",
        "  SELECT entity_id, decode('"
        + digest
        + "', 'hex'), decode('"
        + salt.hex()
        + "', 'hex'), now()",
        "  FROM guacamole_entity WHERE name = 'cmadmin' AND type = 'USER'",
        "  ON CONFLICT (entity_id) DO UPDATE SET password_hash = EXCLUDED.password_hash, password_salt = EXCLUDED.password_salt;",
        "INSERT INTO guacamole_system_permission (entity_id, permission)",
        "  SELECT entity_id, 'ADMINISTER' FROM guacamole_entity WHERE name = 'cmadmin' AND type = 'USER'",
        "  ON CONFLICT DO NOTHING;",
    ]
    for conn in md.connections:
        default_port: int = {"rdp": 3389, "ssh": 22, "vnc": 5900}[conn.protocol]
        params: dict[str, str] = {
            "hostname": ctx.ip(conn.hostname, conn.interface),
            "port": str(conn.port or default_port),
        }
        if conn.username:
            params["username"] = conn.username
        if conn.protocol == "rdp":
            params.update({"security": "any", "ignore-cert": "true"})
        if md.recording:
            params.update(
                {
                    "recording-path": "${HISTORY_PATH}/${HISTORY_UUID}",
                    "create-recording-path": "true",
                }
            )
        name: str = _sql(conn.name)
        lines += [
            "INSERT INTO guacamole_connection (connection_name, protocol)",
            f"  SELECT {name}, {_sql(conn.protocol)} WHERE NOT EXISTS",
            f"  (SELECT 1 FROM guacamole_connection WHERE connection_name = {name} AND parent_id IS NULL);",
        ]
        for key, value in params.items():
            lines += [
                "INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)",
                f"  SELECT connection_id, {_sql(key)}, {_sql(value)} FROM guacamole_connection",
                f"  WHERE connection_name = {name} AND parent_id IS NULL",
                "  ON CONFLICT (connection_id, parameter_name) DO UPDATE SET parameter_value = EXCLUDED.parameter_value;",
            ]
        lines += [
            "INSERT INTO guacamole_connection_permission (entity_id, connection_id, permission)",
            "  SELECT e.entity_id, c.connection_id, 'READ' FROM guacamole_entity e, guacamole_connection c",
            f"  WHERE e.name = 'cmadmin' AND e.type = 'USER' AND c.connection_name = {name}",
            "  ON CONFLICT DO NOTHING;",
        ]
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def render_guacamole(ctx: Context, hostname: str, md: GuacamoleHost) -> list[Injection]:
    """STUB: cm-guacamole does not exist yet. The SQL and settings follow the
    upstream Guacamole PostgreSQL schema and docker images."""
    out: Path = ctx.host_dir(hostname) / "guacamole"
    salt_path: Path = out / "salt"
    if not salt_path.is_file():
        write(salt_path, secrets.token_hex(32), private=True)
    salt: bytes = bytes.fromhex(salt_path.read_text(encoding="utf-8").strip())
    sql: Path = write(
        out / "50-cherrymountain.sql", guacamole_sql(ctx, md, salt), private=True
    )
    env: Path = write(
        out / "cm-guacamole.env",
        env_file(
            {
                "GUAC_RECORDING": "true" if md.recording else "false",
                "TOTP_ENABLED": "true" if md.totp else "false",
            }
        ),
    )
    return [
        Injection(
            src=sql,
            dst="/opt/cherrymountain/guacamole/initdb/50-cherrymountain.sql",
            description="Guacamole connections and range admin",
            permissions="0600",
        ),
        Injection(
            src=env, dst="/etc/default/cm-guacamole", description="Guacamole settings"
        ),
    ]


# ---------------------------------------------------------------- Wazuh


def render_wazuh(ctx: Context) -> dict[str, list[Injection]]:
    """STUB: cm-wazuh and the agent images do not exist yet. Agents enroll with
    a range password (authd use_password)."""
    cfg = ctx.app.wazuh
    if cfg is None:
        return {}
    manager_ip: str = ctx.ip(cfg.manager, cfg.interface)
    password: str = ctx.creds.wazuh_enrollment.get_secret_value()
    agents: list[str] = list(cfg.agents) or [
        name
        for name, md in ctx.hosts.items()
        if name != cfg.manager
        and not isinstance(md, OpenPlcHost)
        and _os(ctx, name) == "linux"
    ]
    plan: dict[str, list[Injection]] = {}
    out: Path = ctx.host_dir(cfg.manager) / "wazuh"
    env: Path = write(
        out / "cm-wazuh-manager.env",
        env_file({"WAZUH_REGISTRATION_PASSWORD": password}),
        private=True,
    )
    plan[cfg.manager] = [
        Injection(
            src=env,
            dst="/etc/default/cm-wazuh-manager",
            description="Wazuh enrollment password",
            permissions="0600",
        ),
        *_packaged(
            ctx,
            cfg.manager,
            [
                (
                    ("scripts", "80-cm-wazuh-manager.sh"),
                    f"{STARTUP}/80-cm-wazuh-manager.sh",
                    "0755",
                    "enable password enrollment",
                )
            ],
        ),
    ]
    for agent in agents:
        labels: Any = (ctx.nodes.get(agent) or {}).get("labels") or {}
        group: str = str(labels.get("zone", "default"))
        aout: Path = ctx.host_dir(agent) / "wazuh"
        aenv: Path = write(
            aout / "cm-wazuh-agent.env",
            env_file(
                {
                    "WAZUH_MANAGER": manager_ip,
                    "WAZUH_ENROLLMENT_PORT": cfg.enrollment_port,
                    "WAZUH_AGENT_GROUP": group,
                    "WAZUH_REGISTRATION_PASSWORD": password,
                }
            ),
            private=True,
        )
        plan[agent] = [
            Injection(
                src=aenv,
                dst="/etc/default/cm-wazuh-agent",
                description="Wazuh agent enrollment",
                permissions="0600",
            ),
            *_packaged(
                ctx,
                agent,
                [
                    (
                        ("scripts", "80-cm-wazuh-agent.sh"),
                        f"{STARTUP}/80-cm-wazuh-agent.sh",
                        "0755",
                        "enroll the Wazuh agent",
                    )
                ],
            ),
        ]
    return plan


def _os(ctx: Context, hostname: str) -> str:
    node: Any = ctx.nodes.get(hostname) or {}
    return str((node.get("hardware") or {}).get("os_type") or "linux").lower()


# ---------------------------------------------------------------- MGMT nftables


def mgmt_members(ctx: Context) -> dict[str, str]:
    """hostname -> MGMT address, for every topology node with a MGMT NIC."""
    members: dict[str, str] = {}
    for name, node in ctx.nodes.items():
        for iface in (node.get("network") or {}).get("interfaces") or []:
            if iface.get("name") == ctx.app.mgmt.interface and iface.get("address"):
                members[name] = str(iface["address"])
    return members


def render_mgmt(ctx: Context) -> dict[str, list[Injection]]:
    policy = ctx.app.mgmt
    if not policy.enabled:
        return {}
    members: dict[str, str] = mgmt_members(ctx)
    hub: str = ctx.app.sim_ctl.hostname
    if hub not in members:
        raise SourceError(
            f"{hub} has no {policy.interface} interface; the MGMT policy needs the broker's address"
        )
    peers: list[str] = sorted(set(members.values()))
    broker_ports: str = ", ".join(str(p) for p in sorted(set(policy.broker_ports)))
    if policy.federate_port_min > policy.federate_port_max:
        raise SourceError("metadata.mgmt.federatePortMin is above federatePortMax")
    federate_ports: str = f"{policy.federate_port_min}-{policy.federate_port_max}"
    plan: dict[str, list[Injection]] = {}
    for name in sorted(members):
        if name in policy.exclude or _os(ctx, name) != "linux":
            continue
        rules: Path = write(
            ctx.host_dir(name) / "mgmt" / "nftables-mgmt.nft",
            mako(
                "nftables-mgmt.nft.mako",
                hostname=name,
                iface=policy.interface,
                hub=name == hub,
                hub_ip=members[hub],
                peers=peers,
                broker_ports=broker_ports,
                federate_ports=federate_ports,
                opensearch_port=policy.opensearch_port,
            ),
        )
        plan[name] = [
            Injection(
                src=rules,
                dst=f"{CONF_DIR}/nftables-mgmt.nft",
                description="MGMT nftables policy",
            ),
            *_packaged(
                ctx,
                name,
                [
                    (
                        ("scripts", "04-cm-mgmt-nftables.sh"),
                        f"{STARTUP}/04-cm-mgmt-nftables.sh",
                        "0755",
                        "load the MGMT nftables policy",
                    )
                ],
            ),
        ]
    return plan


# ---------------------------------------------------------------- BBMD


def bbmd_config(ctx: Context, hostname: str, md: BbmdHost) -> dict[str, Any]:
    """The BDT for one BBMD: itself first, then its peers, all without a mask,
    so each BBMD unicasts to its peers (DESIGN.md 10.4)."""
    own: str = ctx.ip(hostname, md.interface)
    entries: list[dict[str, Any]] = [{"address": own, "port": md.port}]
    for peer in md.peers:
        peer_md: Any = ctx.hosts.get(peer)
        iface: str = peer_md.interface if isinstance(peer_md, BbmdHost) else "IF0"
        port: int = peer_md.port if isinstance(peer_md, BbmdHost) else md.port
        entries.append({"address": ctx.ip(peer, iface), "port": port})
    node: Any = ctx.nodes[hostname]
    mask: Any = next(
        (
            i.get("mask")
            for i in (node.get("network") or {}).get("interfaces") or []
            if i.get("name") == md.interface
        ),
        24,
    )
    prefix: int = md.prefix if md.prefix is not None else int(mask)
    return {
        "address": f"{own}/{prefix}",
        "port": md.port,
        "bdt": entries,
    }


def render_bbmd(ctx: Context, hostname: str, md: BbmdHost) -> list[Injection]:
    """The BDT config for cm-bbmd.service. The service itself lives in
    phenix-injects/bbmd/ (another change); without it only the config is
    injected and a warning is logged."""
    out: Path = ctx.host_dir(hostname) / "bbmd"
    cfg: dict[str, Any] = bbmd_config(ctx, hostname, md)
    path: Path = write(out / "bbmd.json", to_json(cfg))
    bdt: str = " ".join(f"{e['address']}:{e['port']}" for e in cfg["bdt"])
    env: Path = write(
        out / "cm-bbmd.env",
        env_file(
            {"CM_BBMD_ADDRESS": f"{cfg['address']}:{cfg['port']}", "CM_BBMD_BDT": bdt}
        ),
    )
    if not ctx.sources.has_injects_dir("bbmd"):
        logger.warning(
            f"{hostname}: phenix-injects/bbmd/ is missing; injecting the BDT config only"
        )
    return [
        Injection(
            src=path, dst=f"{CONF_DIR}/bbmd.json", description="BBMD address and BDT"
        ),
        Injection(
            src=env,
            dst="/etc/default/cm-bbmd",
            description="BBMD settings for cm-bbmd.service",
        ),
    ]


# ---------------------------------------------------------------- all


def render_all(ctx: Context) -> dict[str, list[Injection]]:
    """Every injection, by host."""
    plan: dict[str, list[Injection]] = {}

    def add(hostname: str, injects: list[Injection]) -> None:
        plan.setdefault(hostname, []).extend(injects)

    for name, md in ctx.hosts.items():
        if isinstance(md, OpenPlcHost):
            add(name, render_openplc(ctx, name, md))
        elif isinstance(md, FuxaHost):
            add(name, render_fuxa(ctx, name, md))
        elif isinstance(md, (SimCtlHost, SimFederateHost)):
            add(name, render_sim(ctx, name, md))
        elif isinstance(md, HistorianHost):
            add(name, render_historian(ctx, name, md))
        elif isinstance(md, HmiHost):
            add(name, render_hmi(ctx, name, md))
        elif isinstance(md, GuacamoleHost):
            add(name, render_guacamole(ctx, name, md))
        elif isinstance(md, BbmdHost):
            add(name, render_bbmd(ctx, name, md))
    for name, injects in render_wazuh(ctx).items():
        add(name, injects)
    for name, injects in render_mgmt(ctx).items():
        add(name, injects)
    return plan
