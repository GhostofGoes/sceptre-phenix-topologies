"""The full variant: plant.yaml against the 43-node topology, and the
renderers whose inputs exist for it, the five PLC program ZIPs included. (The
full FUXA projects are built here too, but a whole pre-start is not run.)"""

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
    own address, which the ``ss`` count sees as one more connected peer."""
    app = run_app("configure", exp=full)
    plant = app.sources.plant() if app.sources else None
    assert plant is not None
    hosts: set[str] = {spec.host for spec in plant.federates.values()}
    hosts |= {n for n, h in plant.hosts.items() if h.role == "rio" and h.federate}
    assert "sim-ctl" in hosts
    sim_ctl = next(h for h in app.app.hosts if h.hostname == "sim-ctl")
    script: str = sim_ctl.metadata.sohProfile.customTests[0].testScript
    assert f"want={len(hosts)}\n" in script


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
    assert kiosk["KIOSK_URL"] == "http://10.20.30.10:1881/home/Brewhouse"
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
    values = env(Path(marker.src))
    assert values["CM_TRUTH_INDEX_BASE"] == "cherrymountain-truth"
    assert values["GF_SECURITY_ADMIN_PASSWORD"]
    proc = ctx.hosts["sim-process"]
    assert isinstance(proc, SimFederateHost)
    dsts = {i.dst for i in render.render_sim(ctx, "sim-process", proc)}
    assert "/etc/cherrymountain/ground-truth.env" not in dsts


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
