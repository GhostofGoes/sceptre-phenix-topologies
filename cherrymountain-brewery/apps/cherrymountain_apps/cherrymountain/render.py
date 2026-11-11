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

import base64
import hashlib
import importlib
import json
import secrets
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, ClassVar, Final

import yaml
from phenix_apps.common import utils
from phenix_apps.common.logger import logger
from pydantic import BaseModel, ConfigDict

from . import brand
from .credentials import RangeSecrets, host_keys, totp_secret, write_private
from .metadata import (
    AppMetadata,
    FuxaHost,
    GuacamoleHost,
    HistorianHost,
    HmiHost,
    HostBase,
    OpenPlcHost,
    EwsHost,
    RioHost,
    SimCtlHost,
    SimFederateHost,
)
from .sources import DebugMap, SourceError, Sources

TEMPLATES: Final[Path] = Path(__file__).parent / "templates"
STARTUP: Final[str] = "/etc/phenix/startup"
CONF_DIR: Final[str] = "/etc/cherrymountain"
OPENPLC_DIR: Final[str] = "/opt/cherrymountain/openplc"
#: plant.yaml northbound -> OpenPLC plugin.
NORTHBOUND_PLUGIN: Final[dict[str, str]] = {
    "modbus": "modbus_slave",
    "opcua": "opcua",
    "s7comm": "s7comm",
}


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


#: The OpenPLC runtime account the app creates on every PLC and gives the EWS.
OPENPLC_USER: Final[str] = "admin"


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
        # Operator-set secrets are limited to shell-safe characters
        # (metadata.SECRET_CHARS), so only spaces need quoting here.
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
                            # FUXA signs in anonymously. The plugin gives an
                            # anonymous session the viewer role unless the
                            # profile names another (v4.3.2 user_manager.py),
                            # and viewer is read-only below: without this no
                            # setpoint, phase command, or entry permit could
                            # be written to an OPC UA PLC.
                            "anonymous_role": "operator",
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
    """``conf/s7comm.json``: one data block per image, the numbering that
    fuxa/build_project.py addresses (its ``S7_DATA_BLOCKS``): DB1 %QW, DB2 %IW,
    DB3 %MW, DB4 %IX, DB5 %QX, each from buffer 0. The bit images take one byte
    per eight bits (``DB4.DBX<byte>.<bit>``).

    TODO(verify): the openplc-v4 spike exercised only ``int_output`` and
    ``int_input``; the memory and bit mappings follow the v4.3.2 plugin source
    (s7comm_config.c), not a run.
    """
    qw: int = int(layout["holding_registers"]["qw_count"])
    iw: int = int(layout["input_registers"]["iw_count"])
    mw: int = int(layout["holding_registers"]["mw_count"])
    ix: int = int(layout["discrete_inputs"]["ix_bits"])
    qx: int = int(layout["coils"]["qx_bits"])

    def block(number: int, name: str, size: int, kind: str) -> dict[str, Any]:
        return {
            "db_number": number,
            "description": name,
            "size_bytes": max(size, 1),
            "mapping": {"type": kind, "start_buffer": 0},
        }

    return {
        "server": {"enabled": True, "bind_address": "0.0.0.0", "port": port},
        "data_blocks": [
            block(1, "QW", qw * 2, "int_output"),
            block(2, "IW", iw * 2, "int_input"),
            block(3, "MW", mw * 2, "int_memory"),
            block(4, "IX", -(-ix // 8), "bool_input"),
            block(5, "QX", -(-qx // 8), "bool_output"),
        ],
    }


def northbound(ctx: Context, hostname: str, md: OpenPlcHost) -> str:
    """The northbound plugin: the metadata's, else plant.yaml's."""
    if md.northbound is not None:
        return md.northbound
    planned: Any = ctx.sources.plant().hosts.get(hostname)
    return NORTHBOUND_PLUGIN.get(
        getattr(planned, "northbound", "modbus"), "modbus_slave"
    )


def render_openplc(ctx: Context, hostname: str, md: OpenPlcHost) -> list[Injection]:
    src: Sources = ctx.sources
    plugin_nb: str = northbound(ctx, hostname, md)
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
    stale: list[str] = src.zip_conf_drift(program, plantgen_dir)
    if stale:
        # The boot script replaces conf/ anyway, but the program's located
        # variables were compiled against these maps (DESIGN.md 12.6).
        logger.warning(
            f"{hostname}: the {program} ZIP was built with other plugin configs than "
            f"plant/generated/{src.variant} ({', '.join(stale)}); rebuild it for this variant"
        )
    for plugin in ("modbus_master", "modbus_slave"):
        path: Path = plantgen_dir / f"{plugin}.json"
        if (
            plugin == "modbus_slave"
            and plugin_nb != "modbus_slave"
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

    if plugin_nb == "opcua":
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
    elif plugin_nb == "s7comm":
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
                "OPENPLC_USER": OPENPLC_USER,
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


# ---------------------------------------------------------------- brand


def brand_grafana(ctx: Context, hostname: str) -> list[Injection]:
    """The brewery's images for the Grafana of a host (the historian, and sim-ctl where
    ground truth runs): the brand files, copied from phenix-injects/brand/, and the
    script that puts them over Grafana's own (brand.py, phenix-injects/grafana/)."""
    return [
        *_shared(
            ctx,
            hostname,
            "brand",
            [(f.name, f.dst, "0644", f.description) for f in brand.GRAFANA_FILES],
        ),
        *_shared(
            ctx,
            hostname,
            "grafana",
            [
                (
                    "cm-grafana-brand",
                    "/usr/local/bin/cm-grafana-brand",
                    "0755",
                    "put the brewery's images into Grafana",
                ),
                (
                    "67-grafana-brand.sh",
                    f"{STARTUP}/67-grafana-brand.sh",
                    "0755",
                    "brand Grafana at boot",
                ),
            ],
        ),
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
    """The devices the import must produce, by name. Not FUXA's own server device
    (``FuxaServer``, which holds the batch engine's tags): FUXA always has it, and its
    name has a space, which a space-separated list cannot carry."""
    devices: Any = project.get("devices") or {}
    return sorted(
        str(d.get("name"))
        for d in devices.values()
        if isinstance(d, dict) and d.get("name") and d.get("type") != "FuxaServer"
    )


def render_fuxa(ctx: Context, hostname: str, md: FuxaHost) -> list[Injection]:
    out: Path = ctx.host_dir(hostname) / "fuxa"
    project: dict[str, Any] = ctx.sources.fuxa_project(md.project, hostname)
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
                "FUXA_OPERATOR_PASSWORD": ctx.creds.fuxa_operator_password.get_secret_value(),
                "FUXA_PROJECT": f"{CONF_DIR}/fuxa/project.json",
                # cm-fuxa-provision requires each of these after the import.
                "FUXA_EXPECT_DEVICES": " ".join(expect),
                # 66-fuxa-brand.sh writes it into the client's index.html.
                "FUXA_TITLE": brand.FUXA_TITLES[md.role],
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
                "set the admin password, add the operator account, and import the project",
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
    # The brand (brand.py): the tab icon and the mark, copied from phenix-injects/brand/,
    # and the script that sets the page title.
    injects += _shared(
        ctx,
        hostname,
        "brand",
        [(f.name, f.dst, "0644", f.description) for f in brand.FUXA_FILES],
    )
    injects += _shared(
        ctx,
        hostname,
        "fuxa",
        [
            (
                "66-fuxa-brand.sh",
                f"{STARTUP}/66-fuxa-brand.sh",
                "0755",
                "set FUXA's page title",
            )
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
    out: Path = ctx.host_dir(hostname) / "sim"
    truth: list[Injection] = []
    if isinstance(md, SimCtlHost):
        helics: Any = ctx.sources.generated_json("helics.json")
        gt: Any = helics.get("ground_truth") if isinstance(helics, dict) else None
        if gt:
            # cm-sim starts OpenSearch and Grafana only where this file exists
            # (DESIGN.md 9.5, 15.7); the ci variant has no ground truth.
            truth.append(
                Injection(
                    src=write(
                        out / "ground-truth.env",
                        env_file(
                            {
                                "CM_TRUTH_INDEX_BASE": str(gt.get("index_base", "")),
                                "GF_SECURITY_ADMIN_PASSWORD": ctx.creds.grafana_password.get_secret_value(),
                            }
                        ),
                        private=True,
                    ),
                    dst=f"{CONF_DIR}/ground-truth.env",
                    description="ground truth on sim-ctl: OpenSearch and Grafana",
                    # It holds Grafana's admin password. systemd reads it as
                    # root (EnvironmentFile, ConditionPathExists).
                    permissions="0600",
                )
            )
            # The brand: Grafana's images, and the dashboard with the brand's series
            # colors (the image no longer carries a dashboard of its own).
            truth += brand_grafana(ctx, hostname)
            truth.append(
                Injection(
                    src=write(
                        out / "cm-ground-truth.json",
                        to_json(ground_truth_dashboard(ctx)),
                    ),
                    dst="/var/lib/grafana/dashboards/cherrymountain/cm-ground-truth.json",
                    description="ground-truth Grafana dashboard",
                )
            )
    federates: list[str] = ctx.federates_of(hostname, md)
    if not federates:
        return truth
    broker: str = ctx.ip(ctx.app.sim_ctl.hostname, ctx.app.mgmt.interface)
    level: str = md.log_level if isinstance(md, SimFederateHost) else "INFO"
    injects: list[Injection] = [
        *truth,
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
    if isinstance(md, SimCtlHost) and "weather" in federates:
        injects += render_viz(ctx, hostname, out)
    return injects


def render_viz(ctx: Context, hostname: str, out: Path) -> list[Injection]:
    """cm-viz on sim-ctl, next to the event API it reads (DESIGN.md 15.10):
    its settings, its hardened unit (loopback only), and the startup hook."""
    sim_ctl = ctx.app.sim_ctl
    env: Path = write(
        out / "cm-viz.env",
        env_file(
            {
                "CM_VIZ_SIMCTL_URL": f"http://127.0.0.1:{sim_ctl.event_port}",
                "CM_VIZ_PORT": str(sim_ctl.viz_port),
                "CM_HELICS_CONFIG": f"{CONF_DIR}/helics.json",
            }
        ),
    )
    return [
        Injection(src=env, dst="/etc/default/cm-viz", description="cm-viz settings"),
        *_packaged(
            ctx,
            hostname,
            [
                (
                    ("systemd", "cm-viz.service"),
                    "/etc/systemd/system/cm-viz.service",
                    "0644",
                    "cm-viz unit (white-cell 3D view)",
                ),
                (
                    ("scripts", "61-cm-viz.sh"),
                    f"{STARTUP}/61-cm-viz.sh",
                    "0755",
                    "start cm-viz after the federates",
                ),
            ],
        ),
    ]


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
    pid: int,
    title: str,
    bucket: str,
    flux: str,
    unit: str,
    y: int,
    overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "datasource": {"type": "influxdb", "uid": f"cm-influx-{bucket}"},
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": overrides or []},
        "targets": [{"refId": "A", "query": flux}],
    }


#: The four fermenters of the cellar, in the order their lines take the brand's series colors.
FERMENTERS: Final[tuple[str, ...]] = ("FV101", "FV102", "FV103", "FV104")


def fermenter_overrides(
    ctx: Context, sensors: tuple[str, ...], dashed: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """One color per fermenter from the brand's chart series (``tokens.json``, ember
    first), one override per sensor line; a ``dashed`` sensor's lines are dashed, so the
    side and cone temperatures of a fermenter differ by dash, not by color."""
    colors: list[str] = brand.series_colors(
        ctx.sources.inject_file("brand", "tokens.json")
    )
    if len(colors) < len(FERMENTERS):
        raise SourceError(
            "tokens.json has fewer chart series colors than the cellar has fermenters"
        )
    return [
        brand.line_color(rf"\.{fv}\.{sensor}\.PV", colors[i], dashed=sensor in dashed)
        for i, fv in enumerate(FERMENTERS)
        for sensor in sensors
    ]


#: Where the historian finds the records task (cm-historian-provision).
RECORDS_TASK: Final[str] = "/etc/cherrymountain/cm-records.flux"


def records_task(ctx: Context) -> str:
    """The InfluxDB task that copies, once a day, the last value of each TTB
    record series from the scada bucket into the records bucket (DESIGN.md
    12.4). The raw data ages out after 30 days; the daily values stay three
    years. TODO(verify): FUXA's DAQ field layout, as for the dashboard."""
    h = ctx.app.historian
    scada: str = "scada" if "scada" in h.buckets else h.buckets[0]
    return (
        "// Rendered by the cherrymountain app (DESIGN.md 12.4): the daily TTB records copy.\n"
        'option task = {name: "cm-ttb-records", every: 1d, offset: 10m}\n\n'
        f'from(bucket: "{scada}")\n'
        "  |> range(start: -task.every)\n"
        f"  |> filter(fn: (r) => r.tag =~ /{h.records_tags}/)\n"
        "  |> aggregateWindow(every: 1d, fn: last, createEmpty: false)\n"
        f'  |> to(bucket: "{h.records_bucket or scada}", org: "{h.org}")\n'
    )


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
                fermenter_overrides(ctx, ("TT01", "TT02"), dashed=("TT02",)),
            ),
            _panel(
                2,
                "Apparent extract",
                scada,
                gravity,
                "none",
                8,
                fermenter_overrides(ctx, ("DT01",)),
            ),
            _panel(3, "Packaging PackTags (OEE)", oee, packtags, "none", 16),
        ],
    }


def ground_truth_dashboard(ctx: Context) -> dict[str, Any]:
    """The white cell's dashboard on sim-ctl (OpenSearch ground truth): documents per
    federate, and the scenario events. It was a heredoc in the cm-sim image; it is
    rendered here so the federates' lines can take the brand's series colors from
    ``tokens.json`` (one color per federate of plant.yaml, ember first, the cycle
    starting over after the fifth)."""
    colors: list[str] = brand.series_colors(
        ctx.sources.inject_file("brand", "tokens.json")
    )
    federates: list[str] = list(ctx.sources.plant().federates)
    overrides: list[dict[str, Any]] = [
        brand.line_color(rf"\b{name}\b", colors[i % len(colors)])
        for i, name in enumerate(federates)
    ]
    source: dict[str, str] = {
        "type": "grafana-opensearch-datasource",
        "uid": "cm-ground-truth",
    }
    return {
        "uid": "cm-ground-truth",
        "title": "CherryMountain ground truth",
        "schemaVersion": 39,
        "time": {"from": "now-24h", "to": "now+24h"},
        "refresh": "30s",
        "panels": [
            {
                "id": 1,
                "type": "timeseries",
                "title": "Documents per federate",
                "gridPos": {"h": 8, "w": 24, "x": 0, "y": 0},
                "datasource": source,
                "fieldConfig": {"defaults": {}, "overrides": overrides},
                "targets": [
                    {
                        "refId": "A",
                        "query": "*",
                        "metrics": [{"id": "1", "type": "count"}],
                        "bucketAggs": [
                            {
                                "id": "2",
                                "type": "terms",
                                "field": "federate",
                                "settings": {"size": "10"},
                            },
                            {
                                "id": "3",
                                "type": "date_histogram",
                                "field": "@timestamp",
                                "settings": {"interval": "auto"},
                            },
                        ],
                        "timeField": "@timestamp",
                    }
                ],
            },
            {
                "id": 2,
                "type": "logs",
                "title": "Scenario events",
                "gridPos": {"h": 10, "w": 24, "x": 0, "y": 8},
                "datasource": source,
                "targets": [
                    {
                        "refId": "A",
                        "query": "federate:weather AND _exists_:events",
                        "metrics": [{"id": "1", "type": "logs"}],
                        "bucketAggs": [],
                        "timeField": "@timestamp",
                    }
                ],
            },
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
                "INFLUX_RECORDS_BUCKET": h.records_bucket,
                "INFLUX_RECORDS_RETENTION": f"{h.records_retention_hours}h"
                if h.records_retention_hours
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
    records: Path = write(out / "cm-records.flux", records_task(ctx))
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
        Injection(
            src=records,
            dst=RECORDS_TASK,
            description="InfluxDB task: the daily TTB records copy",
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
    injects += brand_grafana(ctx, hostname)
    return injects


# ---------------------------------------------------------------- HMI

#: The FUXA account the kiosks sign in as: the one cm-fuxa-provision.sh makes
#: (``OPERATOR_USER``), in FUXA's Operator group.
FUXA_OPERATOR_USER: Final[str] = "operator"
#: What the kiosk reads its sign-in from (templates/scripts/cm-kiosk).
KIOSK_CREDENTIALS: Final[str] = "/etc/default/cm-kiosk-credentials"


def kiosk_url(ctx: Context, md: HmiHost) -> str:
    """The kiosk's start page. FUXA's home page takes the view to open from the
    ``viewName`` query parameter and looks it up by name (client
    home.component.ts at the cm-fuxa commit and in 1.3.5); a name in the path
    (``/home/<view>``) is routed but ignored, and the project's start view
    opens instead."""
    fuxa_md: Any = ctx.hosts.get(md.fuxa)
    port: int = fuxa_md.port if isinstance(fuxa_md, FuxaHost) else 1881
    iface: str = md.interface
    return f"http://{ctx.ip(md.fuxa, iface)}:{port}/home?viewName={md.view.replace(' ', '%20')}"


def render_hmi(ctx: Context, hostname: str, md: HmiHost) -> list[Injection]:
    """The kiosk's URL (world readable), and its sign-in as the FUXA operator
    account: a file of its own, 0600, which only the kiosk's user reads (the desktops
    log in as root), and which only the HMI hosts get. It follows the Malcolm
    credentials file, ``render_malcolm``."""
    out: Path = ctx.host_dir(hostname) / "kiosk"
    env: Path = write(out / "cm-kiosk.env", env_file({"KIOSK_URL": kiosk_url(ctx, md)}))
    sign_in: Path = write(
        out / "cm-kiosk-credentials.env",
        env_file(
            {
                "KIOSK_USER": FUXA_OPERATOR_USER,
                "KIOSK_PASSWORD": ctx.creds.fuxa_operator_password.get_secret_value(),
            }
        ),
        private=True,
    )
    return [
        Injection(src=env, dst="/etc/default/cm-kiosk", description="HMI kiosk URL"),
        Injection(
            src=sign_in,
            dst=KIOSK_CREDENTIALS,
            description="HMI kiosk sign-in as the FUXA operator (per-experiment)",
            permissions="0600",
        ),
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
    """Guacamole for the cm-guacamole image. The SQL and settings follow the
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
            # Not 0600: the postgres container runs its init files as its own
            # (non-root) user, after dropping root, and stops the first-run setup
            # on a file it cannot read. The file holds only a salted hash of a
            # generated password, like the stock schema next to it (0644).
            permissions="0644",
        ),
        Injection(
            src=env, dst="/etc/default/cm-guacamole", description="Guacamole settings"
        ),
    ]


# ---------------------------------------------------------------- EWS


def render_ews(ctx: Context, hostname: str, md: EwsHost) -> list[Injection]:
    """/etc/default/cm-ews: the OpenPLC REST credentials cm_plc_tool.py reads
    (the same ones the app gives every PLC), root-only."""
    env: Path = write(
        ctx.host_dir(hostname) / "cm-ews.env",
        env_file(
            {
                "OPENPLC_USER": OPENPLC_USER,
                "OPENPLC_PASSWORD": ctx.creds.openplc_password.get_secret_value(),
            }
        ),
        private=True,
    )
    return [
        Injection(
            src=env,
            dst="/etc/default/cm-ews",
            description="OpenPLC REST credentials for cm_plc_tool.py",
            permissions="0600",
        )
    ]


# ---------------------------------------------------------------- ot-sim telnet

OTSIM_CONFIG: Final[str] = "/etc/ot-sim/config.xml"


def add_telnet(config_xml: str, endpoint: str, banner: str) -> str:
    """config.xml with the ot-sim telnet module: a CPU ``module`` entry that
    starts ``ot-sim-telnet-module`` and a ``telnet`` element (ot-sim's
    config/single-device/device.xml shows both). Idempotent."""
    root: ET.Element = ET.fromstring(config_xml)
    cpu: ET.Element | None = root.find("cpu")
    if cpu is None:
        raise SourceError("the ot-sim config has no <cpu> element")
    if root.find("telnet") is None:
        module: ET.Element = ET.SubElement(cpu, "module", {"name": "telnet"})
        module.text = "ot-sim-telnet-module {{config_file}}"
        telnet: ET.Element = ET.SubElement(root, "telnet", {"name": "telnet"})
        ET.SubElement(telnet, "endpoint").text = endpoint
        ET.SubElement(telnet, "banner").text = banner
    ET.indent(root)
    # No XML declaration or comment before <ot-sim>: the C++ DNP3 module rejects
    # a root comment (DESIGN.md 12.3).
    return ET.tostring(root, encoding="unicode") + "\n"


# ---------------------------------------------------------------- ot-sim BACnet

#: Where the BACnet module goes on a device (phenix-injects/otsim/).
BACNET_MODULE: Final[str] = "/usr/local/lib/cm-otsim/cm_otsim_bacnet.py"
#: The CPU module's command for it: the venv that holds ot-sim's Python
#: modules also holds BACpypes3 (cm-otsim image).
BACNET_COMMAND: Final[str] = f"/opt/otsim-venv/bin/python -I {BACNET_MODULE} {{{{config_file}}}}"
#: Process patterns of the two modules this app adds, for SoH.
BACNET_PROCESSES: Final[tuple[str, ...]] = ("ot-sim-io-module", "cm_otsim_bacnet.py")


def bacnet_endpoint(ctx: Context, hostname: str) -> Any:
    """plant.yaml's BACnet endpoint of ``hostname``, or None. The phenix
    ot-sim app knows only Modbus and DNP3, so for such a host it renders
    neither an I/O module nor a protocol module (DESIGN.md 12.3)."""
    planned: Any = ctx.sources.plant().hosts.get(hostname)
    return getattr(planned, "bacnet", None)


def bacnet_hosts(ctx: Context) -> list[str]:
    """The ot-sim devices of this app that serve BACnet."""
    return [
        name
        for name, md in ctx.hosts.items()
        if isinstance(md, RioHost) and bacnet_endpoint(ctx, name) is not None
    ]


def io_element(ctx: Context, hostname: str) -> ET.Element:
    """The ``io`` element (ot-sim's HELICS I/O module) of a BACnet device,
    built from plantgen's helics.json the way the phenix ot-sim app builds it
    for a Modbus device: a subscription per point, and the writable tags under
    the federate endpoint that receives their updates."""
    helics: Any = ctx.sources.generated_json("helics.json")
    spec: Any = (helics.get("io_federates") or {}).get(hostname)
    if spec is None:
        raise SourceError(f"helics.json has no I/O federate for {hostname}")
    types: dict[str, str] = {
        pub["key"]: pub["type"]
        for fed in helics["federates"].values()
        for pub in fed["publications"]
    }
    io: ET.Element = ET.Element("io", {"name": "helics-federate"})
    ET.SubElement(io, "broker-endpoint").text = ctx.ip(ctx.app.sim_ctl.hostname, ctx.app.mgmt.interface)
    ET.SubElement(io, "federate-name").text = str(spec["federate_name"])
    ET.SubElement(io, "federate-log-level").text = "SUMMARY"
    ET.SubElement(io, "end-time").text = str(int(helics["end_time_s"]))
    for key in spec["subscriptions"]:
        if key not in types:
            raise SourceError(f"helics.json: no federate publishes {key}, which {hostname} subscribes to")
        if types[key] not in ("double", "boolean"):
            raise SourceError(f"helics.json: {key} is a {types[key]}, which ot-sim's I/O module cannot read")
        sub: ET.Element = ET.SubElement(io, "subscription")
        ET.SubElement(sub, "key").text = key
        ET.SubElement(sub, "tag").text = key.split("/", 1)[1]
        ET.SubElement(sub, "type").text = types[key]
    endpoints: dict[str, ET.Element] = {}
    for send in spec.get("sends") or []:
        name: str = str(send["endpoint"])
        if name not in endpoints:
            endpoints[name] = ET.SubElement(io, "endpoint", {"name": name})
        ET.SubElement(endpoints[name], "tag", {"key": str(send["tag"])}).text = str(send["tag"])
    return io


def bacnet_element(ctx: Context, hostname: str) -> ET.Element:
    """The ``bacnet`` element the BACnet module reads, from plantgen's
    ``bacnet/<host>.json`` object table. The address is the node's own, with
    its prefix, so the module also hears the subnet's directed broadcast."""
    doc: Any = ctx.sources.generated_json("bacnet", f"{hostname}.json")
    endpoint: Any = doc["endpoint"]
    address: str = ctx.ip(hostname, str(endpoint["interface"]))
    if address != endpoint["address"]:
        raise SourceError(
            f"{hostname}: the topology gives {endpoint['interface']} the address {address}, "
            f"plant.yaml gives {endpoint['address']}"
        )
    root: ET.Element = ET.Element("bacnet", {"name": "bacnet-server", "mode": "server"})
    ET.SubElement(root, "endpoint").text = f"{address}/{endpoint['prefix']}:{endpoint['port']}"
    ET.SubElement(
        root, "device", {"instance": str(doc["device"]["instance"]), "name": str(doc["device"]["name"])}
    )
    for obj in doc["objects"]:
        attrs: dict[str, str] = {
            "type": str(obj["object_type"]),
            "instance": str(obj["instance"]),
            "name": str(obj["name"]),
            "tag": str(obj["tag"]),
        }
        if obj.get("description"):
            attrs["description"] = str(obj["description"])
        if obj.get("units"):
            attrs["units"] = str(obj["units"])
        if obj.get("commandable"):
            attrs["commandable"] = "true"
        limits: Any = obj.get("range")
        # A multi-state object's range is its state numbers, not a limit.
        if limits and not obj.get("states"):
            attrs["min"], attrs["max"] = (f"{float(v):g}" for v in limits)
        element: ET.Element = ET.SubElement(root, "object", attrs)
        for state in obj.get("states") or []:
            ET.SubElement(element, "state").text = str(state)
    return root


def add_bacnet(config_xml: str, io: ET.Element, bacnet: ET.Element) -> str:
    """config.xml with the HELICS I/O module and the BACnet module: a CPU
    ``module`` entry and an element for each. Idempotent."""
    root: ET.Element = ET.fromstring(config_xml)
    cpu: ET.Element | None = root.find("cpu")
    if cpu is None:
        raise SourceError("the ot-sim config has no <cpu> element")
    modules: set[str | None] = {m.get("name") for m in cpu.findall("module")}
    if root.find("io") is None:
        root.append(io)
    if "i/o" not in modules:
        ET.SubElement(cpu, "module", {"name": "i/o"}).text = "ot-sim-io-module {{config_file}}"
    if root.find("bacnet") is None:
        root.append(bacnet)
    if "bacnet" not in modules:
        ET.SubElement(cpu, "module", {"name": "bacnet"}).text = BACNET_COMMAND
    ET.indent(root)
    return ET.tostring(root, encoding="unicode") + "\n"


def render_otsim(ctx: Context, hostname: str, md: RioHost, rendered: str | None) -> list[Injection]:
    """What this app adds to an ot-sim device that the ot-sim app cannot
    render: the telnet module, and for a BACnet controller the I/O and BACnet
    modules. ``rendered`` is the config.xml the ot-sim app injected."""
    bacnet: bool = bacnet_endpoint(ctx, hostname) is not None
    if md.telnet is None and not bacnet:
        return []
    if rendered is None:
        if bacnet:
            raise SourceError(
                "the ot-sim app injected no config.xml for this BACnet device; "
                "the scenario must list ot-sim before cherrymountain"
            )
        logger.warning(f"{hostname}: no ot-sim config.xml injection; the telnet module was not added")
        return []
    text: str = Path(rendered).read_text(encoding="utf-8")
    added: list[str] = []
    injects: list[Injection] = []
    if md.telnet is not None:
        endpoint: str = f"{ctx.ip(hostname, md.telnet.interface)}:{md.telnet.port}"
        text = add_telnet(text, endpoint, md.telnet.banner)
        added.append("telnet")
    if bacnet:
        text = add_bacnet(text, io_element(ctx, hostname), bacnet_element(ctx, hostname))
        added.append("I/O and BACnet")
        injects += _shared(
            ctx,
            hostname,
            "otsim",
            [("cm_otsim_bacnet.py", BACNET_MODULE, "0644", "ot-sim BACnet module")],
        )
    return [
        Injection(
            src=write(ctx.host_dir(hostname) / "ot-sim-config.xml", text),
            dst=OTSIM_CONFIG,
            description=f"ot-sim config with the {' and '.join(added)} modules",
        ),
        *injects,
    ]


# ---------------------------------------------------------------- vendor persona

#: Where the topology injects the vendor persona (dmz-jump, ews, vendor-ws).
VENDOR_ENV: Final[str] = "/etc/cherrymountain/ridgeline.env"
#: Every technician's Guacamole and EWS account is ``ridgeline-<name>``.
VENDOR_PREFIX: Final[str] = "ridgeline-"


def vendor_roster(ctx: Context) -> list[dict[str, str]]:
    """One named account per technician (DESIGN.md 13): the first uses the
    ``secrets.vendor*`` credentials, the others per-experiment ones kept in
    ``vendor.json`` (0600), so a restart renders the same values."""
    techs: list[str] = list(ctx.app.vendor_technicians)
    others: list[str] = techs[1:]
    store: Path = ctx.out_dir / "vendor.json"
    guac: dict[str, str] = host_keys(store, [f"{t}.guac" for t in others], lambda: secrets.token_urlsafe(24))
    ews: dict[str, str] = host_keys(store, [f"{t}.ews" for t in others], lambda: secrets.token_urlsafe(24))
    totp: dict[str, str] = host_keys(store, [f"{t}.totp" for t in others], totp_secret)
    roster: list[dict[str, str]] = [
        {
            "user": VENDOR_PREFIX + techs[0],
            "guac": ctx.creds.vendor_guac_password.get_secret_value(),
            "totp": ctx.creds.vendor_totp_secret.get_secret_value(),
            "ews": ctx.creds.vendor_ews_password.get_secret_value(),
        }
    ]
    for t in others:
        roster.append(
            {"user": VENDOR_PREFIX + t, "guac": guac[f"{t}.guac"], "totp": totp[f"{t}.totp"], "ews": ews[f"{t}.ews"]}
        )
    return roster


def render_vendor(ctx: Context) -> dict[str, list[Injection]]:
    """The vendor persona (DESIGN.md 13) with per-experiment credentials, for
    every node the topology gives ``ridgeline.env``: the rendered file replaces
    the committed one, so dmz-jump's Guacamole account, the EWS account, and the
    vendor's scripted session agree and change with every experiment."""
    hosts: list[str] = sorted(
        name
        for name, node in ctx.nodes.items()
        if any(i.get("dst") == VENDOR_ENV for i in node.get("injections") or [])
    )
    if not hosts:
        return {}
    roster: list[dict[str, str]] = vendor_roster(ctx)
    first: dict[str, str] = roster[0]
    values: dict[str, str | int] = {
        "VENDOR_GUAC_USER": first["user"],
        "VENDOR_GUAC_PASSWORD": first["guac"],
        "VENDOR_TOTP_SECRET": first["totp"],
        "VENDOR_EWS_USER": first["user"],
        "VENDOR_EWS_PASSWORD": first["ews"],
        "VENDOR_TECH_COUNT": len(roster),
    }
    for n, tech in enumerate(roster, 1):
        values[f"VENDOR_TECH{n}_USER"] = tech["user"]
        values[f"VENDOR_TECH{n}_GUAC_PASSWORD"] = tech["guac"]
        values[f"VENDOR_TECH{n}_TOTP_SECRET"] = tech["totp"]
        values[f"VENDOR_TECH{n}_EWS_PASSWORD"] = tech["ews"]
    text: str = (
        "# The vendor persona of this experiment, rendered by the cherrymountain app\n"
        "# (DESIGN.md 13): one named Guacamole and EWS account per technician, and the\n"
        "# first technician's again as VENDOR_* for vendor-ws's scripted session.\n"
        "# Range-only, generated per experiment.\n" + env_file(values)
    )
    out: dict[str, list[Injection]] = {}
    for host in hosts:
        src: Path = write(ctx.host_dir(host) / "ridgeline.env", text, private=True)
        out[host] = [
            Injection(
                src=src,
                dst=VENDOR_ENV,
                description="vendor persona (per-experiment credentials)",
                permissions="0600",
            )
        ]
    return out


# ---------------------------------------------------------------- WireGuard


def rekey_wireguard(ctx: Context, wireguard_hosts: list[Any]) -> int:
    """Replace the committed WireGuard keys of the ``wireguard`` app's hosts with
    per-experiment ones (stored in ``wireguard.json``, 0600). Each host's private
    key is replaced, and every peer entry that named the old public key gets the
    new one, so any tunnel layout stays consistent. Returns the hosts rekeyed."""
    with ctx.sources.plantgen_path():
        wgkeys: Any = importlib.import_module("plantgen.wgkeys")
    old_private: dict[str, str] = {}
    for host in wireguard_hosts:
        iface: Any = (host.get("metadata") or {}).get("interface") or {}
        if iface.get("private_key"):
            old_private[str(host["hostname"])] = str(iface["private_key"])
    if not old_private:
        return 0
    new_private: dict[str, str] = host_keys(
        ctx.out_dir / "wireguard.json",
        sorted(old_private),
        lambda: base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
    )
    pub_map: dict[str, str] = {
        wgkeys.public_key(old_private[h]): wgkeys.public_key(new_private[h])
        for h in old_private
    }
    for host in wireguard_hosts:
        name: str = str(host["hostname"])
        md: Any = host.get("metadata") or {}
        if name in new_private:
            md["interface"]["private_key"] = new_private[name]
        for peer in md.get("peers") or []:
            if peer.get("public_key") in pub_map:
                peer["public_key"] = pub_map[peer["public_key"]]
    return len(new_private)


# ---------------------------------------------------------------- Wazuh


def render_wazuh(ctx: Context) -> dict[str, list[Injection]]:
    """Wazuh manager settings for cm-wazuh and agent enrollment for the images
    that carry the agent. Agents enroll with a range password (authd
    use_password)."""
    cfg = ctx.app.wazuh
    if cfg is None:
        return {}
    manager_ip: str = ctx.ip(cfg.manager, cfg.interface)
    password: str = ctx.creds.wazuh_enrollment.get_secret_value()
    # An explicit allowlist (plant.yaml derives it from the conduits to the
    # manager): the enrollment password goes only to hosts that can enroll.
    agents: list[str] = list(cfg.agents)
    if not agents:
        logger.warning(
            "wazuh: no agents listed (metadata.wazuh.agents); only the manager is configured"
        )
    plan: dict[str, list[Injection]] = {}
    out: Path = ctx.host_dir(cfg.manager) / "wazuh"
    # The enrollment password is for 80-cm-wazuh-manager.sh; the admin password
    # is for cm-wazuh-provision, whose unit reads this file (EnvironmentFile=).
    env: Path = write(
        out / "cm-wazuh-manager.env",
        env_file(
            {
                "WAZUH_REGISTRATION_PASSWORD": password,
                "WAZUH_ADMIN_PASSWORD": ctx.creds.wazuh_password.get_secret_value(),
            }
        ),
        private=True,
    )
    plan[cfg.manager] = [
        Injection(
            src=env,
            dst="/etc/default/cm-wazuh-manager",
            description="Wazuh enrollment and dashboard admin passwords",
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


# ---------------------------------------------------------------- Malcolm

#: The script the topology injects on the Malcolm sensor (phenix-injects/malcolm)
#: and the file its unit reads for the credentials the app renders.
MALCOLM_PROVISION: Final[str] = "/usr/local/sbin/cm-malcolm-provision"
MALCOLM_CREDENTIALS: Final[str] = "/etc/default/cm-malcolm-credentials"


def render_malcolm(ctx: Context) -> dict[str, list[Injection]]:
    """The web password of Malcolm's user ``malcolm`` (DESIGN.md 14) for every
    node the topology gives ``cm-malcolm-provision``. The topology owns
    ``/etc/default/cm-malcolm`` (the sizing profile and the capture port, world
    readable); the password goes into a file of its own that only the
    provisioning unit reads, so it does not reach the sensor's other services."""
    hosts: list[str] = sorted(
        name
        for name, node in ctx.nodes.items()
        if any(i.get("dst") == MALCOLM_PROVISION for i in node.get("injections") or [])
    )
    out: dict[str, list[Injection]] = {}
    for host in hosts:
        src: Path = write(
            ctx.host_dir(host) / "malcolm" / "cm-malcolm-credentials.env",
            env_file({"MALCOLM_ADMIN_PASSWORD": ctx.creds.malcolm_password.get_secret_value()}),
            private=True,
        )
        out[host] = [
            Injection(
                src=src,
                dst=MALCOLM_CREDENTIALS,
                description="Malcolm web password (per-experiment)",
                permissions="0600",
            )
        ]
    return out


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
                address=members[name],
                hub=name == hub,
                hub_ip=members[hub],
                peers=peers,
                broker_ports=broker_ports,
                federate_ports=federate_ports,
                opensearch_port=policy.opensearch_port,
            ),
        )
        # phenix does not rename the guest's NICs, so the topology's interface
        # name is not the device name: the hook finds the device by this address.
        settings: Path = write(
            ctx.host_dir(name) / "mgmt" / "cm-mgmt.env",
            env_file({"CM_MGMT_ADDRESS": members[name]}),
        )
        plan[name] = [
            Injection(
                src=rules,
                dst=f"{CONF_DIR}/nftables-mgmt.nft",
                description="MGMT nftables policy",
            ),
            Injection(
                src=settings,
                dst="/etc/default/cm-mgmt",
                description="MGMT address, for the hook to find the device",
            ),
            *_packaged(
                ctx,
                name,
                [
                    (
                        # After phenix's 3_interfaces-start.sh, before the helics
                        # app's 5-wait-broker.sh.
                        ("scripts", "4-cm-mgmt-nftables.sh"),
                        f"{STARTUP}/4-cm-mgmt-nftables.sh",
                        "0755",
                        "load the MGMT nftables policy",
                    )
                ],
            ),
        ]
    return plan


# ---------------------------------------------------------------- all


#: Host type -> its renderer.
RENDERERS: Final[dict[type[HostBase], Any]] = {
    OpenPlcHost: render_openplc,
    FuxaHost: render_fuxa,
    SimCtlHost: render_sim,
    EwsHost: render_ews,
    SimFederateHost: render_sim,
    HistorianHost: render_historian,
    HmiHost: render_hmi,
    GuacamoleHost: render_guacamole,
}


def render_all(ctx: Context) -> dict[str, list[Injection]]:
    """Every injection, by host. Renders every host before failing, so one
    run reports every missing input at once."""
    plan: dict[str, list[Injection]] = {}
    failures: list[str] = []

    def add(hostname: str, injects: list[Injection]) -> None:
        plan.setdefault(hostname, []).extend(injects)

    for name, md in ctx.hosts.items():
        renderer: Any = RENDERERS.get(type(md))
        if renderer is None:
            continue
        try:
            add(name, renderer(ctx, name, md))
        except SourceError as ex:
            failures.append(f"{name}: {ex}")
    for step in (render_wazuh, render_malcolm, render_mgmt, render_vendor):
        try:
            for name, injects in step(ctx).items():
                add(name, injects)
        except SourceError as ex:
            failures.append(str(ex))
    if failures:
        raise SourceError(
            f"{len(failures)} host(s) could not be rendered:\n  "
            + "\n  ".join(failures)
        )
    return plan
