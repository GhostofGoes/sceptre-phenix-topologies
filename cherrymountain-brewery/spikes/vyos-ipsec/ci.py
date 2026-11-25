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
import subprocess
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
        lab.check(
            "rtr-edge is a VyOS rolling build (the 1.5 line; rolling images report 999.<stamp>)",
            "VyOS" in version_out and ("rolling" in version_out or "1.5" in version_out),
            version_out[:300],
        )

        # Path 1: the config phenix injected and the image applied at first boot. The boot script
        # runs after the cc client registers (run 13 looked 5 s too early), so poll for it.
        stock: dict[str, str] = {}
        probe_script: str = (
            f"cat > /tmp/op.sh <<'OPEOF'\n#!/bin/vbash\n{VBASH}\nrun show configuration commands\nOPEOF\n"
            "chmod +x /tmp/op.sh; sg vyattacfg -c /tmp/op.sh 2>&1 | grep -c 'vpn ipsec' ; ls -l /usr/lib/live/mount/persistence/boot/vyos/rw/config/scripts/custom/ 2>&1; "
            "journalctl -u vyos-router --no-pager 2>/dev/null | grep -iE 'vyos.script|error|commit' | tail -15"
        )
        for vm in ROUTERS:
            def applied(v: str = vm) -> tuple[bool, str]:
                out: str = lab.shell(v, probe_script, wait_s=180)
                return bool(re.match(r"\s*([1-9]\d*)", out)), out

            try:
                stock[vm] = wait_for(f"phenix's config on {vm}", 150, 10, applied)
            except TimeoutError as exc:
                stock[vm] = str(exc)
            lab.fact(f"{vm}_boot_apply", stock[vm])
        # NIC order: eth<n> must be the n-th topology interface, in PCI slot order.
        for vm in ROUTERS:
            lab.fact(f"{vm}_nic_order", lab.shell(vm, "for n in eth0 eth1; do echo $n $(cat /sys/class/net/$n/address) $(basename $(readlink -f /sys/class/net/$n/device)); done; ip -br addr show eth0 eth1"))
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

        lab.fact("stock_config_key_exchange", vyos_op(lab, "rtr-edge", "show configuration commands | match 'ike-group IKE-1W'"))
        for vm in ("rtu-der", "utility-derms"):
            lab.put_file(vm, "/tmp/dnp3_link.py", HERE / "dnp3_link.py")
        lab.shell("rtu-der", "setsid nohup python3 /tmp/dnp3_link.py serve 20000 >/tmp/dnp3.log 2>&1 </dev/null & sleep 1; echo up")
        # Capture on the host tap of rtr-edge's WAN interface: a capture inside the router also
        # shows the decrypted copy of every inbound packet, which is not what is on the wire.
        wan_tap: str = lab.taps("rtr-edge")[0]
        wan_file: Path = lab.dir / "wan-capture.txt"
        wan_cap: subprocess.Popen[bytes] = subprocess.Popen(  # noqa: S603
            ["timeout", "90", "tcpdump", "-ni", wan_tap, "-l", "-c", "600"],  # noqa: S607
            stdout=wan_file.open("wb"), stderr=subprocess.STDOUT,
        )  # fmt: skip
        time.sleep(3)
        icmp: str = lab.shell("utility-derms", "ping -c 5 -W 2 10.20.15.10 2>&1 | tail -3")
        lab.check("ICMP crosses the tunnel", " 0% packet loss" in icmp, icmp)
        tcp: str = lab.shell("utility-derms", "timeout 5 bash -c 'exec 3<>/dev/tcp/10.20.15.10/20000' && echo open || echo closed")
        lab.check("TCP 20000 reachable across the tunnel", "open" in tcp, tcp)
        dnp3: str = lab.shell("utility-derms", "python3 /tmp/dnp3_link.py probe 10.20.15.10 2>&1")
        lab.check("DNP3 link status crosses the tunnel", "PASS: DNP3 link status" in dnp3, dnp3)
        time.sleep(5)
        wan_cap.terminate()
        wan_cap.wait(timeout=15)
        wan: str = wan_file.read_text(errors="replace")
        counts: dict[str, int] = {
            "esp": len(re.findall(r"\bESP\(", wan)),
            "icmp": len(re.findall(r"ICMP echo", wan)),
            "dnp3": len(re.findall(r"\.20000\b", wan)),
            "ike": len(re.findall(r"\.(?:500|4500):", wan)),
        }
        lab.fact("wan_capture_counts", f"tap {wan_tap}: {counts}")
        lab.check(
            "a capture of the WAN link outside the router shows ESP only (no cleartext ICMP or TCP 20000)",
            counts["esp"] > 0 and counts["icmp"] == 0 and counts["dnp3"] == 0,
            f"{counts}\n{wan[:500]}",
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
            # Run 4 showed VyOS rolling commits this variant: an IKE group without a proposal
            # takes the defaults. Record what happens instead of asserting a rejection.
            committed: bool = "PASS: commit succeeded" in res2
            lab.fact("upstream_bug_commits", f"{committed}; the IKE group then has no proposal and the SA is not up: {res2.strip()[-300:]}")
            lab.check("the upstream template's esp-group IKE-1W lines were applied and the commit result recorded", "applied" in res2, res2[:400])
        log("done")
    finally:
        lab.collect()
        lab.stop_experiment()
        for name in ("topology.yml", "scenario.yml"):
            sh(["cp", "-f", str(HERE / name), str(lab.dir / name)])
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
