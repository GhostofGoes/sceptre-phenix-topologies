"""The brand on the workstations: the wallpaper, the mark, and the script that sets the
wallpaper in the Xfce session. The script runs against a stand-in xfconf-query; what a real
desktop does with it is for the first boot (phenix-images/README.md)."""

from __future__ import annotations

import os
import stat
import struct
from pathlib import Path
from typing import Any

import pytest
from support import INJECTS, PREFIX, ROOT, run, topology_nodes

WALLPAPER: Path = INJECTS / "desktop" / "cm-brand-wallpaper"
DESKTOPS: tuple[str, ...] = ("ews", "hmi-brewhouse", "hmi-packaging", "corp-ws", "vendor-ws", "soc-analyst")


def test_every_desktop_gets_the_wallpaper_the_mark_and_the_script() -> None:
    nodes = topology_nodes()
    want = {
        "/usr/share/backgrounds/cherrymountain.png": ("brand/wallpaper-1080p.png", "0644"),
        "/usr/share/pixmaps/cherrymountain.png": ("brand/icon-256.png", "0644"),
        "/usr/local/bin/cm-brand-wallpaper": ("desktop/cm-brand-wallpaper", "0755"),
        "/etc/xdg/autostart/cm-brand-wallpaper.desktop": ("desktop/cm-brand-wallpaper.desktop", "0644"),
    }
    for host in DESKTOPS:
        injects = {i["dst"]: i for i in nodes[host].get("injections") or []}
        for dst, (src, mode) in want.items():
            assert injects[dst]["src"] == PREFIX + src, (host, dst)
            assert injects[dst]["permissions"] == mode, (host, dst)
            assert (INJECTS / src).is_file(), src


def test_the_desktops_run_below_the_wallpapers_size() -> None:
    """The desktops are 1440x900 (the lightdm display-setup-script of both images), so the
    1920x1080 wallpaper is the one they take, not the 3840x2160 one."""
    for image in ("cm-desktop.yml", "cm-ews.yml"):
        assert "--mode 1440x900" in (ROOT / "phenix-images" / image).read_text()
    width, height = struct.unpack(">II", (INJECTS / "brand" / "wallpaper-1080p.png").read_bytes()[16:24])
    assert (width, height) == (1920, 1080) and width >= 1440 and height >= 900


def test_one_source_has_one_mode_in_every_topology() -> None:
    """phenix refuses two injections that give one source different permissions."""
    modes: dict[str, set[str]] = {}
    for node in topology_nodes().values():
        for inject in node.get("injections") or []:
            modes.setdefault(inject["src"], set()).add(str(inject.get("permissions", "")))
    clash = {src: m for src, m in modes.items() if len(m) > 1}
    assert not clash, clash


def test_the_launchers_we_own_show_the_mark() -> None:
    for name in ("cm-analyst.desktop", "cm-brand-wallpaper.desktop"):
        text = (INJECTS / "desktop" / name).read_text()
        assert "Icon=cherrymountain\n" in text, name
    # the icon is where /usr/share/pixmaps keeps it on the node that shows the analyst launcher
    soc = {i["dst"] for i in topology_nodes()["soc-analyst"]["injections"]}
    assert "/usr/share/pixmaps/cherrymountain.png" in soc


XFCONF_STUB = r"""#!/bin/bash
# A stand-in for xfconf-query on the xfce4-desktop channel: properties in $XFCONF_STORE.
store="$XFCONF_STORE"
chan=""; list=0; prop=""; val=""; setting=0
while [ $# -gt 0 ]; do
  case "$1" in
    -c) chan="$2"; shift 2 ;;
    -l) list=1; shift ;;
    -p) prop="$2"; shift 2 ;;
    -s) val="$2"; setting=1; shift 2 ;;
    -n) shift ;;
    -t) shift 2 ;;
    *) shift ;;
  esac
done
[ "$chan" = xfce4-desktop ] || exit 1
echo x >> "$store.calls"
if [ "$list" = 1 ]; then
  n=$(wc -l < "$store.lists" 2> /dev/null || echo 0)
  echo x >> "$store.lists"
  # the stock start-up script (/root/.init.sh) sets its wallpaper after a while
  if [ -n "${XFCONF_REVERT_AFTER:-}" ] && [ "$n" -eq "$XFCONF_REVERT_AFTER" ]; then
    sed -i 's|^\(/backdrop/screen0/monitorVirtual-1/workspace0/last-image\)=.*|\1=/usr/share/backgrounds/stock.jpg|' "$store"
  fi
  cut -d= -f1 "$store"
  exit 0
fi
if [ "$setting" = 1 ]; then
  { grep -v "^${prop}=" "$store" || true; echo "${prop}=${val}"; } > "$store.tmp"
  mv "$store.tmp" "$store"
  exit 0
fi
line=$(grep "^${prop}=" "$store") || exit 1
echo "${line#*=}"
"""

PROPS = (
    "/backdrop/screen0/monitorVirtual-1/workspace0/last-image=/usr/share/backgrounds/stock.jpg\n"
    "/backdrop/screen0/monitorrdp0/workspace0/last-image=/usr/share/backgrounds/stock.jpg\n"
    "/backdrop/screen0/monitorVirtual-1/workspace0/color-style=0\n"
)


@pytest.fixture
def xfce(tmp_path: Path) -> dict[str, Any]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "xfconf-query"
    stub.write_text(XFCONF_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    image = tmp_path / "cherrymountain.png"
    image.write_bytes(b"png")
    store = tmp_path / "props"
    store.write_text(PROPS)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "XFCONF_STORE": str(store),
        "CM_WALLPAPER": str(image),
        "CM_WALLPAPER_INTERVAL": "0",
        "CM_WALLPAPER_PASSES": "12",
        "CM_WALLPAPER_STABLE": "3",
    }
    return {"env": env, "store": store, "image": image}


def props(store: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in store.read_text().splitlines() if line)


def test_the_wallpaper_is_set_on_every_monitor(xfce: dict[str, Any]) -> None:
    done = run(WALLPAPER, xfce["env"])
    assert done.returncode == 0, done.stderr
    got = props(xfce["store"])
    for monitor in ("monitorVirtual-1", "monitorrdp0"):
        assert got[f"/backdrop/screen0/{monitor}/workspace0/last-image"] == str(xfce["image"])
        assert got[f"/backdrop/screen0/{monitor}/workspace0/image-style"] == "5"  # zoomed
    assert got["/backdrop/screen0/monitorVirtual-1/workspace0/color-style"] == "0"  # untouched
    assert "set the wallpaper on 2 workspace(s)" in done.stdout


def test_the_wallpaper_wins_over_the_images_own_start_up_script(xfce: dict[str, Any]) -> None:
    """/root/.init.sh sets a stock wallpaper ten seconds after login; the script looks
    again and sets ours once more."""
    env = {**xfce["env"], "XFCONF_REVERT_AFTER": "2", "CM_WALLPAPER_PASSES": "30"}
    done = run(WALLPAPER, env)
    assert done.returncode == 0, done.stderr
    assert props(xfce["store"])["/backdrop/screen0/monitorVirtual-1/workspace0/last-image"] == str(xfce["image"])
    assert done.stdout.count("set the wallpaper") == 2  # the first time, and after the stock one


def test_a_wallpaper_that_is_already_set_ends_the_run_early(xfce: dict[str, Any]) -> None:
    assert run(WALLPAPER, xfce["env"]).returncode == 0
    lists_before = len((xfce["store"].with_name("props.lists")).read_text().split())
    done = run(WALLPAPER, xfce["env"])
    assert done.returncode == 0 and "set the wallpaper" not in done.stdout
    lists_after = len((xfce["store"].with_name("props.lists")).read_text().split())
    assert lists_after - lists_before == 3  # CM_WALLPAPER_STABLE quiet passes, then done


def test_without_the_image_nothing_is_touched(xfce: dict[str, Any]) -> None:
    xfce["image"].unlink()
    before = xfce["store"].read_text()
    done = run(WALLPAPER, xfce["env"])
    assert done.returncode == 0 and "nothing to set" in done.stdout
    assert xfce["store"].read_text() == before


def test_without_a_wallpaper_setting_it_gives_up_quietly(xfce: dict[str, Any]) -> None:
    xfce["store"].write_text("/backdrop/screen0/monitorVirtual-1/workspace0/color-style=0\n")
    done = run(WALLPAPER, xfce["env"])
    assert done.returncode == 0
    assert "never showed a wallpaper setting" in done.stdout
    assert "last-image" not in xfce["store"].read_text()
