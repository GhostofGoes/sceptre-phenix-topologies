"""The brand on FUXA, as the app renders it: the tab icon, the mark, and the page title."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from box import Box

from cherrymountain_apps.cherrymountain import brand, render
from cherrymountain_apps.cherrymountain.metadata import (
    FuxaHost,
)
from cherrymountain_apps.cherrymountain.render import Context
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


def test_fuxa_gets_the_tab_icon_the_mark_and_the_title(ctx: Context) -> None:
    host = ctx.hosts["scada-fuxa"]
    assert isinstance(host, FuxaHost)
    injects = by_dst(render.render_fuxa(ctx, "scada-fuxa", host))
    for f in brand.FUXA_FILES:
        inject = injects[f.dst]
        assert inject.permissions == "0644"
        # the same bytes as brand/, through the copy phenix-injects/brand/ (sync.py)
        assert Path(inject.src).read_bytes() == (BRAND / f.name).read_bytes()
    env = Path(injects["/etc/default/cm-fuxa"].src).read_text()
    assert 'FUXA_TITLE="CherryMountain Brewery SCADA"\n' in env
    script = injects[f"{render.STARTUP}/66-fuxa-brand.sh"]
    assert script.permissions == "0755"
    assert (
        Path(script.src).read_bytes()
        == (ROOT / "phenix-injects/fuxa/66-fuxa-brand.sh").read_bytes()
    )


def test_the_project_names_the_mark_where_the_app_injects_it(ctx: Context) -> None:
    host = ctx.hosts["scada-fuxa"]
    assert isinstance(host, FuxaHost)
    project = ctx.sources.fuxa_project(host.project, "scada-fuxa")
    logo = project["hmi"]["layout"]["navigation"]["logo"]
    served_from = (
        "/opt/fuxa/server/"  # FUXA's working dir: it serves _images/ at /_images
    )
    icon = next(f for f in brand.FUXA_FILES if f.name == "icon.svg")
    assert icon.dst == served_from + logo
    favicon = next(f for f in brand.FUXA_FILES if f.name == "favicon.ico")
    assert (
        favicon.dst == "/opt/fuxa/client/dist/favicon.ico"
    )  # index.html links favicon.ico


def test_each_fuxa_role_has_its_own_title() -> None:
    assert brand.FUXA_TITLES == {
        "scada": "CherryMountain Brewery SCADA",
        "bas": "CherryMountain Brewery BAS",
    }
    # the roles of the metadata and the titles stay in step
    role = FuxaHost.model_fields["role"].annotation
    assert set(role.__args__) == set(brand.FUXA_TITLES)  # type: ignore[union-attr]


def test_the_ci_topology_injects_the_same_fuxa_brand_files() -> None:
    """The ci slice has no app, so its topology injects what the app would."""
    import yaml

    topology = yaml.safe_load((ROOT / "phenix-configs/topology-ci.yml").read_text())
    node = next(
        n for n in topology["spec"]["nodes"] if n["general"]["hostname"] == "scada-fuxa"
    )
    injects = {i["dst"]: i["src"] for i in node["injections"]}
    prefix = "/phenix/injects/${BRANCH_NAME}/"
    for f in brand.FUXA_FILES:
        assert injects[f.dst] == f"{prefix}brand/{f.name}"
    assert (
        injects[f"{render.STARTUP}/66-fuxa-brand.sh"]
        == f"{prefix}fuxa/66-fuxa-brand.sh"
    )
    env = (ROOT / "phenix-injects/fuxa/fuxa.env").read_text()
    assert 'FUXA_TITLE="CherryMountain Brewery SCADA"' in env
