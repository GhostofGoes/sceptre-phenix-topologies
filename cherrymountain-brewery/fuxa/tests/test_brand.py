"""The brand on FUXA's screens: the frame (header, menu, sign-in dialog) takes the colors
and the mark of brand/, and nothing in the process area does.

Checked on every project the builder writes: the ci project, and full and lite for
scada-fuxa and bas-fuxa. The rendered result (header, menu, sign-in dialog, views, an
active alarm) was looked at in a real FUXA; see fuxa/README.md.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from fuxa_test_support import ROOT, build_project, load_builder

BUILDER = load_builder()
TOKENS: dict[str, Any] = json.loads((ROOT / "brand" / "tokens.json").read_text())
BRAND_COLORS: dict[str, str] = {k.lower(): v.lower() for k, v in TOKENS["colors"].items()}
ALARM_COLORS: set[str] = {v.lower() for v in TOKENS["reserved_for_alarms"].values()}
HEX: re.Pattern[str] = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b")

PROJECTS: list[tuple[str, str]] = [
    ("ci", "scada-fuxa"),
    ("full", "scada-fuxa"),
    ("full", "bas-fuxa"),
    ("lite", "scada-fuxa"),
    ("lite", "bas-fuxa"),
]


@pytest.fixture(scope="module", params=PROJECTS, ids=[f"{v}-{h}" for v, h in PROJECTS])
def project(request: pytest.FixtureRequest) -> dict[str, Any]:
    variant, host = request.param
    return build_project(variant, host)


def hexes(text: str) -> set[str]:
    return {h.lower()[:7] for h in HEX.findall(text)}


def role(name: str) -> str:
    return TOKENS["colors"][TOKENS["roles"][name]]


def test_the_builder_reads_the_same_tokens() -> None:
    assert BUILDER.BRAND.colors == TOKENS["colors"]
    assert BUILDER.BRAND.reserved_for_alarms == TOKENS["reserved_for_alarms"]


def test_the_frame_colors_are_the_tokens(project: dict[str, Any]) -> None:
    layout = project["hmi"]["layout"]
    assert layout["header"]["bkcolor"] == role("header_bg")
    assert layout["header"]["fgcolor"] == role("header_fg")
    assert layout["navigation"]["bkcolor"] == role("menu_bg")
    assert layout["navigation"]["fgcolor"] == role("menu_fg")
    # ink and paper on the header, coal and paper on the menu, by name
    assert (role("header_bg"), role("menu_bg")) == (TOKENS["colors"]["ink"], TOKENS["colors"]["coal"])


def test_the_mark_and_name_are_in_the_frame(project: dict[str, Any]) -> None:
    layout = project["hmi"]["layout"]
    assert layout["navigation"]["logo"] == BUILDER.LOGO_URL == "_images/Brand/icon.svg"
    assert layout["header"]["title"] == "CherryMountain Brewery"
    (label,) = layout["header"]["items"]
    assert label["type"] == "label" and label["property"]["text"] == "CherryMountain Brewery"
    # FUXA's header is 46 px by default; the stripe and the mark's row need more
    assert layout["header"]["height"] == BUILDER.HEADER_HEIGHT >= BUILDER.LOGO_SIZE
    assert BUILDER.LOGO_SIZE >= 48  # the full drawing is for 48 px and up (brand/README.md)


def test_the_stripe_and_dialog_css_uses_only_brand_colors(project: dict[str, Any]) -> None:
    css = project["hmi"]["layout"]["customStyles"]
    used = hexes(css)
    assert used, "the frame CSS names its colors"
    assert used <= set(BRAND_COLORS.values()), used - set(BRAND_COLORS.values())
    assert role("stripe").lower() in used  # the ember stripe
    assert not used & ALARM_COLORS


def test_no_brand_color_is_an_alarm_color() -> None:
    assert not set(BRAND_COLORS.values()) & ALARM_COLORS


FRAME_SELECTORS = (
    ".header",
    ".sidenav-menu",
    ".mat-mdc-dialog-container:has(app-login)",
    ".mat-mdc-dialog-container app-login",
    "app-login",
)


def stray_selectors(css: str) -> list[str]:
    """The selectors of ``css`` that do not start at the header, the menu, or the sign-in dialog."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # a comma inside [...] or (...) does not separate selectors
    selectors = [
        s.strip()
        for block in re.findall(r"([^{}]+)\{", css)
        for s in re.split(r",(?![^\[(]*[\])])", block)
    ]
    assert selectors
    return [s for s in selectors if not s.startswith(FRAME_SELECTORS)]


def test_the_frame_css_only_reaches_the_frame(project: dict[str, Any]) -> None:
    """Every rule starts at the header, the menu, or the sign-in dialog, so no view
    and none of a view's buttons (the Batch view's Start, Hold, ...) takes it."""
    assert not stray_selectors(project["hmi"]["layout"]["customStyles"])


def test_the_check_catches_a_rule_that_could_reach_a_view() -> None:
    assert stray_selectors(".header { a: b } button { color: red }") == ["button"]
    assert stray_selectors(".header, .md-btn { a: b }") == [".md-btn"]
    assert stray_selectors('.header .x[style*="1, 2"] { a: b }') == []


def test_the_process_area_keeps_its_gray_and_takes_no_brand_color(project: dict[str, Any]) -> None:
    gray = TOKENS["process_area"]["background"].lower()
    for view in project["hmi"]["views"]:
        assert view["profile"]["bkcolor"].lower() == gray + "ff", view["name"]
        painted = hexes(view["svgcontent"]) | hexes(json.dumps(view["items"])) | hexes(json.dumps(view["profile"]))
        taken = painted & set(BRAND_COLORS.values())
        assert not taken, f"view {view['name']} uses brand colors {taken}"


def test_the_batch_buttons_keep_their_own_readable_style(project: dict[str, Any]) -> None:
    views = {v["name"]: v for v in project["hmi"]["views"]}
    if "Batch" not in views:
        pytest.skip("this project has no Batch view")
    svg = views["Batch"]["svgcontent"]
    styles = re.findall(r'<button[^>]*style="([^"]*)"', svg)
    assert len(styles) == 5
    for style in styles:
        # light gray with dark text, set on the button itself: independent of the frame
        assert "background-color:rgba(238,238,238,1)" in style and "color:rgba(33,33,33,1)" in style


def test_the_alarm_levels_keep_the_reserved_colors(project: dict[str, Any]) -> None:
    reserved = TOKENS["reserved_for_alarms"]
    want = {
        "highhigh": reserved["critical"],
        "high": reserved["high"],
        "low": reserved["medium"],
        "info": reserved["low"],
    }
    assert project["alarms"], "every project has alarms"
    for alarm in project["alarms"]:
        for level, color in want.items():
            assert alarm[level]["bkcolor"].lower() == color.lower(), (alarm["name"], level)


def test_the_header_label_is_shaped_as_fuxa_stores_one(project: dict[str, Any]) -> None:
    """HeaderItem (client/src/app/_models/hmi.ts): an id, a type, margins, and the label's
    text in ``property.text`` (home.component.ts loadHeaderItems reads it from there)."""
    (item,) = project["hmi"]["layout"]["header"]["items"]
    assert item["id"].startswith("i_")
    assert item["type"] in ("button", "label", "image")
    assert isinstance(item["marginLeft"], int) and isinstance(item["marginRight"], int)
    assert item["property"]["events"] == [] and item["property"]["actions"] == []
