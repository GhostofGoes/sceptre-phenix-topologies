"""The full variant: plant.yaml against the 43-node topology, and the
renderers whose inputs exist for it, the five PLC program ZIPs included. (The
full FUXA projects are built here too; test_full_prestart.py renders every
host in one pass.)"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from cherrymountain_apps.cherrymountain import render
from cherrymountain_apps.cherrymountain.metadata import (
    BbmdHost,
    GuacamoleHost,
    HistorianHost,
    HmiHost,
    SimCtlHost,
    SimFederateHost,
)
from cherrymountain_apps.cherrymountain.render import Context
from cherrymountain_apps.cherrymountain.tests.conftest import (
    HERE,
    ROOT,
    RunApp,
    app_block,
)

FULL: dict[str, Any] = json.loads(
    (HERE / "fixtures" / "experiment-full.json").read_text()
)


@pytest.fixture
def full(tmp_path: Path) -> dict[str, Any]:
    exp: dict[str, Any] = copy.deepcopy(FULL)
    exp["spec"]["baseDir"] = str(tmp_path / "exp")
    app_block(exp)["metadata"]["sourceDir"] = str(ROOT)
    return exp


@pytest.fixture
def ctx(run_app: RunApp, full: dict[str, Any]) -> Context:
    app = run_app("configure", exp=full)
    assert app.config is not None
    assert app.sources is not None
    return app._context(app.config, app.sources)


def env(path: Path) -> dict[str, str]:
    pairs = (
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    return {k: v.strip('"') for k, v in pairs}


def test_full_plant_matches_the_topology(run_app: RunApp, full: dict[str, Any]) -> None:
    app = run_app("configure", exp=full)
    assert len(app.hosts_md) == len(app_block(full)["hosts"])
    annotations = {
        n.general.hostname: n.annotations["helics/federate"].to_list()
        for n in app.experiment.spec.topology.nodes
        if (n.get("annotations") or {}).get("helics/federate")
        and n.general.hostname.startswith("sim-")
    }
    plant = app.sources.plant() if app.sources else None
    assert plant is not None
    for host, value in annotations.items():
        count = sum(1 for spec in plant.federates.values() if spec.host == host)
        assert value == [{"broker": "sim-ctl|mgmt", "fed-count": count}], host
    assert "sim-ctl" in annotations


def test_full_federate_count_includes_the_weather_federate_on_sim_ctl(
    run_app: RunApp, full: dict[str, Any]
) -> None:
    """The weather federate runs on sim-ctl and reaches the broker on sim-ctl's
    own address, which the ``ss`` count sees as one more connected peer. Every
    device with an I/O module counts too: the remote I/O, the field devices,
    and the BAS controllers (the scenario's fd-server hosts)."""
    app = run_app("configure", exp=full)
    plant = app.sources.plant() if app.sources else None
    assert plant is not None
    hosts: set[str] = {spec.host for spec in plant.federates.values()}
    assert "sim-ctl" in hosts and len(hosts) == 4
    otsim: Any = next(a for a in full["spec"]["scenario"]["apps"] if a["name"] == "ot-sim")
    devices: set[str] = {h["hostname"] for h in otsim["hosts"] if h["metadata"]["type"] == "fd-server"}
    assert {"rio-cellar", "glycol-chiller", "relay-main", "bas-vent"} <= devices and len(devices) == 12
    sim_ctl = next(h for h in app.app.hosts if h.hostname == "sim-ctl")
    script: str = sim_ctl.metadata.sohProfile.customTests[0].testScript
    assert f"want={len(hosts | devices)}\n" in script


def test_full_bbmds_are_checked_not_rendered(
    run_app: RunApp, full: dict[str, Any]
) -> None:
    app = run_app("configure", exp=full)
    assert BbmdHost not in render.RENDERERS
    for host in ("ot-svc", "bas-bbmd"):
        profile = next(
            h for h in app.app.hosts if h.hostname == host
        ).metadata.sohProfile
        assert profile.processes == ["cm_bbmd.py"]


def test_full_renderers_with_existing_inputs(ctx: Context) -> None:
    kinds = (
        SimCtlHost,
        SimFederateHost,
        HistorianHost,
        HmiHost,
        GuacamoleHost,
    )
    for name, md in ctx.hosts.items():
        if isinstance(md, kinds):
            assert render.RENDERERS[type(md)](ctx, name, md) is not None
    hmi = ctx.hosts["hmi-brewhouse"]
    assert isinstance(hmi, HmiHost)
    kiosk_env = next(
        i.src
        for i in render.render_hmi(ctx, "hmi-brewhouse", hmi)
        if i.dst == "/etc/default/cm-kiosk"
    )
    kiosk = env(Path(kiosk_env))
    # FUXA reads the view from the viewName query parameter, not from the path.
    assert kiosk["KIOSK_URL"] == "http://10.20.30.10:1881/home?viewName=Brewhouse"
    assert set(render.render_wazuh(ctx)) >= {
        "wazuh",
        "historian",
        "scada-fuxa",
        "dmz-jump",
    }


def test_full_sim_ctl_gets_the_ground_truth_marker(ctx: Context) -> None:
    """cm-sim starts OpenSearch and Grafana only where ground-truth.env exists:
    sim-ctl in full, never a federate node."""
    sim_ctl = ctx.hosts["sim-ctl"]
    assert isinstance(sim_ctl, SimCtlHost)
    injects = {i.dst: i for i in render.render_sim(ctx, "sim-ctl", sim_ctl)}
    marker = injects["/etc/cherrymountain/ground-truth.env"]
    assert Path(marker.src).stat().st_mode & 0o077 == 0
    # It holds Grafana's admin password, so it is root-only in the guest too.
    assert marker.permissions == "0600"
    values = env(Path(marker.src))
    assert values["CM_TRUTH_INDEX_BASE"] == "cherrymountain-truth"
    assert values["GF_SECURITY_ADMIN_PASSWORD"]
    proc = ctx.hosts["sim-process"]
    assert isinstance(proc, SimFederateHost)
    dsts = {i.dst for i in render.render_sim(ctx, "sim-process", proc)}
    assert "/etc/cherrymountain/ground-truth.env" not in dsts


def test_full_sim_ctl_runs_cm_viz_on_loopback(ctx: Context) -> None:
    """cm-viz (DESIGN.md 15.10) goes where the event API runs: sim-ctl, which
    hosts the weather federate. It reads the snapshot on 127.0.0.1 and its unit
    allows loopback traffic only; no federate node gets it."""
    sim_ctl = ctx.hosts["sim-ctl"]
    assert isinstance(sim_ctl, SimCtlHost)
    injects = {i.dst: i for i in render.render_sim(ctx, "sim-ctl", sim_ctl)}
    values = env(Path(injects["/etc/default/cm-viz"].src))
    assert values["CM_VIZ_SIMCTL_URL"] == "http://127.0.0.1:8080"
    assert values["CM_VIZ_PORT"] == "8090"
    unit = Path(injects["/etc/systemd/system/cm-viz.service"].src).read_text()
    assert "--host 127.0.0.1" in unit
    assert "IPAddressDeny=any" in unit and "IPAddressAllow=localhost" in unit
    assert injects["/etc/phenix/startup/61-cm-viz.sh"].permissions == "0755"
    proc = ctx.hosts["sim-process"]
    assert isinstance(proc, SimFederateHost)
    dsts = {i.dst for i in render.render_sim(ctx, "sim-process", proc)}
    assert "/etc/default/cm-viz" not in dsts


def test_full_mgmt_policy_covers_every_mgmt_member(ctx: Context) -> None:
    members = render.mgmt_members(ctx)
    plan = render.render_mgmt(ctx)
    assert set(plan) == set(members)
    assert members["sim-ctl"] == "172.16.10.1"
    assert len(members) >= 16


def test_a_zip_built_for_another_variant_warns(ctx: Context) -> None:
    from phenix_apps.common.logger import logger

    from cherrymountain_apps.cherrymountain.metadata import OpenPlcHost

    lines: list[str] = []
    sink = logger.add(
        lambda m: lines.append(str(m)), level="WARNING", format="{message}"
    )
    try:
        md = ctx.hosts["plc-cellar"]
        assert isinstance(md, OpenPlcHost)
        render.render_openplc(ctx, "plc-cellar", md)
    finally:
        logger.remove(sink)
    drift = ctx.sources.zip_conf_drift(
        "plc-cellar", ctx.sources.plc_conf_dir("plc-cellar")
    )
    if drift:
        assert any("rebuild it for this variant" in line for line in lines)
    else:
        assert lines == []


def test_full_vendor_persona_is_generated_per_experiment(ctx: Context) -> None:
    """ridgeline.env is rendered for every node the topology gives it, with the
    experiment's credentials (DESIGN.md 13), not the committed values."""
    import base64

    # The recorded fixture has no topology injections; give the three nodes the
    # committed one, as topology-core and topology-remote-access do.
    for host in ("dmz-jump", "ews", "vendor-ws"):
        ctx.nodes[host]["injections"] = [
            {"src": "guacamole/ridgeline.env", "dst": render.VENDOR_ENV}
        ]
    plan = render.render_vendor(ctx)
    assert set(plan) == {"dmz-jump", "ews", "vendor-ws"}
    texts = {Path(i.src).read_text() for injects in plan.values() for i in injects}
    assert len(texts) == 1
    values = env(Path(plan["ews"][0].src))
    committed = env(ROOT / "phenix-injects" / "guacamole" / "ridgeline.env")
    assert values["VENDOR_GUAC_USER"] == committed["VENDOR_GUAC_USER"]
    for key in ("VENDOR_GUAC_PASSWORD", "VENDOR_TOTP_SECRET", "VENDOR_EWS_PASSWORD"):
        assert values[key] and values[key] != committed[key], key
    assert len(base64.b32decode(values["VENDOR_TOTP_SECRET"])) == 20
    # One named account per technician (DESIGN.md 13), each with its own TOTP key;
    # the first is the scripted session's VENDOR_* account.
    assert values["VENDOR_TECH_COUNT"] == "2"
    assert values["VENDOR_TECH1_USER"] == values["VENDOR_GUAC_USER"] == values["VENDOR_EWS_USER"] == "ridgeline-akim"
    assert values["VENDOR_TECH1_TOTP_SECRET"] == values["VENDOR_TOTP_SECRET"]
    assert values["VENDOR_TECH2_USER"] == "ridgeline-jdiaz"
    assert values["VENDOR_TECH2_TOTP_SECRET"] != values["VENDOR_TECH1_TOTP_SECRET"]
    assert len(base64.b32decode(values["VENDOR_TECH2_TOTP_SECRET"])) == 20
    assert render.render_vendor(ctx)["ews"][0].src and env(Path(plan["ews"][0].src))["VENDOR_TECH2_GUAC_PASSWORD"] == values["VENDOR_TECH2_GUAC_PASSWORD"]
    assert Path(plan["ews"][0].src).stat().st_mode & 0o077 == 0


def test_full_wireguard_keys_are_replaced_consistently(
    run_app: RunApp, full: dict[str, Any]
) -> None:
    """configure swaps the fixed-seed keys for per-experiment ones and keeps
    each peer's public key matching the other side's new private key, and a
    second configure keeps the same keys."""
    import sys

    sys.path.insert(0, str(ROOT / "tools" / "plantgen"))
    try:
        from plantgen.wgkeys import public_key
    finally:
        sys.path.remove(str(ROOT / "tools" / "plantgen"))

    def wg_hosts(exp: Any) -> dict[str, Any]:
        apps = exp["spec"]["scenario"]["apps"]
        wg = next(a for a in apps if a["name"] == "wireguard")
        return {h["hostname"]: h["metadata"] for h in wg["hosts"]}

    before = copy.deepcopy(wg_hosts(full))
    app = run_app("configure", exp=full)
    after = wg_hosts(app.experiment)
    for host in ("dmz-vpn", "vendor-ws"):
        assert after[host]["interface"]["private_key"] != before[host]["interface"]["private_key"]
    assert after["dmz-vpn"]["peers"][0]["public_key"] == public_key(after["vendor-ws"]["interface"]["private_key"])
    assert after["vendor-ws"]["peers"][0]["public_key"] == public_key(after["dmz-vpn"]["interface"]["private_key"])
    again = run_app("configure", exp=full)
    assert wg_hosts(again.experiment) == after


def test_full_renders_every_plc_from_its_variant_zip(ctx: Context) -> None:
    """Every PLC of the full variant has a program ZIP (plc/common/build_zip.sh);
    the app renders all five, plc-brewhouse's OPC UA config from the ZIP's
    debug map and plc-packaging's S7 config from the slave layout."""
    from cherrymountain_apps.cherrymountain.metadata import OpenPlcHost

    plcs = {n: md for n, md in ctx.hosts.items() if isinstance(md, OpenPlcHost)}
    assert set(plcs) == {"plc-brewhouse", "plc-cellar", "plc-packaging", "plc-utilities", "plc-power"}
    for name, md in plcs.items():
        dsts = {i.dst for i in render.render_openplc(ctx, name, md)}
        assert f"/opt/cherrymountain/openplc/{md.program or name}.zip" in dsts, name
    opcua = next(
        i.src
        for i in render.render_openplc(ctx, "plc-brewhouse", plcs["plc-brewhouse"])
        if i.dst.endswith("opcua.json")
    )
    config = json.loads(Path(opcua).read_text())
    assert config  # built from the ZIP's debug_map.json


def test_full_fuxa_projects_are_built_for_both_hosts(ctx: Context) -> None:
    """scada-fuxa and bas-fuxa get the project fuxa/build_project.py builds for
    them, equal to the committed fuxa/full/<host>-project.json, and the
    provisioning settings expect every device of it."""
    from cherrymountain_apps.cherrymountain.metadata import FuxaHost

    for host in ("scada-fuxa", "bas-fuxa"):
        md = ctx.hosts[host]
        assert isinstance(md, FuxaHost)
        injects = {i.dst: i for i in render.render_fuxa(ctx, host, md)}
        project = json.loads(Path(injects["/etc/cherrymountain/fuxa/project.json"].src).read_text())
        committed = json.loads((ROOT / "fuxa" / "full" / f"{host}-project.json").read_text())
        assert project == committed, host
        expect = env(Path(injects["/etc/default/cm-fuxa"].src))["FUXA_EXPECT_DEVICES"].split()
        assert set(expect) == {d["name"] for d in project["devices"].values()}


def test_full_fuxa_views_have_unique_names_and_the_kiosks_views_exist(ctx: Context) -> None:
    """FUXA opens a view by its name (/home?viewName=<name>, the kiosk URL), so no two
    views of a project may share one: plc-cellar's generated view is "Cellar
    points" beside the hand-laid "Cellar". Every kiosk's view must exist on
    the FUXA host it opens."""
    from cherrymountain_apps.cherrymountain.metadata import FuxaHost

    views: dict[str, list[str]] = {}
    for host, md in ctx.hosts.items():
        if isinstance(md, FuxaHost):
            project = ctx.sources.fuxa_project(md.project, host)
            views[host] = [v["name"] for v in project["hmi"]["views"]]
            assert len(set(views[host])) == len(views[host]), (host, views[host])
    assert {"Cellar", "Cellar points"} <= set(views["scada-fuxa"])
    kiosks = {name: md for name, md in ctx.hosts.items() if isinstance(md, HmiHost)}
    assert kiosks
    for name, md in kiosks.items():
        assert md.view in views[md.fuxa], (name, md.view)


def test_full_ews_gets_the_openplc_credentials(ctx: Context) -> None:
    """cm_plc_tool.py on the EWS reads /etc/default/cm-ews: the same OpenPLC
    account the app creates on every PLC, root-only."""
    from cherrymountain_apps.cherrymountain.metadata import EwsHost

    md = ctx.hosts["ews"]
    assert isinstance(md, EwsHost)
    (inject,) = render.render_ews(ctx, "ews", md)
    assert inject.dst == "/etc/default/cm-ews" and inject.permissions == "0600"
    values = env(Path(inject.src))
    assert values == {
        "OPENPLC_USER": render.OPENPLC_USER,
        "OPENPLC_PASSWORD": ctx.creds.openplc_password.get_secret_value(),
    }


def test_full_relay_main_gets_the_telnet_module(ctx: Context, tmp_path: Path) -> None:
    """The cherrymountain app adds the ot-sim telnet module to relay-main's
    config.xml (DESIGN.md 10.4): a CPU module entry and a telnet element on
    relay-main's FIELD-PWR address, port 23; running it twice changes nothing."""
    import xml.etree.ElementTree as ET

    from cherrymountain_apps.cherrymountain.metadata import RioHost

    md = ctx.hosts["relay-main"]
    assert isinstance(md, RioHost) and md.telnet is not None
    rendered = tmp_path / "relay-main.xml"
    rendered.write_text(
        "<ot-sim><cpu><module name=\"backplane\">ot-sim-message-bus {{config_file}}</module></cpu>"
        "<modbus name=\"modbus\" mode=\"server\"><endpoint>10.20.16.20:502</endpoint></modbus></ot-sim>"
    )
    (inject,) = render.render_telnet(ctx, "relay-main", md, str(rendered))
    assert inject.dst == "/etc/ot-sim/config.xml"
    text = Path(inject.src).read_text()
    assert text.startswith("<ot-sim>")
    root = ET.fromstring(text)
    assert [m.get("name") for m in root.find("cpu")] == ["backplane", "telnet"]  # type: ignore[union-attr]
    assert root.findtext("telnet/endpoint") == "10.20.16.20:23"
    assert render.add_telnet(text, "10.20.16.20:23", "default").count("<telnet") == 1
    assert render.render_telnet(ctx, "relay-main", md, None) == []


def test_full_restart_reads_the_config_the_ot_sim_app_just_rendered(
    run_app: RunApp, full: dict[str, Any]
) -> None:
    """phenix keeps a start's injections in the stored experiment. On the next
    start the ot-sim app appends its fresh config.xml after this app's own one
    from the last start: the telnet module goes onto the fresh one, and one
    injection to config.xml is left."""
    import xml.etree.ElementTree as ET

    from cherrymountain_apps.cherrymountain.tests.conftest import node

    def config(modules: str) -> str:
        return f"<ot-sim><cpu>{modules}</cpu></ot-sim>"

    base = Path(full["spec"]["baseDir"])
    stale = base / "cherrymountain" / "relay-main" / "ot-sim-config.xml"
    fresh = base / "ot-sim" / "relay-main.xml"
    for path, modules in (
        (stale, '<module name="backplane">a</module><module name="last-start">b</module>'),
        (fresh, '<module name="backplane">a</module><module name="modbus">c</module>'),
    ):
        path.parent.mkdir(parents=True)
        path.write_text(config(modules))
    relay = node(full, "relay-main")
    relay["injections"] = [
        *(relay.get("injections") or []),
        {"src": str(stale), "dst": "/etc/ot-sim/config.xml"},
        {"src": str(fresh), "dst": "/etc/ot-sim/config.xml"},
    ]
    app = run_app("pre-start", exp=full)
    injects = [
        i
        for i in app.require_node("relay-main").injections
        if i["dst"] == "/etc/ot-sim/config.xml"
    ]
    assert [i["src"] for i in injects] == [str(stale)]
    root = ET.fromstring(stale.read_text())
    assert [m.get("name") for m in root.find("cpu")] == ["backplane", "modbus", "telnet"]  # type: ignore[union-attr]
