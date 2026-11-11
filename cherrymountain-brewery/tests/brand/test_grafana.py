"""The brand on Grafana (historian and sim-ctl): cm-grafana-brand against a tree laid out as
the grafana_12.4.14 package lays it out. Grafana was not run: the rendered result is for the
first boot (phenix-images/README.md)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from support import INJECTS, run

GRAFANA_BRAND: Path = INJECTS / "grafana" / "cm-grafana-brand"
#: The image files of the grafana_12.4.14 package, each three times (find public -name ...):
#: public/img, public/build/img, and public/build/static/img with webpack's hash.
GRAFANA_LAYOUT = (
    "img/fav32.png",
    "img/apple-touch-icon.png",
    "img/grafana_icon.svg",
    "img/g8_login_dark.svg",
    "img/g8_login_light.svg",
    "img/grafana_mask_icon.svg",
    "img/icn-dashboard.svg",
    "build/img/fav32.png",
    "build/img/apple-touch-icon.png",
    "build/img/grafana_icon.svg",
    "build/img/g8_login_dark.svg",
    "build/img/g8_login_light.svg",
    "build/img/grafana_mask_icon.svg",
    "build/static/img/grafana_icon.1e0deb6b.svg",
    "build/static/img/g8_login_dark.571d84ac.svg",
    "build/static/img/g8_login_light.366e4b68.svg",
    "build/static/img/grafana_text_logo_dark.70a1e5ee.svg",
)
#: What the script replaces, and with which brand file.
GRAFANA_REPLACED = {
    "grafana_icon": "icon.svg",
    "fav32": "icon-32.png",
    "apple-touch-icon": "icon-256.png",
    "g8_login_dark": "login-background.svg",
    "g8_login_light": "login-background.svg",
}


@pytest.fixture
def grafana_public(tmp_path: Path) -> Path:
    public = tmp_path / "public"
    for rel in GRAFANA_LAYOUT:
        path = public / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"grafana's {rel}")
    return public


def grafana_env(public: Path) -> dict[str, str]:
    return {"GRAFANA_PUBLIC": str(public), "CM_BRAND_DIR": str(INJECTS / "brand")}


def test_grafana_gets_the_brand_images_in_all_three_places(grafana_public: Path) -> None:
    done = run(GRAFANA_BRAND, grafana_env(grafana_public))
    assert done.returncode == 0, done.stderr
    for rel in GRAFANA_LAYOUT:
        stem = re.sub(r"\.[0-9a-f]{8}(?=\.\w+$)", "", Path(rel).name).rsplit(".", 1)[0]
        path = grafana_public / rel
        if stem in GRAFANA_REPLACED:
            assert path.read_bytes() == (INJECTS / "brand" / GRAFANA_REPLACED[stem]).read_bytes(), rel
        else:  # everything else keeps Grafana's own file
            assert path.read_text() == f"grafana's {rel}", rel
    assert "replaced 13 image file(s)" in done.stdout


def test_grafana_brand_is_safe_to_run_again(grafana_public: Path) -> None:
    run(GRAFANA_BRAND, grafana_env(grafana_public))
    snapshot = {p: p.read_bytes() for p in grafana_public.rglob("*") if p.is_file()}
    assert run(GRAFANA_BRAND, grafana_env(grafana_public)).returncode == 0
    assert snapshot == {p: p.read_bytes() for p in grafana_public.rglob("*") if p.is_file()}


def test_grafana_brand_fails_where_grafana_moved_its_images(tmp_path: Path) -> None:
    (tmp_path / "public").mkdir()
    done = run(GRAFANA_BRAND, grafana_env(tmp_path / "public"))
    assert done.returncode == 1 and "none of Grafana's images" in done.stdout


def test_grafana_brand_fails_without_a_brand_file(grafana_public: Path, tmp_path: Path) -> None:
    empty = tmp_path / "brand"
    empty.mkdir()
    done = run(GRAFANA_BRAND, {"GRAFANA_PUBLIC": str(grafana_public), "CM_BRAND_DIR": str(empty)})
    assert done.returncode == 1 and "FAIL: no " in done.stdout


def test_the_startup_hook_runs_the_script_the_app_injects() -> None:
    hook = (INJECTS / "grafana" / "67-grafana-brand.sh").read_text()
    assert "/usr/local/bin/cm-grafana-brand" in hook
