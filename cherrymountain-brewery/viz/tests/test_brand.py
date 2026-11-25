"""The brand on cm-viz's pages: the mark, the tab icon, and the frame colors come from
brand/ (through sync.py's copies), and the pages and server carry them offline."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cherrymountain_viz.server import WEB, Hub, csp, http_reply

ROOT: Path = Path(__file__).resolve().parents[2]
BRAND: Path = ROOT / "brand"
WEB_BRAND: Path = WEB / "brand"
TOKENS: dict = json.loads((BRAND / "tokens.json").read_text())
PAGES: tuple[str, ...] = ("index.html", "svg.html")


def test_the_copies_are_the_brand_files() -> None:
    """What cm-viz shows is a byte-for-byte copy of brand/ (``python sync.py`` writes it)."""
    for name in ("icon.svg", "icon-small.svg", "favicon.ico"):
        assert (WEB_BRAND / name).read_bytes() == (BRAND / name).read_bytes(), name


def test_tokens_css_has_every_color_of_the_tokens() -> None:
    css = (WEB_BRAND / "tokens.css").read_text()
    for name, value in TOKENS["colors"].items():
        assert f"--cm-{name}: {value};" in css
    for role in ("page_bg_dark", "page_fg_dark", "menu_bg", "stripe", "primary", "primary_fg"):
        assert f"--cm-{role.replace('_', '-')}: var(--cm-{TOKENS['roles'][role]});" in css


@pytest.mark.parametrize("page", PAGES)
def test_a_page_has_the_tab_icon_the_title_the_mark_and_the_tokens(page: str) -> None:
    html = (WEB / page).read_text()
    assert re.search(r"<title>CherryMountain Brewery [^<]*view</title>", html)
    assert 'rel="icon" href="brand/favicon.ico"' in html
    assert 'rel="icon" href="brand/icon-small.svg" type="image/svg+xml"' in html
    # the tokens load before the page's own CSS, which reads them
    assert html.index("brand/tokens.css") < html.index("style.css")
    # the mark at 32 px is the small drawing (the full one is for 48 px and up)
    assert '<img class="mark" src="brand/icon-small.svg" alt="" width="32" height="32">' in html
    assert "CherryMountain Brewery: ground truth" in html
    assert 'href="data:,"' not in html  # the blank icon is gone


def test_every_file_a_page_names_exists_and_nothing_is_fetched_from_elsewhere() -> None:
    for page in PAGES:
        html = (WEB / page).read_text()
        refs = re.findall(r'(?:href|src)="([^"#]+)"', html)
        for ref in refs:
            assert not re.match(r"[a-z]+:|//", ref) or ref.startswith("data:"), ref
            if not ref.startswith("data:") and ref != "svg.html" and ref != "index.html":
                assert (WEB / ref).is_file(), f"{page} names {ref}, which web/ does not have"


def test_the_frame_colors_are_the_tokens_and_the_hex_that_remains_means_something() -> None:
    """style.css names the brand colors by their custom properties; the colors left as
    hex are the ones that carry a meaning (alarm red, warn amber, ok green, info blue,
    the pills), and none is a brand color."""
    css = (WEB / "style.css").read_text() + (WEB / "svg.css").read_text()
    brand = {v.lower() for v in TOKENS["colors"].values()}
    assert not {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}\b", css)} & brand
    root = css[css.index(":root"): css.index("}", css.index(":root"))]
    for prop, token in (
        ("--bg", "page-bg-dark"),
        ("--panel", "menu-bg"),
        ("--text", "page-fg-dark"),
        ("--accent", "primary"),
    ):
        assert f"{prop}: var(--cm-{token})" in root
    assert "border-bottom: 3px solid var(--cm-stripe)" in css  # the stripe under the header


def test_the_alarm_colors_are_not_used_for_decoration() -> None:
    css = (WEB / "style.css").read_text()
    reserved = {v.lower() for v in TOKENS["reserved_for_alarms"].values()}
    # the page's own meaning colors are not the reserved ones either way, and the active
    # tab and the stripe (the brand's ember) never take an alarm color
    assert not {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}\b", css)} & reserved


@pytest.mark.parametrize(
    ("route", "ctype"),
    [
        ("/brand/favicon.ico", "image/vnd.microsoft.icon"),
        ("/brand/icon-small.svg", "image/svg+xml"),
        ("/brand/icon.svg", "image/svg+xml"),
        ("/brand/tokens.css", "text/css; charset=utf-8"),
    ],
)
def test_the_server_serves_the_brand_files(route: str, ctype: str) -> None:
    reply = http_reply(route, Hub())
    assert reply.status_code == 200
    assert reply.headers["Content-Type"] == ctype
    # the policy keeps images and styles to the server itself, which is where these come from
    policy = csp()
    assert "img-src 'self' data:" in policy and "style-src 'self'" in policy
