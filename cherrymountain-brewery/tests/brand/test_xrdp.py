"""The brand on xrdp's login screen (ews): the logo, the settings sync.py makes, and the
script that sets them in xrdp.ini. The script runs on the xrdp.ini of noble's package
(fixtures/xrdp-0.9.24.ini, from xrdp 0.9.24-4ubuntu0.1, Apache-2.0); what xrdp draws from
it is for the first boot."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from support import INJECTS, PREFIX, run, topology_nodes

SCRIPT: Path = INJECTS / "ews" / "cm-brand-xrdp"
CONF: Path = INJECTS / "brand" / "xrdp.conf"
LOGO: Path = INJECTS / "brand" / "xrdp-logo.bmp"
STOCK: Path = Path(__file__).parent / "fixtures" / "xrdp-0.9.24.ini"


def settings(text: str) -> dict[str, str]:
    """The keys an ini section or the settings file sets (not the commented ones)."""
    return dict(
        line.split("=", 1) for line in text.splitlines() if line[:1].isalpha() and "=" in line
    )


def section(text: str, name: str) -> str:
    return text.split(f"[{name}]\n", 1)[1].split("\n[", 1)[0]


@pytest.fixture
def host(tmp_path: Path) -> dict[str, str]:
    """An xrdp.ini as the package ships it, and the settings with the logo at a path that exists."""
    ini: Path = tmp_path / "xrdp.ini"
    ini.write_text(STOCK.read_text())
    conf: Path = tmp_path / "cherrymountain.conf"
    logo: Path = tmp_path / "cherrymountain-logo.bmp"
    logo.write_bytes(LOGO.read_bytes())
    conf.write_text(CONF.read_text().replace(settings(CONF.read_text())["ls_logo_filename"], str(logo)))
    return {"XRDP_INI": str(ini), "XRDP_BRAND_CONF": str(conf)}


def test_the_logo_is_a_bitmap_xrdp_can_draw() -> None:
    """xrdp 0.9 always reads BMP; its own logo is 240x140 at 24 bits, and the login box
    places the logo for that size (ls_logo_x_pos 55 in a box 350 wide)."""
    data: bytes = LOGO.read_bytes()
    assert data[:2] == b"BM" and struct.unpack("<I", data[2:6])[0] == len(data)
    width, height, planes, bits = struct.unpack("<iiHH", data[18:30])
    assert (width, height, planes, bits) == (240, 140, 1, 24)
    assert struct.unpack("<I", data[30:34])[0] == 0  # not compressed
    stock = settings(section(STOCK.read_text(), "Globals"))
    assert (int(stock["ls_width"]) - width) // 2 == int(stock["ls_logo_x_pos"])


def test_only_ews_gets_it_and_gets_all_of_it() -> None:
    want = {
        "/usr/share/xrdp/cherrymountain-logo.bmp": ("brand/xrdp-logo.bmp", "0644"),
        "/etc/xrdp/cherrymountain.conf": ("brand/xrdp.conf", "0644"),
        "/usr/local/bin/cm-brand-xrdp": ("ews/cm-brand-xrdp", "0755"),
        "/etc/phenix/startup/68-xrdp-brand.sh": ("ews/68-xrdp-brand.sh", "0755"),
    }
    for hostname, node in topology_nodes().items():
        injects = {i["dst"]: i for i in node.get("injections") or []}
        if hostname != "ews":  # the one host that runs xrdp
            assert not set(want) & set(injects), hostname
            continue
        for dst, (src, mode) in want.items():
            assert injects[dst]["src"] == PREFIX + src, dst
            assert injects[dst]["permissions"] == mode, dst
            assert (INJECTS / src).is_file(), src
    # the settings name the logo where the topology puts it
    assert settings(CONF.read_text())["ls_logo_filename"] in want
    hook = (INJECTS / "ews" / "68-xrdp-brand.sh").read_text()
    assert "/usr/local/bin/cm-brand-xrdp && systemctl try-restart xrdp.service" in hook


def test_the_settings_are_keys_the_packages_ini_has() -> None:
    """A key xrdp does not know would be ignored without a word: every one the brand sets
    is in the package's [Globals] section, set or commented out."""
    stock = section(STOCK.read_text(), "Globals")
    known = {line.lstrip("#;").split("=", 1)[0] for line in stock.splitlines() if "=" in line}
    assert set(settings(CONF.read_text())) <= known


def test_the_script_sets_the_login_screen_and_nothing_else(host: dict[str, str]) -> None:
    done = run(SCRIPT, host)
    assert done.returncode == 0, done.stderr
    assert "set 5 login screen settings" in done.stdout
    ini = Path(host["XRDP_INI"]).read_text()
    want = settings(Path(host["XRDP_BRAND_CONF"]).read_text())
    got = settings(section(ini, "Globals"))
    assert {k: got[k] for k in want} == want
    # every other line of the file is as the package has it, in the same place
    before, after = STOCK.read_text().splitlines(), ini.splitlines()
    assert len(before) == len(after)
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert sorted(b.split("=", 1)[0] for _, b in changed) == sorted(want)
    assert ("#ls_title=My Login Title", "ls_title=CherryMountain Brewery") in changed


def test_a_second_run_changes_nothing(host: dict[str, str]) -> None:
    assert run(SCRIPT, host).returncode == 0
    first = Path(host["XRDP_INI"]).read_text()
    again = run(SCRIPT, host)
    assert again.returncode == 0 and "already has the brand" in again.stdout
    assert Path(host["XRDP_INI"]).read_text() == first


def test_a_key_the_section_lacks_is_added_to_it_and_a_second_copy_is_dropped(host: dict[str, str]) -> None:
    ini = Path(host["XRDP_INI"])
    text = ini.read_text().replace("#ls_title=My Login Title\n", "").replace(
        "ls_bg_color=dedede\n", "ls_bg_color=dedede\nls_bg_color=000000\n"
    )
    ini.write_text(text)
    assert run(SCRIPT, host).returncode == 0
    lines = ini.read_text().splitlines()
    globals_ = section(ini.read_text(), "Globals").splitlines()
    assert globals_.count("ls_title=CherryMountain Brewery") == 1
    assert [line for line in globals_ if line.startswith("ls_bg_color=")] == ["ls_bg_color=EFEFEF"]
    # the new key ends the section, above the blank line before the next one
    at = lines.index("ls_title=CherryMountain Brewery")
    assert lines[at + 1] == "" and lines[at + 2].startswith("[") and lines[at - 1] != ""
    assert sum(line.startswith("ls_title=") for line in lines) == 1


@pytest.mark.parametrize("missing", ["ini", "conf", "logo", "section"])
def test_it_stops_and_leaves_the_ini_alone_when_something_is_missing(host: dict[str, str], missing: str) -> None:
    ini = Path(host["XRDP_INI"])
    if missing == "ini":
        ini.unlink()
    elif missing == "conf":
        Path(host["XRDP_BRAND_CONF"]).unlink()
    elif missing == "logo":
        Path(settings(Path(host["XRDP_BRAND_CONF"]).read_text())["ls_logo_filename"]).unlink()
    else:
        ini.write_text(ini.read_text().replace("[Globals]", "[Global]"))
    before = ini.read_text() if ini.exists() else None
    done = run(SCRIPT, host)
    assert done.returncode == 1 and "cm-brand-xrdp:" in done.stderr
    assert (ini.read_text() if ini.exists() else None) == before
    assert list(ini.parent.glob("xrdp.ini.*")) == []  # no temporary file left behind
