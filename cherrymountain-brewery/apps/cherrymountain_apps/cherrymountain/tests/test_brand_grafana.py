"""The brand on Grafana (the historian, and sim-ctl where ground truth runs): the images and the
series colors of the dashboards, as the app renders them."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from box import Box

from cherrymountain_apps.cherrymountain import brand, render
from cherrymountain_apps.cherrymountain.metadata import (
    HistorianHost,
    SimCtlHost,
)
from cherrymountain_apps.cherrymountain.render import Context
from cherrymountain_apps.cherrymountain.sources import Sources
from cherrymountain_apps.cherrymountain.tests.conftest import ROOT, RunApp

BRAND: Path = ROOT / "brand"
INJECTS_BRAND: Path = ROOT / "phenix-injects" / "brand"
TOKENS: dict[str, Any] = json.loads((BRAND / "tokens.json").read_text())
SERIES: list[str] = [TOKENS["colors"][n] for n in TOKENS["roles"]["chart_series"]]


def md(model: type[Any], data: dict[str, Any]) -> Any:
    return model.model_validate(data, strict=True)


def by_dst(injects: list[render.Injection]) -> dict[str, render.Injection]:
    return {i.dst: i for i in injects}


@pytest.fixture
def ctx(run_app: RunApp) -> Context:
    app = run_app("configure")
    assert app.config is not None
    assert app.sources is not None
    context: Context = app._context(app.config, app.sources)
    context.nodes["historian"] = Box(
        {
            "general": {"hostname": "historian"},
            "hardware": {"os_type": "linux"},
            "network": {
                "interfaces": [{"name": "IF0", "address": "10.20.30.11", "mask": 24}]
            },
        }
    )
    return context


def test_cm_grafana_brand_uses_the_files_the_app_injects() -> None:
    """The script's table of Grafana image -> brand file, and the app's list of files to
    inject, name the same brand files."""
    script = (ROOT / "phenix-injects/grafana/cm-grafana-brand").read_text()
    wanted = set(re.findall(r"\]=([\w.-]+)\n", script))
    assert wanted == {f.name for f in brand.GRAFANA_FILES}
    assert all(
        f.dst == f"/opt/cherrymountain/brand/{f.name}" for f in brand.GRAFANA_FILES
    )
    assert 'BRAND="${CM_BRAND_DIR:-/opt/cherrymountain/brand}"' in script
    for f in brand.GRAFANA_FILES:
        assert (INJECTS_BRAND / f.name).is_file(), f.name


def test_the_historian_gets_grafanas_images(ctx: Context) -> None:
    hist = md(HistorianHost, {"type": "historian"})
    ctx.hosts["historian"] = hist
    injects = by_dst(render.render_historian(ctx, "historian", hist))
    for f in brand.GRAFANA_FILES:
        assert (
            Path(injects[f.dst].src).read_bytes()
            == (INJECTS_BRAND / f.name).read_bytes()
        )
    assert injects["/usr/local/bin/cm-grafana-brand"].permissions == "0755"
    assert injects[f"{render.STARTUP}/67-grafana-brand.sh"].permissions == "0755"


def lines(panel: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Override regex -> {color, dashed} of a panel."""
    out: dict[str, dict[str, Any]] = {}
    for o in panel["fieldConfig"]["overrides"]:
        assert o["matcher"]["id"] == "byRegexp"
        props = {p["id"]: p["value"] for p in o["properties"]}
        assert props["color"]["mode"] == "fixed"
        out[o["matcher"]["options"]] = {
            "color": props["color"]["fixedColor"],
            "dashed": props.get("custom.lineStyle")
            == {"fill": "dash", "dash": [10, 10]},
        }
    return out


def test_the_plant_dashboard_draws_each_fermenter_in_a_series_color(
    ctx: Context,
) -> None:
    ctx.hosts["historian"] = md(HistorianHost, {"type": "historian"})
    dash = render.grafana_dashboard(ctx)
    temps, extract, packtags = dash["panels"]
    got = lines(temps)
    assert len(got) == 8  # side and cone of four fermenters
    for i, fv in enumerate(render.FERMENTERS):
        side, cone = got[rf"\.{fv}\.TT01\.PV"], got[rf"\.{fv}\.TT02\.PV"]
        assert side["color"] == cone["color"] == SERIES[i]  # one color per fermenter
        assert not side["dashed"]
        assert cone["dashed"]
    assert [v["color"] for k, v in lines(extract).items()] == SERIES[:4]
    assert SERIES[0] == TOKENS["colors"]["ember"]  # ember first
    # the PackTags series are named by the PLC, not known here: Grafana's palette stays
    assert packtags["fieldConfig"]["overrides"] == []
    # every color is one of the brand's chart series
    used = {v["color"] for p in (temps, extract) for v in lines(p).values()}
    assert used <= set(SERIES)


def test_the_series_colors_are_not_alarm_colors() -> None:
    alarm = {v.lower() for v in TOKENS["reserved_for_alarms"].values()}
    assert not {c.lower() for c in SERIES} & alarm


def test_the_ground_truth_dashboard_colors_each_federate(ctx: Context) -> None:
    ctx.sources = Sources(ROOT, "full")
    dash = render.ground_truth_dashboard(ctx)
    assert dash["uid"] == "cm-ground-truth"
    assert dash["title"] == "CherryMountain ground truth"
    docs, events = dash["panels"]
    federates = list(ctx.sources.plant().federates)
    got = lines(docs)
    assert list(got) == [rf"\b{name}\b" for name in federates]
    assert [v["color"] for v in got.values()] == [
        SERIES[i % len(SERIES)] for i in range(len(federates))
    ]
    assert events["type"] == "logs"
    assert "fieldConfig" not in events
    # the dashboard the cm-sim image used to carry, query for query
    assert docs["targets"][0]["bucketAggs"][0] == {
        "id": "2",
        "type": "terms",
        "field": "federate",
        "settings": {"size": "10"},
    }


def test_sim_ctl_gets_the_dashboard_and_grafanas_images(ctx: Context) -> None:
    ctx.sources = Sources(ROOT, "full")
    host = md(SimCtlHost, {"type": "sim-ctl"})
    ctx.hosts["sim-ctl"] = host
    injects = by_dst(render.render_sim(ctx, "sim-ctl", host))
    dashboard = injects[
        "/var/lib/grafana/dashboards/cherrymountain/cm-ground-truth.json"
    ]
    assert json.loads(Path(dashboard.src).read_text())["uid"] == "cm-ground-truth"
    for f in brand.GRAFANA_FILES:
        assert f.dst in injects
    assert f"{render.STARTUP}/67-grafana-brand.sh" in injects


def test_sim_ctl_without_ground_truth_gets_no_grafana_files(ctx: Context) -> None:
    """The ci variant has no ground truth, so no Grafana runs on sim-ctl."""
    host = md(SimCtlHost, {"type": "sim-ctl"})
    ctx.hosts["sim-ctl"] = host
    injects = by_dst(render.render_sim(ctx, "sim-ctl", host))
    assert not [
        d
        for d in injects
        if "grafana" in d or d.startswith("/opt/cherrymountain/brand")
    ]


def test_the_cm_sim_image_no_longer_carries_the_dashboard() -> None:
    """The app renders it, so there is one definition of its lines and colors."""
    image = (ROOT / "phenix-images/cm-sim.yml").read_text()
    assert "cm-ground-truth.json <<" not in image
    assert "Documents per federate" not in image
