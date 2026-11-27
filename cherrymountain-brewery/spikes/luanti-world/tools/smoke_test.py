"""Headless smoke test: cm_world on a Luanti server, fed by the spike harness.

No client and no display: the server runs the mod, the mod writes
cm_world_status.json in the world directory after each frame, and this script
checks it. This is what a CI job would run (see the spike README).

  python smoke_test.py --engine local    # Debian's minetestserver 5.6.1
  python smoke_test.py --engine docker   # linuxserver/luanti (Luanti 5.18.0)

Checks:
  1. the static scenes build and frames arrive (seq advances)
  2. the co2-attack scenario reaches CO2 tier 1 in the world (gas layer grows)
  3. a console press (autopress fans-on, the button's own handler) reaches the
     relay, and the purge fans run in a later frame
  4. an action off the allowlist (autopress nope) is refused by the relay
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
MOD = SPIKE / "mod/cm_world"
HARNESS = SPIKE / "harness.py"
HELICS = SPIKE.parents[1] / "plant/generated/full/helics.json"
#: minetest_game from Debian's minetest-data; the 5.18 image ships only devtest.
MTG = Path(os.environ.get("CM_MTG", "/usr/share/games/minetest/games/minetest_game"))
MTSERVER = os.environ.get("CM_MTSERVER", "/usr/games/minetestserver")
VIEW_PORT, RELAY_PORT, GAME_PORT = 8190, 8192, 30010


def status(world: Path) -> dict:
    try:
        return json.loads((world / "cm_world_status.json").read_text())
    except (OSError, ValueError):
        return {}


def wait_for(pred: Callable[[dict], bool], world: Path, timeout: float, what: str) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = status(world)
        if s and pred(s):
            print(f"  ok   {what} after {time.time() - t0:.0f} s (seq {s.get('seq')})")
            return s
        time.sleep(1)
    raise AssertionError(
        f"timed out after {timeout:.0f} s: {what}; last status {json.dumps(status(world))[:400]}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=("local", "docker"), default="local")
    ap.add_argument("--speed", type=float, default=10.0)
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="cm-smoke-"))
    world = tmp / "world"
    (world / "worldmods").mkdir(parents=True)
    shutil.copytree(MOD, world / "worldmods/cm_world")
    (world / "world.mt").write_text(
        "gameid = minetest\nbackend = sqlite3\nplayer_backend = sqlite3\nauth_backend = sqlite3\n"
    )
    (world / "map_meta.txt").write_text("mg_name = singlenode\nseed = 1\n[end_of_params]\n")
    conf = tmp / "minetest.conf"
    conf.write_text(
        "\n".join(
            [
                "secure.http_mods = cm_world",
                "mg_name = singlenode",
                "server_announce = false",
                "enable_ipv6 = false",
                "ipv6_server = false",
                f"cm_world.frame_url = http://127.0.0.1:{VIEW_PORT}/frame.json",
                f"cm_world.control_url = http://127.0.0.1:{RELAY_PORT}/control",
                "cm_world.write_status = true",
                "cm_world.autopress = nope@15,fans-on@60",
                "",
            ]
        )
    )
    procs = []
    harness = subprocess.Popen(
        [
            sys.executable,
            str(HARNESS),
            "--helics",
            str(HELICS),
            "--scenario",
            "co2-attack",
            "--speed",
            str(args.speed),
            "--port",
            str(VIEW_PORT),
            "--control-port",
            str(RELAY_PORT),
        ],
        stdout=open(tmp / "harness.log", "w"),
        stderr=subprocess.STDOUT,
    )
    procs.append(harness)
    if args.engine == "local":
        cmd = [
            MTSERVER,
            "--config",
            str(conf),
            "--world",
            str(world),
            "--gameid",
            "minetest",
            "--port",
            str(GAME_PORT),
            "--logfile",
            str(tmp / "server.log"),
        ]
        name = "minetestserver 5.6.1"
    else:
        subprocess.run(["sudo", "chmod", "-R", "a+rwX", str(tmp)], check=True)
        cmd = [
            "sudo",
            "docker",
            "run",
            "--rm",
            "--name",
            "cm-smoke",
            "--network",
            "host",
            "-v",
            f"{tmp}:/smoke",
            "-v",
            f"{MTG}:/usr/share/luanti/games/minetest:ro",
            "--entrypoint",
            "luantiserver",
            "linuxserver/luanti:latest",
            "--config",
            "/smoke/minetest.conf",
            "--world",
            "/smoke/world",
            "--gameid",
            "minetest",
            "--port",
            str(GAME_PORT),
            "--logfile",
            "/smoke/server.log",
        ]
        name = "linuxserver/luanti (Luanti 5.18.0)"
    print(f"engine: {name}; work dir {tmp}")
    server = subprocess.Popen(cmd, stdout=open(tmp / "server.out", "w"), stderr=subprocess.STDOUT)
    procs.append(server)
    failed = False
    try:
        t0 = time.time()
        s = wait_for(
            lambda s: s.get("seq", 0) > 0, world, 60, "scenes built and first frame applied"
        )
        first = s["seq"]
        wait_for(lambda s: s["seq"] > first + 5, world, 30, "frames keep arriving")
        s = wait_for(
            lambda s: s["cellar"]["tier"] != "ok" and s["cellar"]["layer_blocks"] >= 1,
            world,
            120,
            "co2-attack reaches tier 1 in the world",
        )
        print(
            f"       avg {s['cellar']['avg_ppm']:.0f} ppm,"
            f" layer {s['cellar']['layer_blocks']} blocks, {s['cellar']['gas_cells']} gas cells,"
            f" apply {s['apply_ms']:.1f} ms"
        )
        relay = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{RELAY_PORT}/control", timeout=3).read()
        )
        s = wait_for(
            lambda s: s["cellar"]["fans_running"],
            world,
            120,
            "console press fans-on reaches the plant (fans run)",
        )
        relay = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{RELAY_PORT}/control", timeout=3).read()
        )
        assert [e["action"] for e in relay["log"]] == ["fans-on"], relay
        assert relay["log"][0]["by"] == "autopress", relay
        print("  ok   relay log holds only fans-on, by autopress")
        log = (tmp / "server.log").read_text(errors="replace")
        assert "CONTROL REFUSED: nope" in log, "the refused action was not logged"
        print("  ok   off-list action refused and logged by the mod")
        built = [line for line in log.splitlines() if "static scenes built" in line]
        print(
            f"  info {built[-1].split('cm_world: ')[-1] if built else 'no build line'};"
            f" total {time.time() - t0:.0f} s"
        )
    except AssertionError as exc:
        failed = True
        print(f"  FAIL {exc}")
        print(
            (tmp / "server.log").read_text(errors="replace")[-3000:]
            if (tmp / "server.log").exists()
            else ""
        )
    finally:
        if args.engine == "docker":
            subprocess.run(
                ["sudo", "docker", "rm", "-f", "cm-smoke"], capture_output=True, check=False
            )
        for p in procs:
            p.terminate()
    print("FAILED" if failed else "PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
