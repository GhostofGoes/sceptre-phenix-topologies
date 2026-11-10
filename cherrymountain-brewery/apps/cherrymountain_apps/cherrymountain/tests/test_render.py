"""Renderers for hosts the ci variant does not have (OPC UA and S7 PLCs, the
historian, HMIs, Guacamole, Wazuh), on a context built from the ci
experiment plus extra topology nodes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import stat
import subprocess
import sys
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
    # FUXA connects anonymously: the session must get the role that may write
    # (the plugin's default for an anonymous session is the read-only viewer).
    (profile,) = config["server"]["security_profiles"]
    assert profile["auth_methods"] == ["Anonymous"]
    assert profile["anonymous_role"] == "operator"
    assert variables["CEL.FV101.TT01.SP"]["permissions"]["viewer"] == "r"
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
    assert sizes == {"QW": 104 * 2, "IW": 117 * 2, "MW": 8 * 2, "IX": 101, "QX": 101}
    numbers = {b["description"]: b["db_number"] for b in doc["data_blocks"]}
    # The numbering fuxa/build_project.py addresses its S7 tags with.
    spec = importlib.util.spec_from_file_location("cm_fuxa_builder", ROOT / "fuxa" / "build_project.py")
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = builder
    try:
        spec.loader.exec_module(builder)
    finally:
        sys.modules.pop(spec.name, None)
    assert numbers == {area: db for area, (db, _) in builder.S7_DATA_BLOCKS.items()}
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
    # Buckets expire after 30 days unless historian.retentionHours says otherwise.
    assert "INFLUX_RETENTION=720h\n" in Path(env.src).read_text()
    # The TTB records (27 CFR 25.300): a daily copy kept three years (DESIGN.md 12.4).
    assert "INFLUX_RECORDS_BUCKET=records\n" in Path(env.src).read_text()
    assert "INFLUX_RECORDS_RETENTION=26280h\n" in Path(env.src).read_text()
    task = Path(injects["/etc/cherrymountain/cm-records.flux"].src).read_text()
    assert 'option task = {name: "cm-ttb-records", every: 1d' in task
    assert 'from(bucket: "scada")' in task and 'to(bucket: "records"' in task
    assert r"PKG\.(LINE|FILLER|KEG)\.PACKML\.(PROCESSED|DEFECTIVE)" in task
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


def test_ci_has_no_bas_fuxa_project(ctx: Context) -> None:
    """The builder makes a bas-fuxa project for full and lite only."""
    host = md(FuxaHost, {"type": "fuxa", "role": "bas"})
    with pytest.raises(SourceError, match="only scada-fuxa"):
        render.render_fuxa(ctx, "bas-fuxa", host)


def test_fuxa_expected_device_must_be_in_the_project(ctx: Context) -> None:
    host = md(FuxaHost, {"type": "fuxa", "expectDevices": ["plc-brewhouse"]})
    with pytest.raises(SourceError, match="no device plc-brewhouse"):
        render.render_fuxa(ctx, "scada-fuxa", host)


# ---------------------------------------------------------------- HMI, Guacamole, Wazuh


def test_hmi_kiosk_url(ctx: Context) -> None:
    host = md(HmiHost, {"type": "hmi", "view": "Cellar"})
    injects = by_dst(render.render_hmi(ctx, "hmi-brewhouse", host))
    env = Path(injects["/etc/default/cm-kiosk"].src).read_text()
    assert "KIOSK_URL=http://10.20.10.10:1881/home?viewName=Cellar" in env
    assert "/etc/xdg/autostart/cm-kiosk.desktop" in injects


def test_hmi_kiosk_signs_in_with_the_operator_account_from_a_root_only_file(
    ctx: Context,
) -> None:
    host = md(HmiHost, {"type": "hmi", "view": "Cellar"})
    injects = by_dst(render.render_hmi(ctx, "hmi-brewhouse", host))
    password = ctx.creds.fuxa_operator_password.get_secret_value()
    sign_in = injects[render.KIOSK_CREDENTIALS]
    assert render.KIOSK_CREDENTIALS == "/etc/default/cm-kiosk-credentials"
    # Like the Malcolm credentials file: 0600 where it is written and in the guest,
    # where the desktop's only user is root.
    assert sign_in.permissions == "0600"
    assert stat.S_IMODE(Path(sign_in.src).stat().st_mode) == 0o600
    lines = Path(sign_in.src).read_text().splitlines()
    assert [x for x in lines if not x.startswith("#")] == [
        "KIOSK_USER=operator",
        f"KIOSK_PASSWORD={password}",
    ]
    # No other file of the kiosk (the URL file is world readable) holds it.
    for dst, inject in injects.items():
        if dst != render.KIOSK_CREDENTIALS:
            assert password not in Path(inject.src).read_text(), dst
    # The account is the one cm-fuxa-provision makes in FUXA's Operator group, and
    # the password is the one it sets (the same range value).
    script = (ROOT / "phenix-injects" / "fuxa" / "cm-fuxa-provision.sh").read_text()
    assert f"\nOPERATOR_USER={render.FUXA_OPERATOR_USER}\n" in script
    assert "\nOPERATOR_GROUPS=2\n" in script
    fuxa_injects = by_dst(
        render.render_fuxa(ctx, "scada-fuxa", md(FuxaHost, {"type": "fuxa"}))
    )
    fuxa_env = Path(fuxa_injects["/etc/default/cm-fuxa"].src).read_text()
    assert f"FUXA_OPERATOR_PASSWORD={password}" in fuxa_env


def test_guacamole_sql_has_the_connections_and_only_a_salted_hash(ctx: Context) -> None:
    host = md(GuacamoleHost, {"type": "guacamole"})
    injects = by_dst(render.render_guacamole(ctx, "dmz-jump", host))
    inject = injects["/opt/cherrymountain/guacamole/initdb/50-cherrymountain.sql"]
    # PostgreSQL's entrypoint runs /docker-entrypoint-initdb.d/* as the postgres
    # user (not root) and aborts on a file it cannot read, so the file must be
    # world-readable in the guest, as the stock schema is. It holds no password.
    assert inject.permissions == "0644"
    sql = Path(inject.src).read_text()
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


def test_wazuh_enrolls_only_the_listed_agents(ctx: Context) -> None:
    """The enrollment password goes only to the allowlist; an empty list
    configures the manager alone (no host gets the password by default)."""
    ctx.app = ctx.app.model_copy(
        update={"wazuh": Wazuh.model_validate({}, strict=True)}
    )
    assert set(render.render_wazuh(ctx)) == {"wazuh"}
    ctx.app = ctx.app.model_copy(
        update={"wazuh": Wazuh.model_validate({"agents": ["scada-fuxa"]}, strict=True)}
    )
    assert set(render.render_wazuh(ctx)) == {"wazuh", "scada-fuxa"}


# ---------------------------------------------------------------- MGMT nftables


@pytest.mark.skipif(shutil.which("nft") is None, reason="nft is not installed")
def test_mgmt_rulesets_parse(ctx: Context) -> None:
    for host, injects in render.render_mgmt(ctx).items():
        rules = by_dst(injects)["/etc/cherrymountain/nftables-mgmt.nft"].src
        proc = subprocess.run(
            ["nft", "-c", "-D", "mgmt_if=ens2", "-f", rules],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 and "Operation not permitted" in proc.stderr:
            pytest.skip("nft -c needs CAP_NET_ADMIN here")
        assert proc.returncode == 0, (host, proc.stderr)


def test_mgmt_federate_ports_cover_what_helics_assigns(ctx: Context) -> None:
    """The broker connects back to each federate core on a port it assigned:
    HELICS 3.6 counts them up per host from the default broker port plus 10
    (23414), and 23492 was also seen. Nothing pins them, so the allowed range
    must start below both. A range of 23500 and up stopped the federation
    (reproduced with two network namespaces and this ruleset)."""
    policy = ctx.app.mgmt
    assert max(policy.broker_ports) < policy.federate_port_min <= 23414
    assert policy.federate_port_max >= 23492 + 64
    for injects in render.render_mgmt(ctx).values():
        rules = Path(by_dst(injects)["/etc/cherrymountain/nftables-mgmt.nft"].src).read_text()
        assert f"tcp dport {policy.federate_port_min}-{policy.federate_port_max} accept" in rules


#: Stand-ins for ip and nft: ip prints $FAKE_IP_OUTPUT, nft records its arguments.
FAKE_IP: str = '#!/bin/sh\ncat "$FAKE_IP_OUTPUT"\n'
FAKE_NFT: str = '#!/bin/sh\necho "$@" >> "$FAKE_NFT_LOG"\n'
MGMT_HOOK: Path = render.template("scripts", "4-cm-mgmt-nftables.sh")
IP_ADDR: str = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
    "2: ens1    inet 10.20.12.10/24 brd 10.20.12.255 scope global ens1\\       valid_lft forever\n"
    "3: ens2    inet 172.16.20.12/16 brd 172.16.255.255 scope global ens2\\       valid_lft forever\n"
)


def _run_mgmt_hook(tmp_path: Path, address: str) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("ip", FAKE_IP), ("nft", FAKE_NFT)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    (tmp_path / "ip.txt").write_text(IP_ADDR)
    (tmp_path / "rules.nft").write_text("# rules\n")
    (tmp_path / "cm-mgmt").write_text(f"CM_MGMT_ADDRESS={address}\n")
    log = tmp_path / "nft.log"
    log.write_text("")
    proc = subprocess.run(
        ["bash", str(MGMT_HOOK)],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_IP_OUTPUT": str(tmp_path / "ip.txt"),
            "FAKE_NFT_LOG": str(log),
            "CM_MGMT_RULES": str(tmp_path / "rules.nft"),
            "CM_MGMT_SETTINGS": str(tmp_path / "cm-mgmt"),
            "CM_MGMT_WAIT": "0",
        },
    )
    return proc, log.read_text()


def test_mgmt_hook_loads_the_policy_on_the_device_with_the_mgmt_address(tmp_path: Path) -> None:
    """phenix does not rename guest NICs: the hook passes the device that holds
    the MGMT address (ens2 here), not the topology's interface name."""
    proc, log = _run_mgmt_hook(tmp_path, "172.16.20.12")
    assert proc.returncode == 0, proc.stderr
    rules = tmp_path / "rules.nft"
    assert log.splitlines() == [
        f"-c -D mgmt_if=ens2 -f {rules}",
        f"-D mgmt_if=ens2 -f {rules}",
    ]


def test_mgmt_hook_fails_without_loading_when_no_device_has_the_address(tmp_path: Path) -> None:
    proc, log = _run_mgmt_hook(tmp_path, "172.16.99.99")
    assert proc.returncode == 1
    assert "no interface has 172.16.99.99" in proc.stderr
    assert log == ""


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
