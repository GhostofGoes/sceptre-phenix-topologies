#!/usr/bin/env python3
"""CI driver for the vyos-ipsec spike: phenix vrouter IPsec on two real VyOS routers.

Run as root on a runner prepared by .github/actions/cm-lab:
  sudo -E /opt/cm-spike-venv/bin/python ci.py

Paths tested, in order:
  1. phenix's own rendered script, applied at first boot by the image
     (stock path; the pinned phenix already has the ike-group fix);
  2. when 1 did not commit, commit_check.sh on the rendered script (first
     failing command) and the manual known-good set;
  3. tunnel, ICMP, DNP3 link status and ESP-only on the WAN link;
  4. a reproduction of the upstream template bug (esp-group IKE-1W proposal
     lines) committed on rtr-util, to confirm it is rejected.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))

from vmlab import Lab, log, main_guard, sh, wait_for  # noqa: E402

EXP: str = "spike-ipsec"
BOOT_DIR: Path = Path("/phenix/experiments") / EXP / "vrouter"
ROUTERS: tuple[str, str] = ("rtr-edge", "rtr-util")
VBASH: str = "source /opt/vyatta/etc/functions/script-template"


def vyos_op(lab: Lab, vm: str, command: str) -> str:
    """Run a VyOS operational command in a router."""
    script: str = f"cat > /tmp/op.sh <<'OPEOF'\n#!/bin/vbash\n{VBASH}\nrun {command}\nOPEOF\nchmod +x /tmp/op.sh; sg vyattacfg -c /tmp/op.sh 2>&1"
    return lab.shell(vm, script, wait_s=180)


def sa_up(lab: Lab, vm: str) -> tuple[bool, str]:
    out: str = vyos_op(lab, vm, "show vpn ipsec sa")
    return bool(re.search(r"\bup\b", out)) and "203.0.113" in out, out


def run() -> int:
    lab: Lab = Lab("vyos-ipsec", EXP)
    lab.versions()
    try:
        lab.create_experiment(HERE / "topology.yml", HERE / "scenario.yml")
        start_out: str = lab.start_cli()
        lab.wait_running(*ROUTERS, "rtu-der", "utility-derms", timeout_s=180)
        for vm in ROUTERS:
            lab.wait_cc_vm(vm, timeout_s=900)
        lab.wait_cc("rtu-der", "utility-derms", timeout_s=600)

        # Rendered config and VyOS version.
        for vm in ROUTERS:
            boot: Path = BOOT_DIR / f"{vm}.boot"
            lab.check(f"phenix rendered {vm}.boot", boot.exists(), str(boot))
            if boot.exists():
                text: str = boot.read_text()
                lab.save(f"{vm}.boot", re.sub(r"secret '[^']+'", "secret 'REDACTED'", text))
        version_out: str = vyos_op(lab, "rtr-edge", "show version")
        lab.fact("rtr-edge show version", version_out)
        lab.fact("rtr-util show version", vyos_op(lab, "rtr-util", "show version"))
        lab.check("rtr-edge is VyOS 1.5", bool(re.search(r"Version:\s+VyOS\s+1\.5", version_out)) or "1.5" in version_out, version_out[:300])

        # Path 1: the config phenix injected and the image applied at first boot.
        stock: dict[str, str] = {}
        for vm in ROUTERS:
            stock[vm] = lab.shell(
                vm,
                f"cat > /tmp/op.sh <<'OPEOF'\n#!/bin/vbash\n{VBASH}\nrun show configuration commands\nOPEOF\n"
                "chmod +x /tmp/op.sh; sg vyattacfg -c /tmp/op.sh 2>&1 | grep -c 'vpn ipsec' ; ls -l /usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/ 2>&1; "
                "journalctl -u vyos-router --no-pager 2>/dev/null | grep -iE 'vyos.script|error|commit' | tail -15",
                wait_s=180,
            )
            lab.fact(f"{vm}_boot_apply", stock[vm])
        stock_ok: bool = all(re.match(r"\s*([1-9]\d*)", stock[vm]) for vm in ROUTERS)
        lab.check("phenix's rendered IPsec config is in the running config of both routers (committed at boot)", stock_ok, str(stock))

        if not stock_ok:
            # Path 2: find the first failing command, then try the manual set.
            lab.put_file("rtr-edge", "/tmp/commit_check.sh", HERE / "commit_check.sh")
            lab.put_file("rtr-edge", "/tmp/generated.sh", BOOT_DIR / "rtr-edge.boot")
            res: str = lab.shell("rtr-edge", "sg vyattacfg -c 'vbash /tmp/commit_check.sh /tmp/generated.sh' 2>&1 | tail -40", wait_s=240)
            lab.fact("commit_check_generated", res)
            psk: str = "ci-test-psk-0123456789abcdef0123456789"
            for vm, local, peer, ln, rn in (
                ("rtr-edge", "203.0.113.2", "203.0.113.3", "10.20.15.0/24", "192.0.2.0/24"),
                ("rtr-util", "203.0.113.3", "203.0.113.2", "192.0.2.0/24", "10.20.15.0/24"),
            ):
                lab.put_file(vm, "/tmp/commit_check.sh", HERE / "commit_check.sh")
                out: str = lab.shell(vm, f"sg vyattacfg -c 'vbash /tmp/commit_check.sh --manual {local} {peer} {ln} {rn} {psk}' 2>&1 | tail -40", wait_s=240)
                lab.fact(f"commit_check_manual_{vm}", out)
            lab.check("the manual known-good set commits on both routers", all("PASS: commit succeeded" in lab.evidence.facts.get(f"commit_check_manual_{v}", "") for v in ROUTERS))

        # Path 3: SA, ICMP, DNP3, ESP only.
        sa_detail: dict[str, str] = {}
        for vm in ROUTERS:
            try:
                # Traffic nudges the tunnel up if connection-type initiate has not yet.
                lab.shell("utility-derms", "ping -c 3 -W 2 10.20.15.10 >/dev/null 2>&1; echo nudged")
                sa_detail[vm] = wait_for(f"SA on {vm}", 120, 5, lambda v=vm: sa_up(lab, v))
                lab.check(f"show vpn ipsec sa shows the tunnel up on {vm}", True, sa_detail[vm][:600])
            except TimeoutError as exc:
                lab.check(f"show vpn ipsec sa shows the tunnel up on {vm}", False, str(exc)[:900])
                lab.fact(f"{vm}_ipsec_diag", vyos_op(lab, vm, "show vpn ipsec connections") + vyos_op(lab, vm, "show log ipsec | tail -40"))

        for vm in ("rtu-der", "utility-derms"):
            lab.put_file(vm, "/tmp/dnp3_link.py", HERE / "dnp3_link.py")
        lab.shell("rtu-der", "setsid nohup python3 /tmp/dnp3_link.py serve 20000 >/tmp/dnp3.log 2>&1 </dev/null & sleep 1; echo up")
        lab.shell("rtr-edge", "rm -f /tmp/wan.txt; (setsid nohup timeout 45 tcpdump -ni eth0 -l -c 400 >/tmp/wan.txt 2>&1 </dev/null &); sleep 2; echo started")
        icmp: str = lab.shell("utility-derms", "ping -c 5 -W 2 10.20.15.10 2>&1 | tail -3")
        lab.check("ICMP crosses the tunnel", " 0% packet loss" in icmp, icmp)
        tcp: str = lab.shell("utility-derms", "timeout 5 bash -c 'exec 3<>/dev/tcp/10.20.15.10/20000' && echo open || echo closed")
        lab.check("TCP 20000 reachable across the tunnel", "open" in tcp, tcp)
        dnp3: str = lab.shell("utility-derms", "python3 /tmp/dnp3_link.py probe 10.20.15.10 2>&1")
        lab.check("DNP3 link status crosses the tunnel", "PASS: DNP3 link status" in dnp3, dnp3)
        time.sleep(5)
        wan: str = lab.shell("rtr-edge", "pkill tcpdump; sleep 1; cp /tmp/wan.txt /tmp/wan.cp; echo esp=$(grep -c ESP /tmp/wan.cp) icmp=$(grep -cE 'ICMP echo' /tmp/wan.cp) dnp3=$(grep -c '\\.20000' /tmp/wan.cp); head -12 /tmp/wan.cp")
        lab.save("wan-capture.txt", wan)
        m = re.search(r"esp=(\d+) icmp=(\d+) dnp3=(\d+)", wan)
        lab.check(
            "tcpdump on the WAN link shows ESP only (no cleartext ICMP or TCP 20000)",
            bool(m) and int(m.group(1)) > 0 and int(m.group(2)) == 0 and int(m.group(3)) == 0,
            wan[:500],
        )

        # Path 4: reproduce the upstream template bug on rtr-util and see it rejected.
        generated: Path = BOOT_DIR / "rtr-util.boot"
        if generated.exists():
            lines: list[str] = [ln for ln in generated.read_text().splitlines() if ln.startswith("set vpn ipsec")]
            bugged: str = "\n".join(["delete vpn ipsec", *[ln.replace("ike-group IKE-1W proposal", "esp-group IKE-1W proposal") for ln in lines]]) + "\n"
            local_bug: Path = lab.save("upstream-bug-rtr-util.sh", bugged)
            lab.put_file("rtr-util", "/tmp/commit_check.sh", HERE / "commit_check.sh")
            lab.put_file("rtr-util", "/tmp/bug.sh", local_bug)
            res2: str = lab.shell("rtr-util", "sg vyattacfg -c 'vbash /tmp/commit_check.sh /tmp/bug.sh' 2>&1 | tail -30", wait_s=240)
            lab.fact("upstream_bug_commit_check", res2)
            lab.check("the upstream template's esp-group IKE-1W proposal lines are rejected on VyOS 1.5", "PASS: commit succeeded" not in res2, res2[:600])
        log("done")
    finally:
        lab.collect()
        lab.stop_experiment()
        for name in ("topology.yml", "scenario.yml"):
            sh(["cp", "-f", str(HERE / name), str(lab.dir / name)])
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
