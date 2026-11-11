"""FUXA's page title: 66-fuxa-brand.sh on a stand-in index.html."""

from __future__ import annotations

from pathlib import Path

import pytest
from support import INJECTS, run

FUXA_BRAND: Path = INJECTS / "fuxa" / "66-fuxa-brand.sh"
FUXA_INDEX = (
    '<!doctype html><html><head><base href="/"><link rel="icon" href="favicon.ico">'
    "<title>FUXA</title><meta charset=\"UTF-8\"></head><body></body></html>\n"
)


def test_fuxa_page_title_is_set_and_can_be_set_again(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text(FUXA_INDEX)
    env = {"FUXA_INDEX": str(index), "FUXA_TITLE": "CherryMountain Brewery SCADA"}
    assert run(FUXA_BRAND, env).returncode == 0
    assert "<title>CherryMountain Brewery SCADA</title>" in index.read_text()
    assert index.read_text().replace("CherryMountain Brewery SCADA", "FUXA") == FUXA_INDEX  # nothing else moved
    assert run(FUXA_BRAND, {**env, "FUXA_TITLE": "CherryMountain Brewery BAS"}).returncode == 0
    assert "<title>CherryMountain Brewery BAS</title>" in index.read_text()
    assert index.read_text().count("<title>") == 1


@pytest.mark.parametrize("title", ["a|b", "a&b", "x</title><script>", 'a"b', "a$b"])
def test_fuxa_page_title_refuses_what_could_break_the_page(tmp_path: Path, title: str) -> None:
    index = tmp_path / "index.html"
    index.write_text(FUXA_INDEX)
    done = run(FUXA_BRAND, {"FUXA_INDEX": str(index), "FUXA_TITLE": title})
    assert done.returncode == 1 and index.read_text() == FUXA_INDEX


def test_fuxa_page_title_fails_without_a_title_to_set(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("<html></html>\n")
    done = run(FUXA_BRAND, {"FUXA_INDEX": str(index), "FUXA_TITLE": "CherryMountain Brewery"})
    assert done.returncode == 1 and "no <title> to set" in done.stderr
