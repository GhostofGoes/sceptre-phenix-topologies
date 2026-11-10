"""sync.py and the brand: copies of brand/ for the screens, the tokens as CSS, Grafana's
sign-in background, and the Guacamole branding extension."""

from __future__ import annotations

import json
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
import sync

REAL_ROOT: Path = Path(__file__).resolve().parents[3]


@pytest.fixture
def brand_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository with only brand/ and the Guacamole extension's sources, copied."""
    root: Path = tmp_path / "repo"
    shutil.copytree(REAL_ROOT / "brand", root / "brand")
    shutil.copytree(
        REAL_ROOT / "phenix-injects" / "guacamole" / "branding",
        root / "phenix-injects" / "guacamole" / "branding",
    )
    monkeypatch.setattr(sync, "ROOT", root)
    return root


def edit_tokens(root: Path, edit) -> None:  # noqa: ANN001 - a callable on the parsed JSON
    path: Path = root / "brand" / "tokens.json"
    tokens: dict = json.loads(path.read_text())
    edit(tokens)
    path.write_text(json.dumps(tokens))


def test_the_brand_is_copied_and_then_in_sync(brand_root: Path) -> None:
    assert sync.sync_brand(check=True) != []  # nothing copied yet
    assert sync.sync_brand(check=False) == []
    assert sync.sync_brand(check=True) == []
    injects: Path = brand_root / "phenix-injects" / "brand"
    for name in (
        "icon.svg",
        "icon-small.svg",
        "favicon.ico",
        "icon-32.png",
        "icon-64.png",
        "icon-256.png",
        "tokens.json",
        "wallpaper-1080p.png",
    ):
        assert (injects / name).read_bytes() == (brand_root / "brand" / name).read_bytes(), name
    web: Path = brand_root / "viz" / "cherrymountain_viz" / "web" / "brand"
    for name in sync.VIZ_BRAND_FILES:
        assert (web / name).read_bytes() == (brand_root / "brand" / name).read_bytes(), name
    assert (injects / "tokens.css").read_text() == (web / "tokens.css").read_text()


def test_a_changed_mark_is_stale_in_every_copy_until_synced(brand_root: Path) -> None:
    sync.sync_brand(check=False)
    (brand_root / "brand" / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    problems: list[str] = sync.sync_brand(check=True)
    assert any("phenix-injects/brand/icon.svg differs from brand/icon.svg" in p for p in problems)
    assert any("viz/cherrymountain_viz/web/brand/icon.svg differs" in p for p in problems)
    # the Guacamole extension carries the mark too
    assert any("cherrymountain-brand.jar is not what sync.py makes" in p for p in problems)
    assert sync.sync_brand(check=False) == []
    assert sync.sync_brand(check=True) == []


def test_a_changed_color_reaches_tokens_css_and_the_extension(brand_root: Path) -> None:
    sync.sync_brand(check=False)
    edit_tokens(brand_root, lambda t: t["colors"].update(ember="#D04A20"))
    assert sync.sync_brand(check=True) != []
    sync.sync_brand(check=False)
    css: str = (brand_root / "phenix-injects" / "brand" / "tokens.css").read_text()
    assert "--cm-ember: #D04A20;" in css and "#E2552B" not in css
    jar: Path = brand_root / "phenix-injects" / "guacamole" / "cherrymountain-brand.jar"
    with zipfile.ZipFile(jar) as zf:
        assert "--cm-ember: #D04A20;" in zf.read("tokens.css").decode()


def test_the_login_background_is_the_dark_page_color(brand_root: Path) -> None:
    sync.sync_brand(check=False)
    svg: str = (brand_root / "phenix-injects" / "brand" / "login-background.svg").read_text()
    assert 'fill="#1B1B1F"' in svg and 'width="2261" height="1272"' in svg
    edit_tokens(brand_root, lambda t: t["colors"].update(ink="#101012"))
    assert 'fill="#101012"' in sync.login_background_svg()


def test_the_xrdp_settings_take_the_brands_colors(brand_root: Path) -> None:
    sync.sync_brand(check=False)
    conf: str = (brand_root / "phenix-injects" / "brand" / "xrdp.conf").read_text()
    settings: dict[str, str] = dict(
        line.split("=", 1) for line in conf.splitlines() if line and not line.startswith("#")
    )
    assert settings == {
        "ls_title": "CherryMountain Brewery",
        "ls_top_window_bg_color": "1B1B1F",  # xrdp writes a color without the #
        "ls_bg_color": "EFEFEF",
        "blue": "E2552B",
        "ls_logo_filename": sync.XRDP_LOGO_PATH,
    }
    edit_tokens(brand_root, lambda t: t["colors"].update(ember="#d04a20"))
    assert "blue=D04A20\n" in sync.xrdp_conf()
    assert any("xrdp.conf is not what sync.py makes" in p for p in sync.sync_brand(check=True))


def test_tokens_css_has_colors_and_single_color_roles_only(brand_root: Path) -> None:
    css: str = sync.tokens_css()
    assert "--cm-ember: #E2552B;" in css
    assert "--cm-stripe: var(--cm-ember);" in css and "--cm-header-bg: var(--cm-ink);" in css
    assert "chart" not in css  # chart_series is a list, not one color


def test_a_color_that_is_not_hex_is_refused(brand_root: Path) -> None:
    edit_tokens(brand_root, lambda t: t["colors"].update(ink="black"))
    with pytest.raises(ValueError, match="not #RRGGBB"):
        sync.tokens_css()


def test_a_brand_color_may_not_be_an_alarm_color(brand_root: Path) -> None:
    edit_tokens(
        brand_root,
        lambda t: t["colors"].update(flame=t["reserved_for_alarms"]["medium"].lower()),
    )
    with pytest.raises(ValueError, match="reserved for the medium alarm"):
        sync.tokens_css()
    assert sync.main(["--check"]) == 1  # main reports it as a FAIL line, not a traceback


def test_a_role_must_name_a_color(brand_root: Path) -> None:
    edit_tokens(brand_root, lambda t: t["roles"].update(stripe="crimson"))
    with pytest.raises(ValueError, match="role stripe names crimson"):
        sync.tokens_css()


def test_the_extension_is_the_same_bytes_every_time(brand_root: Path) -> None:
    first: bytes = sync.guacamole_jar()
    assert sync.guacamole_jar() == first
    with zipfile.ZipFile(BytesIO(first)) as zf:
        assert zf.namelist()[0] == sync.GUAC_MANIFEST  # the manifest comes first
        assert {i.date_time for i in zf.infolist()} == {sync.ZIP_EPOCH}
        assert {i.compress_type for i in zf.infolist()} == {zipfile.ZIP_STORED}
        for name in sync.GUAC_BRAND_FILES:
            assert zf.read(name) == (brand_root / "brand" / name).read_bytes()


def test_the_extension_must_hold_every_file_its_manifest_names(brand_root: Path) -> None:
    manifest: Path = brand_root / "phenix-injects" / "guacamole" / "branding" / sync.GUAC_MANIFEST
    doc: dict = json.loads(manifest.read_text())
    doc["css"].append("missing.css")
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="missing.css"):
        sync.guacamole_jar()


def test_a_source_file_may_not_hide_a_brand_file_of_the_extension(brand_root: Path) -> None:
    (brand_root / "phenix-injects" / "guacamole" / "branding" / "icon.svg").write_text("<svg/>")
    with pytest.raises(ValueError, match="would be overwritten"):
        sync.guacamole_jar()


def test_every_file_of_brand_is_copied_or_left_out_on_purpose(repo: Path) -> None:
    """A new file in brand/ is copied to the screens, or named in BRAND_NOT_COPIED with a
    reason: it cannot be forgotten."""
    held: set[str] = {p.name for p in (repo / "brand").iterdir() if p.is_file()}
    assert held == {p.name for p in sync.brand_assets()} | set(sync.BRAND_NOT_COPIED)
    assert all(sync.BRAND_NOT_COPIED.values()), "every file left out has a reason"
    # the desktops run 1440x900: the 1080p wallpaper is the one they take
    assert "wallpaper.png" in sync.BRAND_NOT_COPIED
    assert "wallpaper-1080p.png" not in sync.BRAND_NOT_COPIED


def test_the_committed_copies_are_the_brand_files(repo: Path) -> None:
    """Without running sync.py: every committed copy is byte for byte the brand file."""
    for c in sync.brand_copies():
        assert c.dst.read_bytes() == c.src.read_bytes(), sync.rel(c.dst)
    for dst, data in sync.brand_generated().items():
        assert dst.read_bytes() == data, sync.rel(dst)
