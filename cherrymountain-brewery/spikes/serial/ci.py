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
OTSIM_SECONDS: int = int(os.environ.get("SERIAL_OTSIM_SECONDS", "300"))
OTSIM_TARBALL: Path = Path("/phenix/injects/otsim-rtu.tgz")  # built by build_otsim.sh, injected by topology.yml
OTSIM_ENV: str = "export LD_LIBRARY_PATH=/opt/otsim/lib PATH=/opt/otsim/bin:$PATH"


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


def put_config(lab: Lab, vm: str, name: str, dev: str) -> None:
    """Write an ot-sim config to the guest with its serial device set to dev."""
    text: str = (HERE / name).read_text().replace(DEV, dev)
    staged: Path = lab.dir / name
    staged.write_text(text)
    lab.put_file(vm, f"/tmp/{name}", staged, "0644")


def otsim_rtu(lab: Lab, rio: str, chiller: str, dev: str = DEV, monitor: str | None = "bus-monitor") -> None:
    """ot-sim's own Modbus RTU master and server over a serial link, in place of the stdlib stand-ins."""
    lab.fact("otsim_tarball", f"{OTSIM_TARBALL}: {OTSIM_TARBALL.stat().st_size} bytes" if OTSIM_TARBALL.exists() else "missing")
    for vm in (rio, chiller):
        lab.shell(vm, "pkill -f rtu_check.py; sleep 1; pgrep -fc rtu_check.py || true")
        out: str = lab.shell(vm, "ls -l /opt/otsim-rtu.tgz && tar -xzf /opt/otsim-rtu.tgz -C /opt && ls /opt/otsim/bin")
        lab.check(f"the injected ot-sim tarball unpacks on {vm}", "ot-sim-modbus-module" in out, out[:300])
    put_config(lab, chiller, "otsim-chiller.xml", dev)
    put_config(lab, rio, "otsim-rio.xml", dev)
    for vm in (rio, chiller):
        lab.put_file(vm, "/tmp/otsim_check.py", HERE / "otsim_check.py")
    if monitor:
        lab.shell(monitor, "rm -f /tmp/bus2.txt; (setsid nohup timeout 900 tcpdump -ni eth0 -l 'udp port 9000' >/tmp/bus2.txt 2>&1 </dev/null &); sleep 1; echo started")
    for vm, cfg in ((chiller, "otsim-chiller"), (rio, "otsim-rio")):
        out = lab.shell(
            vm,
            f"{OTSIM_ENV}; cd /tmp; setsid nohup ot-sim-cpu-module /tmp/{cfg}.xml >/tmp/otsim.log 2>&1 </dev/null & sleep 12; "
            "pgrep -a ot-sim | cut -c1-120; tail -n 3 /tmp/otsim.log | cut -c1-200",
        )
        lab.fact(f"otsim_start_{vm}", out)
        lab.check(f"ot-sim runs on {vm} (message bus, CPU and Modbus modules)", out.count("ot-sim-") >= 3, out[:300])

    # Values: the master polls the server over RTU; the master's loopback server shows what it read.
    probe: str = lab.shell(rio, "python3 /tmp/otsim_check.py watch --seconds 15")
    lab.fact("otsim_probe", probe.strip())
    # A write on the master's side must arrive at the chiller through the master's RTU write.
    lab.shell(rio, "python3 /tmp/otsim_check.py write 40000 77")
    seen: str = ""
    for _ in range(8):
        time.sleep(2)
        seen = lab.shell(chiller, "python3 /tmp/otsim_check.py read 40000").strip()
        if '"value": 77' in seen:
            break
    lab.check("an ot-sim RTU write from rio-utilities reaches the chiller's holding register", '"value": 77' in seen, seen)

    res = lab.shell_result(rio, f"python3 /tmp/otsim_check.py watch --seconds {OTSIM_SECONDS}", wait_s=OTSIM_SECONDS + 240)
    lab.save("otsim-watch.txt", res.stdout)
    m = re.search(r'"samples": (\d+), "errors": (\d+), "distinct": (\d+)', res.stdout)
    samples, errors, distinct = (int(g) for g in m.groups()) if m else (0, 0, 0)
    lab.check(
        "the chiller's changing value reaches rio-utilities over ot-sim RTU (loopback reads, no errors)",
        bool(m) and errors == 0 and distinct >= min(samples, 100) * 0.8,
        res.stdout.strip()[:300],
    )
    log_errors: str = lab.shell(rio, "grep -c ERROR /tmp/otsim.log; grep -m3 ERROR /tmp/otsim.log | cut -c1-200")
    n_err: int = int(log_errors.split()[0]) if log_errors.split() and log_errors.split()[0].isdigit() else -1
    polls: int = (OTSIM_SECONDS + 40) * 2
    lab.fact("otsim_poll_errors", f"{n_err} ERROR lines in the master's log for about {polls} register polls ({100.0 * max(n_err, 0) / polls:.2f}%)")
    lab.check("ot-sim master poll failures under 1% at 9600 baud, 1 s period", 0 <= n_err and 100.0 * n_err / polls < 1.0, log_errors[:300])
    lab.save("otsim-rio.log", lab.shell(rio, "tail -n 200 /tmp/otsim.log"))
    lab.save("otsim-chiller.log", lab.shell(chiller, "tail -n 200 /tmp/otsim.log"))
    if monitor:
        bus: str = lab.shell(monitor, "pkill tcpdump; sleep 1; grep -c UDP /tmp/bus2.txt; head -3 /tmp/bus2.txt")
        lab.save("bus-monitor-otsim.txt", bus)
        first: str = bus.strip().splitlines()[0] if bus.strip() else "0"
        lab.check("the bus monitor sees the ot-sim RTU frames", first.isdigit() and int(first) > 0, bus[:300])


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
        otsim_rtu(lab, a, b)
        log("done")
    finally:
        lab.collect()
        lab.stop_experiment()
        sh(["cp", "-f", str(HERE / "topology.yml"), str(lab.dir / "topology.yml")])
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
