#!/usr/bin/env python3
"""CI driver for the bbmd spike on a VyOS router: two phenix subnets, BBMDs, device 3003.

Run as root on a runner prepared by .github/actions/cm-lab:
  sudo -E /opt/cm-spike-venv/bin/python ci.py

The router's ACL is a VyOS 1.5 `firewall ipv4 forward filter` ruleset (default
drop, stateful, UDP 47808 rules per stage) pushed through cc after boot.
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

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
from vmlab import Lab, log, main_guard, sh  # noqa: E402

EXP: str = "spike-bbmd"
HOSTS: tuple[str, ...] = ("ops-client", "ops-bbmd", "bas-bbmd", "bas-dev")
VBASH: str = "source /opt/vyatta/etc/functions/script-template"
TOOLS: dict[str, Path] = {
    "whois_client.py": HERE / "whois_client.py",
    "bbmd.py": HERE / "bbmd.py",
    "responder.py": HERE.parent / "bacnet" / "responder.py",
    "readprop_raw.py": HERE.parent / "bacnet" / "readprop_raw.py",
}


# VyOS rule number to the role the scenario knows it by.
RULE_ROLES: dict[str, str] = {
    "10": "est", "20": "client_to_device", "30": "ops_bbmd_to_bas_bbmd", "40": "bas_bbmd_to_ops_bbmd",
    "50": "device_to_client", "default": "default_drop",
}  # fmt: skip


def acl_commands(stage: str, client: str = CLIENT_IP) -> list[str]:
    """VyOS 1.5 set commands for an ACL stage (the same intent as netns_lab.nft_ruleset)."""
    base: str = "set firewall ipv4 forward filter"

    def rule(num: int, src: str, dst: str) -> list[str]:
        return [
            f"{base} rule {num} action accept",
            f"{base} rule {num} protocol udp",
            f"{base} rule {num} source address {src}",
            f"{base} rule {num} destination address {dst}",
            f"{base} rule {num} destination port 47808",
        ]

    cmds: list[str] = [
        "delete firewall ipv4 forward",
        f"{base} default-action drop",
        f"{base} rule 10 action accept",
        f"{base} rule 10 state established",
        f"{base} rule 10 state related",
        *rule(20, client, DEVICE_IP),
    ]
    if stage in (STAGE_ONE_WAY, STAGE_BOTH_WAYS, STAGE_FULL):
        cmds += rule(30, OPS_BBMD_IP, BAS_BBMD_IP)
    if stage in (STAGE_BOTH_WAYS, STAGE_FULL):
        cmds += rule(40, BAS_BBMD_IP, OPS_BBMD_IP)
    if stage == STAGE_FULL:
        cmds += rule(50, DEVICE_IP, client)
    return cmds


class VmRunner:
    """Runs the scenario in minimega guests; the router is the VyOS VM fw-ot."""

    py: str = "/opt/bacnet/bin/python"

    def __init__(self, lab: Lab) -> None:
        self.lab: Lab = lab
        self.counter: int = 0
        self.installed: set[tuple[str, str]] = set()

    def run(self, node: str, script: str, timeout_s: float = 90) -> str:
        return self.lab.shell(node, script, wait_s=timeout_s + 60)

    def start(self, node: str, script: str) -> None:
        self.counter += 1
        path: str = f"/tmp/daemon{self.counter}.sh"
        enc: str = base64.b64encode(script.encode()).decode()
        self.lab.shell(node, f"echo {enc} | base64 -d > {path}; (setsid nohup bash {path} </dev/null >/dev/null 2>&1 &); sleep 1; echo started")

    def tool(self, node: str, name: str) -> str:
        if (node, name) not in self.installed:
            self.lab.put_file(node, f"/tmp/{name}", TOOLS[name])
            self.installed.add((node, name))
        return f"/tmp/{name}"

    def vyos(self, commands: list[str], commit: bool = True) -> str:
        body: str = "\n".join(commands)
        tail: str = "commit\nsave\n" if commit else ""
        script: str = (
            f"cat > /tmp/acl.sh <<'ACLEOF'\n#!/bin/vbash\n{VBASH}\nconfigure\n{body}\n{tail}exit\nACLEOF\n"
            "chmod +x /tmp/acl.sh; sg vyattacfg -c /tmp/acl.sh 2>&1; echo exit=$?"
        )
        return self.lab.shell("fw-ot", script, wait_s=240)

    def apply_acl(self, stage: str, client: str = CLIENT_IP) -> str:
        cmds: list[str] = acl_commands(stage, client)
        out: str = self.vyos(cmds)
        if re.search(r"commit failed|invalid|not valid|error|set failed|configuration path", out, re.IGNORECASE):
            raise RuntimeError(f"VyOS rejected the {stage} ACL:\n{out}")
        return "\n".join(cmds) + "\n\n" + out

    def acl_counters(self) -> str:
        script: str = (
            f"cat > /tmp/op.sh <<'OPEOF'\n#!/bin/vbash\n{VBASH}\nrun show firewall ipv4 forward filter\nrun show firewall statistics 2>&1\nOPEOF\n"
            "chmod +x /tmp/op.sh; sg vyattacfg -c /tmp/op.sh 2>&1"
        )
        return self.lab.shell("fw-ot", script, wait_s=180)

    def rule_hits(self) -> RuleHits:
        """Parse the rule table of `show firewall ipv4 forward filter` (rule, action, protocol, packets, bytes)."""
        text: str = self.acl_counters()
        packets: dict[str, int] = {}
        for line in text.splitlines():
            m: re.Match[str] | None = re.match(r"\s*(\d+|default)\s+(?:accept|drop|reject)\s+\S+\s+(\d+)\s+(\d+)\b", line)
            if m and m.group(1) in RULE_ROLES:
                packets[RULE_ROLES[m.group(1)]] = int(m.group(2))
        return RuleHits(packets=packets, raw=text)

    def attach_fuxa(self, probe: FuxaProbe) -> None:
        """Wire the container's veth to the OVS bridge of the OPS VLAN, on the tag ops-client's tap uses."""

        def attach(host_end: str) -> None:
            tap: str = self.lab.taps("ops-client")[0]
            bridge: str = sh(["ovs-vsctl", "port-to-br", tap], check=True).stdout.strip()
            tag: str = sh(["ovs-vsctl", "get", "port", tap, "tag"], check=True).stdout.strip()
            sh(["ovs-vsctl", "add-port", bridge, host_end, f"tag={tag}"], check=True)
            sh(["ip", "link", "set", host_end, "up"], check=True)
            self.lab.fact("fuxa_attachment", f"{host_end} on OVS bridge {bridge}, tag {tag} (like {tap} of ops-client)")

        probe.plumb("10.20.30.1", ["10.20.60.0/24"], attach)

    def flush_state(self) -> None:
        self.lab.shell("fw-ot", "conntrack -F 2>&1 || true; echo flushed")

    def stop_daemons(self) -> None:
        for node in HOSTS:
            self.lab.shell(node, "pkill -f /opt/bacnet/bin/python || true; echo stopped")


def run() -> int:
    lab: Lab = Lab("bbmd-vyos", EXP)
    lab.versions()
    try:
        lab.create_experiment(HERE / "topology.yml")
        lab.start_cli()
        lab.wait_running("fw-ot", *HOSTS, timeout_s=180)
        lab.wait_cc_vm("fw-ot", timeout_s=900)
        lab.wait_cc(*HOSTS, timeout_s=600)
        runner: VmRunner = VmRunner(lab)
        version: str = lab.shell(
            "fw-ot",
            f"cat > /tmp/op.sh <<'OPEOF'\n#!/bin/vbash\n{VBASH}\nrun show version\nOPEOF\nchmod +x /tmp/op.sh; sg vyattacfg -c /tmp/op.sh 2>&1",
        )
        lab.fact("router", f"VyOS VM fw-ot: {version.strip()[:300]}")
        lab.fact("router_interfaces", lab.shell("fw-ot", "ip -br addr"))
        lab.check(
            "fw-ot is a VyOS rolling build (the 1.5 line; rolling images report 999.<stamp>)",
            "VyOS" in version and ("rolling" in version or "1.5" in version),
            version[:200],
        )
        lab.fact("bacpypes3_in_guest", lab.shell("bas-dev", "/opt/bacnet/bin/pip show bacpypes3 | head -2"))
        probe: FuxaProbe = FuxaProbe(lab.dir / "fuxa", FUXA_IP)
        try:
            run_scenario(runner, lab, probe)
        finally:
            probe.remove()
        runner.stop_daemons()
    finally:
        lab.collect()
        lab.stop_experiment()
        sh(["cp", "-f", str(HERE / "topology.yml"), str(lab.dir / "topology.yml")])
    log("done")
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
