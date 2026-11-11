"""Where the brewery's brand goes on the hosts the app renders.

``brand/`` holds the mark and the colors; ``sync.py`` copies its files into
``phenix-injects/brand/`` and the renderers inject them from there, so a changed mark
reaches every host through the copy step and nothing here holds a second copy. This
module only says which file lands where on which kind of host. The colors of the
screens are not here: FUXA's come from ``fuxa/build_project.py`` and the Grafana series
colors from ``tokens.json`` through :func:`series_colors`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict

#: The name as it is written on every screen.
NAME: Final[str] = "CherryMountain Brewery"


class BrandFile(BaseModel):
    """One file of ``phenix-injects/brand/`` and where a host keeps it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    dst: str
    description: str


#: FUXA (``/opt/fuxa``, DESIGN.md 12.2): the client's tab icon replaces the one FUXA
#: ships (``client/dist/index.html`` links ``favicon.ico``), and the mark goes where FUXA
#: serves images from without a sign-in (``server/main.js`` ``/_images``), which is where
#: the project's ``navigation.logo`` and the sign-in dialog read it (``LOGO_URL`` in
#: fuxa/build_project.py).
FUXA_FILES: Final[tuple[BrandFile, ...]] = (
    BrandFile(
        name="favicon.ico",
        dst="/opt/fuxa/client/dist/favicon.ico",
        description="the brewery's tab icon for FUXA",
    ),
    BrandFile(
        name="icon.svg",
        dst="/opt/fuxa/server/_images/Brand/icon.svg",
        description="the brewery's mark for FUXA's menu and sign-in dialog",
    ),
)

#: The page title of each FUXA role (66-fuxa-brand.sh writes it into the client): the
#: role is in the title so the two instances can be told apart in the white cell's tabs.
FUXA_TITLES: Final[dict[str, str]] = {
    "scada": f"{NAME} SCADA",
    "bas": f"{NAME} BAS",
}


#: Where the Grafana hosts keep the brand files that cm-grafana-brand copies over Grafana's
#: own images (phenix-injects/grafana/cm-grafana-brand names the same files).
GRAFANA_BRAND_DIR: Final[str] = "/opt/cherrymountain/brand"
GRAFANA_FILES: Final[tuple[BrandFile, ...]] = tuple(
    BrandFile(
        name=name,
        dst=f"{GRAFANA_BRAND_DIR}/{name}",
        description=f"the brewery's {what} for Grafana",
    )
    for name, what in (
        ("icon.svg", "mark (menu, sign-in, and loading logo)"),
        ("icon-32.png", "tab icon"),
        ("icon-256.png", "touch icon"),
        ("login-background.svg", "sign-in page background"),
    )
)


def series_colors(tokens_json: Path) -> list[str]:
    """The chart series colors of ``tokens.json`` (``roles.chart_series``), in order, as
    ``#RRGGBB``: what a Grafana panel's lines use, ember first."""
    tokens: dict[str, dict[str, object]] = json.loads(
        tokens_json.read_text(encoding="utf-8")
    )
    names: object = tokens["roles"]["chart_series"]
    if not isinstance(names, list):
        raise ValueError("tokens.json roles.chart_series must be a list of color names")
    colors: dict[str, str] = {k: str(v) for k, v in tokens["colors"].items()}
    return [colors[str(n)] for n in names]


def line_color(regex: str, color: str, dashed: bool = False) -> dict[str, Any]:
    """A Grafana field override: the series whose name matches ``regex`` (``byRegexp``,
    a match anywhere in the display name) are drawn in ``color``, dashed if asked.
    ``fixedColor`` and ``custom.lineStyle`` are the ids Grafana's own panels use
    (12.4.14, ``public/build``)."""
    properties: list[dict[str, Any]] = [
        {"id": "color", "value": {"mode": "fixed", "fixedColor": color}}
    ]
    if dashed:
        properties.append(
            {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}
        )
    return {"matcher": {"id": "byRegexp", "options": regex}, "properties": properties}
