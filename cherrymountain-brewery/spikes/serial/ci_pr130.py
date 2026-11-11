#!/usr/bin/env python3
"""CI driver for the serial spike on phenix PR #130 (the QEMU socket serial app).

phenix is built from the fork with `pr130-serial.patch` applied (the PR's src/go/app/serial.go and
util/mm changes at activeshadow/sceptre-phenix `f832ad5`). The scenario's `serial` app gives each VM a
QEMU pci-serial device backed by a chardev socket and starts one socat on the minimega host between the
two sockets. Checks: the guest sees a serial port, raw bytes and ot-sim's Modbus RTU cross it, and what
happens to the link when a VM is redeployed (the PR's open review item: no reconnect).

Run as root on a runner prepared by .github/actions/cm-lab, with socat installed:
  sudo -E /opt/cm-spike-venv/bin/python ci_pr130.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))
sys.path.insert(0, str(HERE))

from vmlab import Lab, log, main_guard, sh, wait_for  # noqa: E402

from ci import otsim_rtu  # noqa: E402

EXP: str = "spike-serial-pr130"
RIO: str = "rio-utilities"
CHILLER: str = "glycol-chiller"


def find_port(lab: Lab, vm: str) -> str:
    """The serial port QEMU's pci-serial device became in the guest: the highest ttyS with a UART."""
    out: str = lab.shell(
        vm,
        "for d in /sys/class/tty/ttyS*; do echo \"$(basename $d) type=$(cat $d/type 2>/dev/null)\"; done; "
        "dmesg 2>/dev/null | grep -i 'ttyS' | tail -n 12",
    )
    lab.save(f"guest-ports-{vm}.txt", out)
    found: list[str] = [m.group(1) for m in re.finditer(r"^(ttyS\d+) type=(\d+)$", out, re.MULTILINE) if int(m.group(2)) != 0]
    found.sort(key=lambda n: int(n[4:]))
    return f"/dev/{found[-1]}" if found else ""


def echo_between(lab: Lab, rx: str, tx: str, dev: str, word: str) -> tuple[bool, str]:
    lab.shell(rx, f"rm -f /tmp/echo.out; (setsid nohup timeout 40 cat {dev} > /tmp/echo.out 2>/dev/null </dev/null &) ; sleep 1; echo started")
    time.sleep(2)
    lab.shell(tx, f"printf '{word}\\n' > {dev}; echo sent")

    def seen() -> tuple[bool, str]:
        got: str = lab.shell(rx, "cat /tmp/echo.out")
        return word in got, got.strip()

    try:
        return True, wait_for(f"{word} on {rx}", 30, 3, seen)
    except TimeoutError as exc:
        return False, str(exc)
    finally:
        lab.shell(rx, "pkill -x cat; sleep 1; echo stopped")


def host_socat(lab: Lab, name: str) -> str:
    out: str = sh(["bash", "-c", "pgrep -a socat | cut -c1-300; echo ---; ls -l /tmp/*_serial_* 2>&1 | head -n 10"]).stdout
    lab.save(name, out)
    return out


def run() -> int:
    lab: Lab = Lab("serial-pr130", EXP)
    lab.versions()
    try:
        lab.create_experiment(HERE / "topology-pr130.yml", HERE / "scenario-pr130.yml")
        lab.start_cli()
        lab.wait_running(RIO, CHILLER)
        lab.wait_cc(RIO, CHILLER)
        started: str = host_socat(lab, "host-socat-after-start.txt")
        lab.fact("host_socat_after_start", started)
        lab.check("one socat runs on the minimega host between the two QEMU sockets", started.count("UNIX-CONNECT") >= 1 and "socat" in started, started[:300])
        qemu: str = sh(["bash", "-c", "pgrep -af qemu-system | grep -oE 'chardev socket[^ ]*|device pci-serial[^ ]*' | head -n 8"]).stdout
        lab.fact("qemu_serial_flags", qemu)
        lab.save("experiment-serial-metadata.txt", lab.phenix("config", "get", f"experiment/{EXP}", "--output", "yaml", check=False))

        ports: dict[str, str] = {vm: find_port(lab, vm) for vm in (RIO, CHILLER)}
        lab.fact("guest_serial_ports", str(ports))
        lab.check("both guests have a serial port beyond the console ports", all(ports.values()) and len(set(ports.values())) == 1, str(ports))
        if not all(ports.values()):
            return lab.finish()
        dev: str = ports[RIO]

        ok1, d1 = echo_between(lab, CHILLER, RIO, dev, "hello-from-rio")
        lab.check("raw echo rio-utilities -> glycol-chiller over the QEMU serial link", ok1, d1)
        ok2, d2 = echo_between(lab, RIO, CHILLER, dev, "hello-from-chiller")
        lab.check("raw echo glycol-chiller -> rio-utilities over the QEMU serial link", ok2, d2)

        otsim_rtu(lab, RIO, CHILLER, dev, monitor=None)

        # The PR's open review item: no reconnect after a VM redeploy. Kill and start the chiller, then try again.
        lab.mm.run(f"vm kill {CHILLER}", EXP)
        time.sleep(5)
        after_kill: str = host_socat(lab, "host-socat-after-kill.txt")
        lab.fact("host_socat_after_vm_kill", after_kill)
        lab.mm.run(f"vm start {CHILLER}", EXP)
        time.sleep(20)
        try:
            wait_for("chiller C2 after restart", 420, 5, lambda: (CHILLER in lab.cc_clients(), str(sorted(lab.cc_clients()))))
            returned: bool = True
        except TimeoutError as exc:
            returned = False
            lab.fact("chiller_c2_after_restart", f"did not register within 420 s: {exc}")
        lab.fact("chiller_vm_state_after_restart", str({k: v.get("state") for k, v in lab.vm_info().items()}))
        qemu_cmd: str = sh(["bash", "-c", "pgrep -af 'qemu.*glycol' | cut -c1-1500"]).stdout
        lab.save("qemu-chiller-after-restart.txt", qemu_cmd)
        after_start: str = host_socat(lab, "host-socat-after-restart.txt")
        lab.fact("host_socat_after_vm_restart", after_start)
        # A fact, not a check: the redeploy is the PR's known gap and is recorded, so the job passes when the link works.
        lab.fact("chiller_c2_after_restart_ok", str(returned))
        if returned:
            port2: str = find_port(lab, CHILLER)
            lab.shell(RIO, "pkill -f ot-sim-; sleep 1; echo stopped")
            ok3, d3 = echo_between(lab, CHILLER, RIO, dev, "after-redeploy") if port2 else (False, "no serial port after restart")
            lab.fact("link_after_redeploy", f"works={ok3}: {d3}")
            lab.fact("review_item_no_reconnect", "link recovered by itself" if ok3 else "link did NOT recover after the VM was killed and started (no reconnect)")
        lab.save("host-socat-logs.txt", sh(["bash", "-c", "tail -n 40 /tmp/*_serial_*.log 2>&1 | cut -c1-250"]).stdout)
        log("done")
    finally:
        lab.collect()
        lab.stop_experiment()
        sh(["cp", "-f", str(HERE / "topology-pr130.yml"), str(lab.dir / "topology.yml")])
        sh(["cp", "-f", str(HERE / "scenario-pr130.yml"), str(lab.dir / "scenario.yml")])
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
