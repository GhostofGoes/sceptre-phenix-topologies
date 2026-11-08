#!/usr/bin/env python3
"""CI driver for the serial spike: phenix serial app (socat over multicast) between real guests.

Run as root on a runner prepared by .github/actions/cm-lab:
  sudo -E /opt/cm-spike-venv/bin/python ci.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))

from vmlab import Lab, log, main_guard, sh, wait_for  # noqa: E402

EXP: str = "spike-serial"
DEV: str = "/dev/ttyS10"
POLLS: int = int(os.environ.get("SERIAL_POLLS", "600"))
INTERVAL: float = float(os.environ.get("SERIAL_INTERVAL", "0.5"))


def link_ready(lab: Lab, vm: str) -> tuple[bool, str]:
    out: str = lab.shell(
        vm,
        f"echo unit=$(systemctl is-active serial-startup.service 2>&1); "
        f"echo socat=$(pgrep -c socat || true); echo link=$(test -L {DEV} && echo yes || echo no)",
    )
    m = re.search(r"socat=(\d+)", out)
    return bool(m) and int(m.group(1)) >= 1 and "link=yes" in out, out.replace("\n", " ")


def echo_check(lab: Lab, rx: str, tx: str, word: str) -> tuple[bool, str]:
    lab.shell(rx, f"rm -f /tmp/echo.out; (setsid nohup timeout 40 cat {DEV} > /tmp/echo.out 2>/dev/null </dev/null &) ; sleep 1; echo started")
    time.sleep(2)
    lab.shell(tx, f"printf '{word}\\n' > {DEV}; echo sent")

    def seen() -> tuple[bool, str]:
        got: str = lab.shell(rx, "cat /tmp/echo.out")
        return word in got, got.strip()

    try:
        return True, wait_for(f"{word} on {rx}", 30, 3, seen)
    except TimeoutError as exc:
        return False, str(exc)


def run() -> int:
    lab: Lab = Lab("serial", EXP)
    lab.versions()
    try:
        lab.create_experiment(HERE / "topology.yml", HERE / "scenario.yml")
        lab.start_cli()
        lab.wait_running("rio-utilities", "glycol-chiller", "bus-monitor")
        lab.wait_cc("rio-utilities", "glycol-chiller", "bus-monitor")
        for vm in ("rio-utilities", "glycol-chiller"):
            lab.fact(f"{vm}_serial_script", lab.shell(vm, "cat /etc/phenix/serial-startup.bash"))
            lab.fact(f"{vm}_ip_addr", lab.shell(vm, "ip -br addr"))

        ready: bool = True
        for vm in ("rio-utilities", "glycol-chiller"):
            try:
                detail: str = wait_for(f"{vm} serial link", 120, 5, lambda v=vm: link_ready(lab, v))
                lab.check(f"pty and socat come up on {vm} at boot, no manual steps", True, detail)
            except TimeoutError as exc:
                ready = False
                lab.check(f"pty and socat come up on {vm} at boot, no manual steps", False, str(exc))
                lab.fact(f"{vm}_socat_log", lab.shell(vm, "cat /tmp/socat.log | tail -30; journalctl -u serial-startup --no-pager | tail -30"))
        if not ready:
            return lab.finish()

        a, b = "rio-utilities", "glycol-chiller"
        ok1, d1 = echo_check(lab, b, a, "hello-from-rio")
        lab.check("raw echo rio-utilities -> glycol-chiller", ok1, d1)
        ok2, d2 = echo_check(lab, a, b, "hello-from-chiller")
        lab.check("raw echo glycol-chiller -> rio-utilities", ok2, d2)
        # A leftover reader would steal the RTU frames from the pty.
        for vm in (a, b):
            lab.shell(vm, "pkill -x cat; sleep 1; pgrep -c -x cat || true")

        for vm in (a, b):
            lab.put_file(vm, "/tmp/rtu_check.py", HERE / "rtu_check.py")
        lab.shell(b, f"setsid nohup python3 /tmp/rtu_check.py server {DEV} >/tmp/rtu_server.log 2>&1 </dev/null & sleep 2; cat /tmp/rtu_server.log")
        # The bus monitor sees every frame (README step 6).
        lab.shell("bus-monitor", "rm -f /tmp/bus.txt; (setsid nohup timeout 600 tcpdump -ni eth0 -l 'udp port 9000' >/tmp/bus.txt 2>&1 </dev/null &); sleep 2; echo started")
        res = lab.shell_result(a, f"python3 /tmp/rtu_check.py client {DEV} --polls {POLLS} --interval {INTERVAL}", wait_s=POLLS * INTERVAL + 240)
        lab.save("rtu-client.txt", res.stdout)
        lab.check("RTU read round trip", "PASS: RTU read" in res.stdout, res.stdout[:300])
        lab.check("RTU write is visible on read-back", "PASS: RTU write visible" in res.stdout, res.stdout[:300])
        m = re.search(r"POLLS: (\d+) interval \S+ failures (\d+) \(([\d.]+)%\)", res.stdout)
        lab.fact("poll_loss", m.group(0) if m else "no POLLS line")
        lab.check("poll failures under 1% at 9600 baud", bool(m) and float(m.group(3)) < 1.0 and "PASS: poll loss" in res.stdout, m.group(0) if m else res.stdout[-300:])
        lab.fact("rtu_server_log", lab.shell(b, "tail -5 /tmp/rtu_server.log"))
        bus: str = lab.shell("bus-monitor", "pkill tcpdump; sleep 1; grep -c 'UDP' /tmp/bus.txt; head -5 /tmp/bus.txt")
        lab.save("bus-monitor.txt", bus)
        first: str = bus.strip().splitlines()[0] if bus.strip() else "0"
        lab.check("a third guest on the VLAN sees the bus frames", first.isdigit() and int(first) > 0, bus[:300])
        lab.fact("ot_sim_rtu", "not tested: no ot-sim image in this workflow; see the README")
        log("done")
    finally:
        lab.collect()
        lab.stop_experiment()
        sh(["cp", "-f", str(HERE / "topology.yml"), str(lab.dir / "topology.yml")])
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
