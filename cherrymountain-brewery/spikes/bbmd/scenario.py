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
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from fuxa_probe import FuxaProbe, TagReading
from pydantic import BaseModel

HERE: Path = Path(__file__).resolve().parent
SPIKES: Path = HERE.parent

CLIENT_IP: str = "10.20.30.30"
FUXA_IP: str = "10.20.30.10"
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

    def apply_acl(self, stage: str, client: str = CLIENT_IP) -> str:
        """Install the router ACL for stage (client is the OPS host the rules name); return the ruleset text."""

    def acl_counters(self) -> str:
        """The router's rule counters, as text."""

    def rule_hits(self) -> RuleHits:
        """The router's per-rule packet counters since the last apply_acl."""

    def attach_fuxa(self, probe: FuxaProbe) -> None:
        """Put the FUXA container on the OPS subnet (address FUXA_IP, router as gateway)."""

    def flush_state(self) -> None:
        """Forget the router's connection tracking."""

    def stop_daemons(self) -> None: ...


class RuleHits(BaseModel):
    """Packets matched per ACL rule, keyed by the role of the rule."""

    packets: dict[str, int] = {}
    raw: str = ""

    @property
    def parsed(self) -> bool:
        return bool(self.packets)

    def hit(self, role: str) -> int:
        return self.packets.get(role, 0)


class WhoIs(BaseModel):
    """Parsed output of whois_client.py."""

    found: bool = False
    addresses: list[str] = []
    via: list[str] = []
    object_name: str = ""
    raw: str = ""


def whois(runner: Runner, bind: str = CLIENT_IP, wait_s: float = 6.0, listen_only: bool = False) -> WhoIs:
    """Run the node-bacnet-like client. Bound to its own address (what FUXA does when its device
    address is an interface address), so the broadcast leaves by the subnet interface; bound to
    0.0.0.0 it would follow the default route."""
    out: str = runner.run(
        "ops-client",
        f"{runner.py} {runner.tool('ops-client', 'whois_client.py')} --wait {wait_s} --bind {bind}{' --listen-only' if listen_only else ''} 2>&1",
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


def start_device(runner: Runner, broadcast_iam: bool = False) -> None:
    flag: str = " --broadcast-iam" if broadcast_iam else ""
    runner.start(
        "bas-dev",
        f"exec {runner.py} {runner.tool('bas-dev', 'responder.py')} --name {OBJECT_NAME} --instance {INSTANCE} "
        f"--address {DEVICE_IP}/24:47808{flag} >/tmp/responder.log 2>&1",
    )


def stop_device(runner: Runner) -> None:
    runner.run("bas-dev", "pkill -f 'responde[r].py'; sleep 1; echo stopped", timeout_s=30)


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


def run_scenario(
    runner: Runner,
    rec: Recorder,
    probe: FuxaProbe | None = None,
    settle: Callable[[], None] = lambda: time.sleep(4),
) -> None:
    run_stand_in(runner, rec, settle)
    run_broadcast_iam(runner, rec, settle)
    if probe is not None:
        run_fuxa(runner, rec, probe, settle)


def run_stand_in(runner: Runner, rec: Recorder, settle: Callable[[], None]) -> None:
    """Steps 1-5: the node-bacnet look-alike client and a device that answers with a unicast I-Am."""
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


def run_broadcast_iam(runner: Runner, rec: Recorder, settle: Callable[[], None]) -> None:
    """A device that broadcasts its I-Am: is the BAS BBMD to OPS BBMD rule (rule 3) needed, and is it hit?

    The BBMDs are still running. A broadcast I-Am reaches only the device's own subnet and its BBMD, which
    forwards it to the OPS BBMD; the OPS BBMD rebroadcasts it for the client. Two cases:
    - answering a Who-Is: the Who-Is has just opened the OPS BBMD to BAS BBMD flow (UDP 47808 to 47808), so
      the forwarded I-Am is that flow's reply direction and the established rule passes it;
    - an unsolicited I-Am (power-up, periodic announce) with no recent flow: a new BAS-to-OPS flow, which
      needs rule 3.
    The stand-in client binds 0.0.0.0, because a socket bound to one address does not receive broadcasts.
    """
    stop_device(runner)
    start_device(runner, broadcast_iam=True)
    settle()

    # Case 1: answering a Who-Is. Only the OPS BBMD may open flows to the BAS BBMD (no rule 3, no device rule).
    rec.save("acl-bcast-iam-one-way.txt", runner.apply_acl(STAGE_ONE_WAY))
    runner.flush_state()
    answered: WhoIs = whois(runner, bind="0.0.0.0")  # noqa: S104
    hits: RuleHits = runner.rule_hits()
    rec.save("acl-counters-bcast-iam-answer-without-rule3.txt", runner.acl_counters())
    rec.fact(
        "bcast_iam_answer_without_rule3",
        f"found={answered.found} via={answered.via} name={answered.object_name!r} hits={hits.packets}",
    )
    rec.check(
        "broadcast I-Am answering a Who-Is: the OPS BBMD to BAS BBMD rule alone is enough (the reply direction is established)",
        answered.found and answered.via == [OPS_BBMD_IP] and answered.object_name == OBJECT_NAME,
        answered.raw or "no output",
    )
    own: WhoIs = whois(runner, bind=CLIENT_IP)
    rec.fact("bcast_iam_client_bound_to_own_address", f"found={own.found} via={own.via} raw={own.raw}")

    # Case 2: unsolicited broadcasts (an announce every 3 s), no flow open: conntrack is flushed before listening.
    stop_device(runner)
    runner.start(
        "bas-dev",
        f"exec {runner.py} {runner.tool('bas-dev', 'responder.py')} --name {OBJECT_NAME} --instance {INSTANCE} "
        f"--address {DEVICE_IP}/24:47808 --broadcast-iam --announce 3 >/tmp/responder.log 2>&1",
    )
    settle()
    rec.save("acl-bcast-iam-unsolicited-one-way.txt", runner.apply_acl(STAGE_ONE_WAY))
    runner.flush_state()
    quiet: WhoIs = whois(runner, bind="0.0.0.0", wait_s=10, listen_only=True)  # noqa: S104
    quiet_hits: RuleHits = runner.rule_hits()
    rec.save("acl-counters-bcast-iam-unsolicited-without-rule3.txt", runner.acl_counters())
    rec.fact("bcast_iam_unsolicited_without_rule3", f"found={quiet.found} via={quiet.via} hits={quiet_hits.packets}")
    rec.check("unsolicited broadcast I-Am: without rule 3 the client hears nothing", not quiet.found, quiet.raw or "no output")

    rec.save("acl-bcast-iam-unsolicited-both-ways.txt", runner.apply_acl(STAGE_BOTH_WAYS))
    runner.flush_state()
    heard: WhoIs = whois(runner, bind="0.0.0.0", wait_s=10, listen_only=True)  # noqa: S104
    heard_hits: RuleHits = runner.rule_hits()
    rec.save("acl-counters-bcast-iam-unsolicited-with-rule3.txt", runner.acl_counters())
    rec.fact("bcast_iam_unsolicited_with_rule3", f"found={heard.found} via={heard.via} name={heard.object_name!r} hits={heard_hits.packets}")
    rec.check(
        "unsolicited broadcast I-Am: with rule 3 the client hears it through the OPS BBMD and can read the device",
        heard.found and heard.via == [OPS_BBMD_IP] and heard.object_name == OBJECT_NAME,
        heard.raw or "no output",
    )
    if heard_hits.parsed:
        rec.check("unsolicited broadcast I-Am: rule 3 matched packets", heard_hits.hit("bas_bbmd_to_ops_bbmd") > 0, str(heard_hits.packets))
    else:
        rec.fact("bcast_iam_rule_hits_unparsed", heard_hits.raw[-1500:])
    rec.save("acl-unicast-only-after-bcast.txt", runner.apply_acl(STAGE_UNICAST))


def run_fuxa(runner: Runner, rec: Recorder, probe: FuxaProbe, settle: Callable[[], None]) -> None:
    """A real FUXA 1.3.5 (node-bacnet 0.2.4) on OPS reads device 3003 through the BBMDs and the stateful ACL."""
    stop_device(runner)
    start_device(runner)
    settle()
    probe.start()
    runner.attach_fuxa(probe)
    rec.fact("fuxa_container", probe.logs()[-600:])

    # Positive: the ACL of the bbmd-both-ways-device-replies stage, with FUXA as the client. The ACL is in
    # place before the project is posted: FUXA sends its Who-Is once, when it connects.
    rec.save("acl-fuxa-full.txt", runner.apply_acl(STAGE_FULL, FUXA_IP))
    runner.flush_state()
    probe.start_capture("fuxa-capture-full.txt")
    rec.fact("fuxa_post_project", probe.post_project(sys.executable))
    reading: TagReading = probe.wait_value(150)
    time.sleep(7)
    later: TagReading = probe.tag_reading()
    hits: RuleHits = runner.rule_hits()
    rec.save("acl-counters-fuxa-full.txt", runner.acl_counters())
    rec.fact("fuxa_full_acl", f"first={reading.value!r} later={later.value!r} hits={hits.packets}")
    probe.stop_capture()
    rec.check(
        "real FUXA connects through the OPS BBMD and reads a present value from device 3003 behind the stateful ACL",
        reading.has_value,
        reading.raw,
    )
    rec.check(
        "the FUXA value is live (the responder ramps 400..1200 ppm)",
        reading.has_value and later.has_value and 400 <= float(str(reading.value)) <= 1200 and later.value != reading.value,
        f"{reading.value!r} then {later.value!r}",
    )
    rec.save("fuxa-logs.txt", probe.logs())

    # Negative: BBMDs run, but the ACL has no BBMD rules. FUXA reconnects (project posted again), its Who-Is
    # reaches the OPS BBMD, whose own I-Am (device 9001) is enough for FUXA to report "connected", but the
    # BAS device never answers, so the tag has no value.
    rec.save("acl-fuxa-unicast-only.txt", runner.apply_acl(STAGE_UNICAST, FUXA_IP))
    runner.flush_state()
    probe.start_capture("fuxa-capture-unicast-only.txt")
    probe.post_project(sys.executable)
    blocked: TagReading = probe.wait_value(40)
    probe.stop_capture()
    rec.fact("fuxa_unicast_only_acl", f"value={blocked.value!r} raw={blocked.raw} hits={runner.rule_hits().packets}")
    rec.check("FUXA reads no value when the ACL has no BBMD rules", not blocked.has_value, blocked.raw)

    # FUXA with a device that broadcasts its I-Am. FUXA binds its own address, so it cannot hear a broadcast.
    stop_device(runner)
    start_device(runner, broadcast_iam=True)
    settle()
    rec.save("acl-fuxa-bcast-iam.txt", runner.apply_acl(STAGE_BOTH_WAYS, FUXA_IP))
    runner.flush_state()
    probe.start_capture("fuxa-capture-bcast-iam.txt")
    probe.post_project(sys.executable)
    bcast: TagReading = probe.wait_value(60)
    probe.stop_capture()
    rec.fact("fuxa_bcast_iam_device", f"value={bcast.value!r} raw={bcast.raw} hits={runner.rule_hits().packets}")
    rec.save("acl-counters-fuxa-bcast-iam.txt", runner.acl_counters())

    # What node-bacnet does with `broadcastAddress` set to the device's own address: a unicast Who-Is.
    stop_device(runner)
    start_device(runner)
    settle()
    rec.save("acl-fuxa-direct.txt", runner.apply_acl(STAGE_UNICAST, FUXA_IP))
    runner.flush_state()
    probe.start_capture("fuxa-capture-direct.txt")
    probe.post_project(sys.executable, broadcast_address=DEVICE_IP)
    direct: TagReading = probe.wait_value(60)
    probe.stop_capture()
    rec.fact("fuxa_broadcast_address_is_device_ip", f"value={direct.value!r} raw={direct.raw} hits={runner.rule_hits().packets}")
    rec.save("acl-counters-fuxa-direct.txt", runner.acl_counters())
    rec.fact("fuxa_container_end", probe.logs()[-1500:])
