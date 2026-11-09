"""The full variant: plant.yaml against the 43-node topology, and the
renderers whose inputs exist for it. (The full PLC program ZIPs and the FUXA
projects do not exist yet, so a whole pre-start is not run here.)"""

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
