#!/usr/bin/env python3
"""The BBMD check, independent of what carries it (namespaces or VMs).

A client on the OPS subnet broadcasts a Who-Is the way FUXA's node-bacnet 0.2.4
does (to 255.255.255.255). Device 3003 sits on the BAS subnet behind a router.
Without BBMDs the broadcast stops at the router. With a BBMD on each side, each
with a BDT that lists the other, the broadcast is forwarded as a unicast
Forwarded-NPDU between the BBMDs and rebroadcast on the far subnet, so the
client sees the I-Am and can ReadProperty through the router's stateful ACL.

A runner supplies the nodes and the ACL; this module runs the steps and records
the results the same way for both carriers.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

HERE: Path = Path(__file__).resolve().parent
SPIKES: Path = HERE.parent

CLIENT_IP: str = "10.20.30.30"
OPS_BBMD_IP: str = "10.20.30.5"
BAS_BBMD_IP: str = "10.20.60.5"
DEVICE_IP: str = "10.20.60.13"
INSTANCE: int = 3003
OBJECT_NAME: str = "bas-vent"
OPS_BBMD_PREFIX: str = "0"
BDT: str = f"{OPS_BBMD_IP}:47808 {BAS_BBMD_IP}:47808"

# ACL stages, applied in this order.
STAGE_UNICAST: str = "unicast-only"
STAGE_ONE_WAY: str = "bbmd-one-way"
STAGE_BOTH_WAYS: str = "bbmd-both-ways"
STAGE_FULL: str = "bbmd-both-ways-device-replies"


class Runner(Protocol):
    """What the scenario needs from a carrier."""

    py: str  # interpreter with BACpypes3 on the nodes

    def run(self, node: str, script: str, timeout_s: float = 90) -> str: ...

    def start(self, node: str, script: str) -> None: ...

    def tool(self, node: str, name: str) -> str:
        """Path on node of a script from this directory (or ../bacnet)."""

    def apply_acl(self, stage: str) -> str:
        """Install the router ACL for stage; return the ruleset text."""

    def acl_counters(self) -> str:
        """The router's rule counters, as text."""

    def flush_state(self) -> None:
        """Forget the router's connection tracking."""

    def stop_daemons(self) -> None: ...


class WhoIs(BaseModel):
    """Parsed output of whois_client.py."""

    found: bool = False
    addresses: list[str] = []
    via: list[str] = []
    object_name: str = ""
    raw: str = ""


def whois(runner: Runner, bind: str = CLIENT_IP, wait_s: float = 6.0) -> WhoIs:
    """Run the node-bacnet-like client. Bound to its own address (what FUXA does when its device
    address is an interface address), so the broadcast leaves by the subnet interface; bound to
    0.0.0.0 it would follow the default route."""
    out: str = runner.run(
        "ops-client",
        f"{runner.py} {runner.tool('ops-client', 'whois_client.py')} --wait {wait_s} --bind {bind} 2>&1",
        timeout_s=60,
    )
    lines: list[str] = [ln for ln in out.splitlines() if ln.startswith("{")]
    if not lines:
        return WhoIs(raw=out[-600:])
    data: dict = json.loads(lines[-1])
    iams: list[dict] = [i for i in data["iams"] if i["instance"] == INSTANCE]
    read: dict = data.get("read") or {}
    return WhoIs(
        found=bool(iams),
        addresses=[i["address"] for i in iams],
        via=[i["via"] for i in iams],
        object_name=read.get("object_name", ""),
        raw=lines[-1],
    )


def direct_read(runner: Runner) -> str:
    """ReadProperty straight to the device, no Who-Is (what a unicast-configured client does)."""
    return runner.run("ops-client", f"{runner.py} {runner.tool('ops-client', 'readprop_raw.py')} {DEVICE_IP} 2>&1", timeout_s=30)


def start_device(runner: Runner) -> None:
    runner.start(
        "bas-dev",
        f"exec {runner.py} {runner.tool('bas-dev', 'responder.py')} --name {OBJECT_NAME} --instance {INSTANCE} "
        f"--address {DEVICE_IP}/24:47808 >/tmp/responder.log 2>&1",
    )


def start_bbmds(runner: Runner) -> None:
    # A node-bacnet client broadcasts to 255.255.255.255. A BACpypes3 node
    # listens for broadcasts on its subnet's directed broadcast address only,
    # so the OPS BBMD needs a /0 prefix: its "subnet broadcast" is then
    # 255.255.255.255 and it also hears (and sends) limited broadcasts.
    for node, name, instance, own, prefix in (
        ("ops-bbmd", "bbmd-ops", 9001, OPS_BBMD_IP, OPS_BBMD_PREFIX),
        ("bas-bbmd", "bbmd-bas", 9002, BAS_BBMD_IP, "24"),
    ):
        runner.start(
            node,
            f"exec {runner.py} {runner.tool(node, 'bbmd.py')} --name {name} --instance {instance} "
            f"--address {own}/{prefix}:47808 --bbmd {BDT} >/tmp/bbmd.log 2>&1",
        )


class Recorder(Protocol):
    def check(self, name: str, passed: bool, detail: str = "") -> bool: ...

    def fact(self, key: str, value: str) -> None: ...

    def save(self, name: str, content: str) -> Path: ...


def run_scenario(runner: Runner, rec: Recorder, settle: Callable[[], None] = lambda: time.sleep(4)) -> None:
    start_device(runner)
    settle()

    # 1. The problem: a limited broadcast does not cross the router.
    rec.save("acl-unicast-only.txt", runner.apply_acl(STAGE_UNICAST))
    direct: str = direct_read(runner)
    rec.check(
        "unicast ReadProperty to device 3003 crosses the stateful ACL (client rule + replies)",
        f"'{OBJECT_NAME}'" in direct,
        direct.strip()[-300:],
    )
    plain: WhoIs = whois(runner)
    rec.check("without BBMDs a Who-Is broadcast from OPS does not find device 3003", not plain.found, plain.raw or "no output")

    # 2. BBMDs on each side; the ACL lets only the OPS BBMD open flows to the BAS BBMD.
    start_bbmds(runner)
    settle()
    rec.save("acl-bbmd-one-way.txt", runner.apply_acl(STAGE_ONE_WAY))
    runner.flush_state()
    one_way: WhoIs = whois(runner)
    rec.fact("one_way_acl_result", f"found={one_way.found} via={one_way.via} name={one_way.object_name!r} raw={one_way.raw}")
    rec.save("acl-counters-one-way.txt", runner.acl_counters())

    # 3. BBMD flows in both directions. bacpypes3 answers a Who-Is with a unicast I-Am to the
    #    requester, so the reply is a new BAS to OPS flow that no BBMD rule covers.
    rec.save("acl-bbmd-both-ways.txt", runner.apply_acl(STAGE_BOTH_WAYS))
    runner.flush_state()
    both: WhoIs = whois(runner)
    rec.fact("both_ways_acl_result", f"found={both.found} via={both.via} name={both.object_name!r} raw={both.raw}")
    rec.save("acl-counters-both-ways.txt", runner.acl_counters())

    # 4. Plus the device's own reply: UDP 47808 from the BAS device to the client.
    rec.save("acl-bbmd-full.txt", runner.apply_acl(STAGE_FULL))
    runner.flush_state()
    full: WhoIs = whois(runner)
    rec.check(
        "with a BBMD on each side, a Who-Is broadcast from OPS finds device 3003 across the router",
        full.found and DEVICE_IP in full.addresses,
        full.raw or "no output",
    )
    rec.check(
        "ReadProperty on the discovered device works through the router",
        full.object_name == OBJECT_NAME,
        f"object-name {full.object_name!r}",
    )
    rec.fact("full_acl_via", str(full.via))
    rec.save("acl-counters-full.txt", runner.acl_counters())
    unbound: WhoIs = whois(runner, bind="0.0.0.0")  # noqa: S104
    rec.fact("client_bound_0000_result", f"found={unbound.found} raw={unbound.raw}")

    # 4. The BBMD rule is what makes discovery work: remove it and discovery fails again.
    rec.save("acl-unicast-only-again.txt", runner.apply_acl(STAGE_UNICAST))
    runner.flush_state()
    again: WhoIs = whois(runner)
    rec.check("removing the BBMD-to-BBMD rule breaks discovery again", not again.found, again.raw or "no output")
    rec.fact("bbmd_logs", runner.run("ops-bbmd", "tail -5 /tmp/bbmd.log; echo ---; tail -5 /tmp/responder.log 2>/dev/null || true", timeout_s=20))
