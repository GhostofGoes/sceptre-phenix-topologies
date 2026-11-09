#!/usr/bin/env python3
"""BBMD check on a Linux router built from network namespaces and nftables.

Needs root, iproute2, nftables and a Python with bacpypes3 and pydantic:
  sudo -E python netns_lab.py

Namespaces: fw (the router, nftables forward chain, policy drop) with a bridge
per subnet, and one namespace per host. This is the stand-in used when no VyOS
image is available; `ci.py` runs the same scenario on a VyOS VM.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))

from fuxa_probe import FuxaProbe  # noqa: E402
from scenario import (  # noqa: E402
    BAS_BBMD_IP,
    CLIENT_IP,
    DEVICE_IP,
    FUXA_IP,
    OPS_BBMD_IP,
    STAGE_BOTH_WAYS,
    STAGE_FULL,
    STAGE_ONE_WAY,
    RuleHits,
    run_scenario,
)
from vmlab import Check, Evidence  # noqa: E402

EVIDENCE: Path = Path(os.environ.get("CM_EVIDENCE_DIR", "evidence")) / "bbmd-netns"
TOOLS: dict[str, Path] = {
    "whois_client.py": HERE / "whois_client.py",
    "bbmd.py": HERE / "bbmd.py",
    "responder.py": HERE.parent / "bacnet" / "responder.py",
    "readprop_raw.py": HERE.parent / "bacnet" / "readprop_raw.py",
}


class Host(BaseModel):
    name: str
    ip: str
    subnet: str  # ops or bas


HOSTS: list[Host] = [
    Host(name="ops-client", ip=CLIENT_IP, subnet="ops"),
    Host(name="ops-bbmd", ip=OPS_BBMD_IP, subnet="ops"),
    Host(name="bas-bbmd", ip=BAS_BBMD_IP, subnet="bas"),
    Host(name="bas-dev", ip=DEVICE_IP, subnet="bas"),
]
GATEWAY: dict[str, str] = {"ops": "10.20.30.1", "bas": "10.20.60.1"}


def sh(argv: list[str], timeout_s: float = 60) -> str:
    proc: subprocess.CompletedProcess[str] = subprocess.run(  # noqa: S603
        argv, capture_output=True, text=True, timeout=timeout_s, check=False
    )
    out: str = proc.stdout + proc.stderr
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} exited {proc.returncode}: {out}")
    return out


def nft_ruleset(stage: str, client: str = CLIENT_IP) -> str:
    """The router's forward chain for an ACL stage (the same intent as the VyOS ruleset in ci.py)."""
    rules: list[str] = ["ct state established,related counter accept comment est"]
    rules.append(f"ip saddr {client} ip daddr {DEVICE_IP} udp dport 47808 counter accept comment client-to-device")
    if stage in (STAGE_ONE_WAY, STAGE_BOTH_WAYS, STAGE_FULL):
        rules.append(f"ip saddr {OPS_BBMD_IP} ip daddr {BAS_BBMD_IP} udp dport 47808 counter accept comment ops-bbmd-to-bas-bbmd")
    if stage in (STAGE_BOTH_WAYS, STAGE_FULL):
        rules.append(f"ip saddr {BAS_BBMD_IP} ip daddr {OPS_BBMD_IP} udp dport 47808 counter accept comment bas-bbmd-to-ops-bbmd")
    if stage == STAGE_FULL:
        rules.append(f"ip saddr {DEVICE_IP} ip daddr {client} udp dport 47808 counter accept comment device-to-client")
    body: str = "\n    ".join(rules)
    return (
        "flush ruleset\n"
        "table inet acl {\n  chain forward {\n    type filter hook forward priority 0; policy drop;\n"
        f"    {body}\n    counter drop comment default-drop\n  }}\n}}\n"
    )


class NetnsRunner:
    """Runs the scenario in network namespaces."""

    py: str = sys.executable

    def __init__(self) -> None:
        self.procs: list[subprocess.Popen[bytes]] = []

    def setup(self) -> None:
        sh(["ip", "netns", "add", "fw"])
        sh(["ip", "netns", "exec", "fw", "sysctl", "-qw", "net.ipv4.ip_forward=1"])
        for subnet in ("ops", "bas"):
            sh(["ip", "netns", "exec", "fw", "ip", "link", "add", f"br-{subnet}", "type", "bridge"])
            sh(["ip", "netns", "exec", "fw", "ip", "addr", "add", f"{GATEWAY[subnet]}/24", "dev", f"br-{subnet}"])
            sh(["ip", "netns", "exec", "fw", "ip", "link", "set", f"br-{subnet}", "up"])
        # The forward chain must filter routed traffic only, not frames the bridges switch.
        for key in ("iptables", "ip6tables", "arptables"):
            subprocess.run(  # noqa: S603
                ["ip", "netns", "exec", "fw", "sysctl", "-qw", f"net.bridge.bridge-nf-call-{key}=0"],  # noqa: S607
                capture_output=True, check=False,
            )  # fmt: skip
        for host in HOSTS:
            sh(["ip", "netns", "add", host.name])
            sh(["ip", "link", "add", f"v-{host.name}", "type", "veth", "peer", "name", "eth0", "netns", host.name])
            sh(["ip", "link", "set", f"v-{host.name}", "netns", "fw"])
            sh(["ip", "netns", "exec", "fw", "ip", "link", "set", f"v-{host.name}", "master", f"br-{host.subnet}", "up"])
            for cmd in (
                f"ip addr add {host.ip}/24 dev eth0",
                "ip link set eth0 up",
                "ip link set lo up",
                f"ip route add default via {GATEWAY[host.subnet]}",
            ):
                sh(["ip", "netns", "exec", host.name, *cmd.split()])

    def teardown(self) -> None:
        self.stop_daemons()
        for name in ["fw", *(h.name for h in HOSTS)]:
            subprocess.run(["ip", "netns", "del", name], capture_output=True, check=False)  # noqa: S603, S607

    def run(self, node: str, script: str, timeout_s: float = 90) -> str:
        proc: subprocess.CompletedProcess[str] = subprocess.run(  # noqa: S603
            ["ip", "netns", "exec", node, "bash", "-c", script],  # noqa: S607
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )  # fmt: skip
        return proc.stdout + proc.stderr

    def start(self, node: str, script: str) -> None:
        self.procs.append(
            subprocess.Popen(["ip", "netns", "exec", node, "bash", "-c", script], stdin=subprocess.DEVNULL)  # noqa: S603, S607
        )

    def tool(self, node: str, name: str) -> str:
        return str(TOOLS[name])

    def apply_acl(self, stage: str, client: str = CLIENT_IP) -> str:
        text: str = nft_ruleset(stage, client)
        proc: subprocess.CompletedProcess[str] = subprocess.run(  # noqa: S603
            ["ip", "netns", "exec", "fw", "nft", "-f", "-"],  # noqa: S607
            input=text, capture_output=True, text=True, check=False,
        )  # fmt: skip
        if proc.returncode != 0:
            raise RuntimeError(f"nft: {proc.stderr}")
        return text

    def acl_counters(self) -> str:
        return sh(["ip", "netns", "exec", "fw", "nft", "list", "ruleset"])

    def rule_hits(self) -> RuleHits:
        text: str = self.acl_counters()
        roles: dict[str, str] = {
            "est": "est", "client-to-device": "client_to_device", "ops-bbmd-to-bas-bbmd": "ops_bbmd_to_bas_bbmd",
            "bas-bbmd-to-ops-bbmd": "bas_bbmd_to_ops_bbmd", "device-to-client": "device_to_client", "default-drop": "default_drop",
        }  # fmt: skip
        packets: dict[str, int] = {}
        for line in text.splitlines():
            m: re.Match[str] | None = re.search(r"counter packets (\d+) bytes \d+.*comment \"?([\w-]+)\"?", line)
            if m and m.group(2) in roles:
                packets[roles[m.group(2)]] = int(m.group(1))
        return RuleHits(packets=packets, raw=text)

    def attach_fuxa(self, probe: FuxaProbe) -> None:
        def attach(host_end: str) -> None:
            sh(["ip", "link", "set", host_end, "netns", "fw"])
            sh(["ip", "netns", "exec", "fw", "ip", "link", "set", host_end, "master", "br-ops", "up"])

        probe.plumb(GATEWAY["ops"], ["10.20.60.0/24"], attach)

    def flush_state(self) -> None:
        # A missing conntrack binary must not pass silently: stale entries make every stage look established.
        sh(["ip", "netns", "exec", "fw", "conntrack", "-F"])

    def stop_daemons(self) -> None:
        for proc in self.procs:
            proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.procs = []


class Recorder:
    def __init__(self) -> None:
        self.evidence: Evidence = Evidence(spike="bbmd-netns")
        EVIDENCE.mkdir(parents=True, exist_ok=True)

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        self.evidence.checks.append(Check(name=name, passed=passed, detail=detail[:2000]))
        print(f"{'PASS' if passed else 'FAIL'}: {name} -- {detail[:300]}", flush=True)
        return passed

    def fact(self, key: str, value: str) -> None:
        self.evidence.facts[key] = value[:4000]
        print(f"fact {key}: {value[:300]}", flush=True)

    def save(self, name: str, content: str) -> Path:
        path: Path = EVIDENCE / name
        path.write_text(content)
        return path


def main() -> int:
    rec: Recorder = Recorder()
    runner: NetnsRunner = NetnsRunner()
    try:
        runner.setup()
        probe: FuxaProbe | None = None if os.environ.get("CM_SKIP_FUXA") else FuxaProbe(EVIDENCE / "fuxa", FUXA_IP)
        try:
            run_scenario(runner, rec, probe)
        finally:
            if probe is not None:
                probe.remove()
        rec.fact("router", "Linux network namespace with nftables (stand-in for VyOS)")
        rec.fact("nft", sh(["nft", "--version"]).strip())
        rec.fact("kernel", sh(["uname", "-r"]).strip())
        rec.fact("bacpypes3", sh([sys.executable, "-m", "pip", "show", "bacpypes3"]).splitlines()[1])
    finally:
        runner.teardown()
        (EVIDENCE / "result.json").write_text(rec.evidence.model_dump_json(indent=2))
        lines: list[str] = [f"### Spike bbmd (netns): {'PASS' if rec.evidence.passed else 'FAIL'}", ""]
        lines += [f"- {'PASS' if c.passed else 'FAIL'}: {c.name}" for c in rec.evidence.checks]
        lines += ["", *(f"- {k}: {v}" for k, v in rec.evidence.facts.items() if k in ("router", "nft", "kernel", "bacpypes3", "one_way_acl_result"))]
        (EVIDENCE / "summary.md").write_text("\n".join(lines) + "\n")
        summary: str | None = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a") as fh:
                fh.write("\n".join(lines) + "\n")
    return 0 if rec.evidence.passed else 1


if __name__ == "__main__":
    sys.exit(main())
