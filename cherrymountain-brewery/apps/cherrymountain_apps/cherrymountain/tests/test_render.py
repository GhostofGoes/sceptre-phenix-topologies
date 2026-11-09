"""Renderers for hosts the ci variant does not have (OPC UA and S7 PLCs, the
historian, HMIs, Guacamole, Wazuh), on a context built from the ci
experiment plus extra topology nodes."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from box import Box

from cherrymountain_apps.cherrymountain import render
from cherrymountain_apps.cherrymountain.metadata import (
    FuxaHost,
    GuacamoleHost,
    HistorianHost,
    HmiHost,
    OpenPlcHost,
    Wazuh,
)
from cherrymountain_apps.cherrymountain.render import Context
from cherrymountain_apps.cherrymountain.sources import DebugMap, SourceError
from cherrymountain_apps.cherrymountain.tests.conftest import HERE, ROOT, RunApp

DEBUG_MAP: str = (
    (HERE / "fixtures" / "debug_map-plc-cellar.json").relative_to(ROOT).as_posix()
)


def _node(
    hostname: str, iface: str, address: str, mask: int = 24, zone: str = "ops"
) -> Box:
    return Box(
        {
            "general": {"hostname": hostname},
            "labels": {"zone": zone},
            "hardware": {"os_type": "linux"},
            "network": {
                "interfaces": [{"name": iface, "address": address, "mask": mask}]
            },
        }
    )


@pytest.fixture
def ctx(run_app: RunApp) -> Context:
    app = run_app("configure")
    assert app.config is not None
    assert app.sources is not None
    context: Context = app._context(app.config, app.sources)
    for n in (
        _node("historian", "IF0", "10.20.30.11"),
        _node("hmi-brewhouse", "IF0", "10.20.20.10", zone="hmi"),
        _node("ews", "IF0", "10.20.30.12"),
        _node("dmz-jump", "IF0", "10.10.20.11", zone="dmz"),
        _node("wazuh", "IF0", "10.40.10.11", zone="soc"),
    ):
        context.nodes[n.general.hostname] = n
    return context


def md(model: type[Any], data: dict[str, Any]) -> Any:
    return model.model_validate(data, strict=True)


def by_dst(injects: list[render.Injection]) -> dict[str, render.Injection]:
    return {i.dst: i for i in injects}


# ---------------------------------------------------------------- OpenPLC


def test_opcua_config_from_the_debug_map(ctx: Context) -> None:
    host = md(
        OpenPlcHost,
        {"type": "openplc", "northbound": "opcua", "opcua": {"debugMap": DEBUG_MAP}},
    )
    injects = by_dst(render.render_openplc(ctx, "plc-cellar", host))
    doc = json.loads(
        Path(injects["/opt/cherrymountain/openplc/conf/opcua.json"].src).read_text()
    )
    config = doc[0]["config"]
    assert config["format_version"] == 2
    assert (
        config["server"]["endpoint_url"] == "opc.tcp://10.20.10.12:4840/openplc/opcua"
    )
    variables = {v["node_id"]: v for v in config["address_space"]["variables"]}
    assert len(variables) == 61
    leaves = DebugMap.parse(json.loads((ROOT / DEBUG_MAP).read_text())).by_variable()
    pv = variables["CEL.FV101.TT01.PV"]
    assert (pv["arr"], pv["elem"], pv["size"]) == (
        leaves["CEL_FV101_TT01_PV"].array_idx,
        leaves["CEL_FV101_TT01_PV"].elem_idx,
        2,
    )
    assert pv["permissions"]["operator"] == "r"
    assert variables["CEL.FV101.TT01.SP"]["permissions"]["operator"] == "rw"
    assert variables["CEL.FV101.TT01.ALM_HIGH"]["datatype"] == "BOOL"
    # The plantgen Modbus configs still go in; the slave plugin is optional then.
    assert "/opt/cherrymountain/openplc/conf/modbus_master.json" in injects


def test_opcua_debug_map_from_another_build_fails(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ctx.sources.debug_map
    monkeypatch.setattr(
        ctx.sources,
        "debug_map",
        lambda program, explicit=None: real(program, explicit).model_copy(
            update={"md5": "0" * 32}
        ),
    )
    host = md(
        OpenPlcHost,
        {"type": "openplc", "northbound": "opcua", "opcua": {"debugMap": DEBUG_MAP}},
    )
    with pytest.raises(SourceError, match="PROGRAM_MD5 differs"):
        render.render_openplc(ctx, "plc-cellar", host)


def test_opcua_needs_every_tag_in_the_debug_map(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ctx.sources.debug_map

    def trimmed(program: str, explicit: str | None = None) -> DebugMap:
        dmap = real(program, explicit)
        return dmap.model_copy(
            update={"leaves": [x for x in dmap.leaves if "FV104" not in x.path]}
        )

    monkeypatch.setattr(ctx.sources, "debug_map", trimmed)
    host = md(
        OpenPlcHost,
        {"type": "openplc", "northbound": "opcua", "opcua": {"debugMap": DEBUG_MAP}},
    )
    with pytest.raises(SourceError, match="not in the compiled program's debug map"):
        render.render_openplc(ctx, "plc-cellar", host)


def test_opcua_without_a_debug_map_says_where_it_looked(ctx: Context) -> None:
    host = md(OpenPlcHost, {"type": "openplc", "northbound": "opcua"})
    with pytest.raises(SourceError, match=r"plc/plc-cellar/debug_map\.json is missing"):
        render.render_openplc(ctx, "plc-cellar", host)


def test_s7comm_config_sizes_follow_the_slave_layout(ctx: Context) -> None:
    host = md(OpenPlcHost, {"type": "openplc", "northbound": "s7comm"})
    injects = by_dst(render.render_openplc(ctx, "plc-cellar", host))
    doc = json.loads(
        Path(injects["/opt/cherrymountain/openplc/conf/s7comm.json"].src).read_text()
    )
    sizes = {b["description"]: b["size_bytes"] for b in doc["data_blocks"]}
    assert sizes == {"QW": 104 * 2, "IW": 117 * 2}
    assert doc["server"]["port"] == 102


# ---------------------------------------------------------------- historian and FUXA DAQ


def test_historian_files(ctx: Context) -> None:
    hist: HistorianHost = md(HistorianHost, {"type": "historian"})
    ctx.hosts["historian"] = hist
    injects = by_dst(render.render_historian(ctx, "historian", hist))
    token = ctx.creds.influx_token.get_secret_value()
    telegraf = Path(injects["/etc/telegraf/telegraf.conf"].src).read_text()
    assert 'token = "${INFLUX_TOKEN}"' in telegraf
    assert token not in telegraf
    assert 'bucket = "oee"' in telegraf
    mosquitto = Path(
        injects["/etc/mosquitto/conf.d/cherrymountain.conf"].src
    ).read_text()
    assert "listener 1883 10.20.30.11" in mosquitto
    env = injects["/etc/default/cm-historian"]
    assert env.permissions == "0600"
    assert 'INFLUX_BUCKETS="scada bas oee"' in Path(env.src).read_text()
    sources = yaml.safe_load(
        Path(
            injects["/etc/grafana/provisioning/datasources/cherrymountain.yaml"].src
        ).read_text()
    )
    assert [d["jsonData"]["defaultBucket"] for d in sources["datasources"]] == [
        "scada",
        "bas",
        "oee",
    ]
    assert all(token not in json.dumps(d) for d in sources["datasources"])
    assert "/etc/systemd/system/telegraf.service.d/cherrymountain.conf" in injects
    assert "/usr/local/bin/cm-export-historian" in injects


def test_fuxa_daq_goes_to_the_historian_when_there_is_one(ctx: Context) -> None:
    ctx.hosts["historian"] = md(HistorianHost, {"type": "historian"})
    host = ctx.hosts["scada-fuxa"]
    assert isinstance(host, FuxaHost)
    settings = render.fuxa_settings(ctx, "scada-fuxa", host)
    assert settings["daqstore"]["type"] == "influxDB"
    assert settings["daqstore"]["url"] == "http://10.20.30.11:8086"
    assert settings["daqstore"]["bucket"] == "scada"


def test_bas_fuxa_needs_a_project(ctx: Context) -> None:
    host = md(FuxaHost, {"type": "fuxa", "role": "bas"})
    with pytest.raises(SourceError, match=r"needs metadata\.project"):
        render.render_fuxa(ctx, "scada-fuxa", host)


def test_fuxa_expected_device_must_be_in_the_project(ctx: Context) -> None:
    host = md(FuxaHost, {"type": "fuxa", "expectDevices": ["plc-brewhouse"]})
    with pytest.raises(SourceError, match="no device plc-brewhouse"):
        render.render_fuxa(ctx, "scada-fuxa", host)


# ---------------------------------------------------------------- HMI, Guacamole, Wazuh


def test_hmi_kiosk_url(ctx: Context) -> None:
    host = md(HmiHost, {"type": "hmi", "view": "Cellar"})
    injects = by_dst(render.render_hmi(ctx, "hmi-brewhouse", host))
    env = Path(injects["/etc/default/cm-kiosk"].src).read_text()
    assert "KIOSK_URL=http://10.20.10.10:1881/home/Cellar" in env
    assert "/etc/xdg/autostart/cm-kiosk.desktop" in injects


def test_guacamole_sql_has_the_connections_and_only_a_salted_hash(ctx: Context) -> None:
    host = md(GuacamoleHost, {"type": "guacamole"})
    injects = by_dst(render.render_guacamole(ctx, "dmz-jump", host))
    sql = Path(
        injects["/opt/cherrymountain/guacamole/initdb/50-cherrymountain.sql"].src
    ).read_text()
    password = ctx.creds.guacamole_password.get_secret_value()
    assert password not in sql
    salt = bytes.fromhex((ctx.out_dir / "dmz-jump" / "guacamole" / "salt").read_text())
    assert hashlib.sha256((password + salt.hex().upper()).encode()).hexdigest() in sql
    assert "'ews-rdp'" in sql
    assert "'10.20.30.12'" in sql
    assert "'3389'" in sql
    assert "'22'" in sql


def test_wazuh_agents_and_manager(ctx: Context) -> None:
    ctx.app = ctx.app.model_copy(
        update={
            "wazuh": Wazuh.model_validate({"agents": ["ews", "dmz-jump"]}, strict=True)
        }
    )
    plan = render.render_wazuh(ctx)
    assert set(plan) == {"wazuh", "ews", "dmz-jump"}
    env = Path(by_dst(plan["dmz-jump"])["/etc/default/cm-wazuh-agent"].src).read_text()
    assert "WAZUH_MANAGER=10.40.10.11" in env
    assert "WAZUH_AGENT_GROUP=dmz" in env
    assert by_dst(plan["wazuh"])["/etc/default/cm-wazuh-manager"].permissions == "0600"


def test_wazuh_default_agents_skip_plcs(ctx: Context) -> None:
    ctx.app = ctx.app.model_copy(
        update={"wazuh": Wazuh.model_validate({}, strict=True)}
    )
    plan = render.render_wazuh(ctx)
    assert "plc-cellar" not in plan
    assert "scada-fuxa" in plan


# ---------------------------------------------------------------- MGMT nftables


@pytest.mark.skipif(shutil.which("nft") is None, reason="nft is not installed")
def test_mgmt_rulesets_parse(ctx: Context) -> None:
    for host, injects in render.render_mgmt(ctx).items():
        rules = by_dst(injects)["/etc/cherrymountain/nftables-mgmt.nft"].src
        proc = subprocess.run(
            ["nft", "-c", "-f", rules], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 and "Operation not permitted" in proc.stderr:
            pytest.skip("nft -c needs CAP_NET_ADMIN here")
        assert proc.returncode == 0, (host, proc.stderr)


def test_env_file_refuses_unsafe_values() -> None:
    with pytest.raises(ValueError, match="FOO"):
        render.env_file({"FOO": 'a"b'})
    assert render.env_file({"A": "x y"}).splitlines()[1] == 'A="x y"'


def test_secrets_refuse_shell_metacharacters() -> None:
    from pydantic import ValidationError

    from cherrymountain_apps.cherrymountain.metadata import Secrets

    assert Secrets.model_validate({"openplcPassword": "Ok.pass-1_~"}).openplc_password is not None
    for bad in ("a;b", "a&b", "a|b", "a b", "a*b", "a<b"):
        with pytest.raises(ValidationError):
            Secrets.model_validate({"openplcPassword": bad})
