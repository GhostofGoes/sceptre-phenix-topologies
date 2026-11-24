#!/usr/bin/env python3
"""CI driver for the wan-bgp spike on VyOS: the lab's WAN on four real routers.

Run as root on a runner prepared by .github/actions/cm-lab, with phenix
patched by patches/vrouter-config-passthrough.ci-e2e-minimega.patch:
  sudo -E /opt/cm-spike-venv/bin/python vyos_ci.py

Four VyOS routers and two hosts boot under phenix and minimega: two ISPs that
peer (shadecast, tumbleweed), the head office's edge on both (fr-fw-edge), and
the Fort Collins edge on both (fc-fw-edge). Every router gets the commands of
the range's generator (tools/wangen) as the `config` list of the vrouter app,
so phenix writes them into the script the image runs at first boot. The lists
are those of vyos_sketch.py and what the generator adds: a source filter on
each tunnel, NAT at both edges, the site's failover default routes, and the
ISPs' conditional default route. Here each ISP's default route depends on the
other ISP's prefix, because the reduced lab has no transit carrier.

Checked, in order:
  1. phenix renders the list after its own configuration, before the commit;
  2. every router commits it at first boot (when one does not,
     vyos_commit_check.sh names the lines it refuses);
  3. BGP between the ISPs and to the head office, the head office's two
     default routes and their preferred source;
  4. the two WireGuard tunnels, the mark that ties each to an uplink, and BGP
     with BFD inside them;
  5. a host at the site reaches a host at the head office through the tunnel,
     and the head office refuses a network of another site when the site's
     edge announces it;
  6. the head office drops a forged source that comes out of a tunnel, and a
     host at each edge reaches public addresses through NAT;
  7. silent faults on ShadeCast (the site's uplink, then the head office's),
     the site's uplink set down, and a silence of 200 s: where the traffic
     goes, how long it takes, whether a tunnel endpoint moves, whether a
     tunnel that was given up comes back, and whether the site's default
     route moves;
  8. the head office restarts;
  9. the ISPs stop peering, and each takes its default route back.
"""

from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import itertools
import json
import os
import re
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools" / "wangen"))

from vmlab import Lab, log, main_guard, wait_for  # noqa: E402
from wangen import model as generator_model  # noqa: E402
from wangen import vyos as generator  # noqa: E402

import netns_lab as model  # noqa: E402
import vyos_sketch  # noqa: E402

EXP: str = "spike-wan-bgp"
BOOT_DIR: Path = Path("/phenix/experiments") / EXP / "vrouter"
WORK: Path = Path("/tmp/cm-wan-bgp-vyos")
WGKEYS: Path = HERE.parents[1] / "tools" / "plantgen" / "plantgen" / "wgkeys.py"
# Anyone can derive these keys from this seed: they protect nothing but this run.
KEY_SEED: str = "cherrymountain-wan-bgp-spike-only"
VBASH: str = "source /opt/vyatta/etc/functions/script-template"
# VyOS's default MTU for a WireGuard interface.
WIREGUARD_MTU: int = 1420
SECRET: re.Pattern[str] = re.compile(r"(private-key|public-key|secret) '[^']+'")
# A file sent into a guest travels base64-encoded in a cc script, which a log
# may record; a long base64 run in the evidence is dropped whatever it holds.
BASE64_RUN: re.Pattern[str] = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


def redact(text: str) -> str:
    """The run's evidence without keys, pre-shared keys, or files sent into a guest."""
    return BASE64_RUN.sub("[base64 removed]", SECRET.sub(r"\1 'REDACTED'", text))


# The tunnels: `wireguard`, or `vti` for route-based IPsec.
KIND: str = os.environ.get("CM_TUNNEL", vyos_sketch.WIREGUARD)
# Who opens the IPsec tunnels: `site` (the head office only answers) or `both`.
OPEN: str = os.environ.get("CM_IPSEC_OPEN", "site")


def tunnel_named(name: str) -> model.Tunnel:
    return next(tunnel for tunnel in model.TUNNELS if tunnel.name == name)


def tif(name: str) -> str:
    """The interface of a tunnel on a VyOS router: wg101, or vti101."""
    return vyos_sketch.tunnel_if(KIND, tunnel_named(name))


def site_uplink(tunnel: model.Tunnel) -> tuple[str, str]:
    """(ethN, the site's own address) of the uplink that carries a tunnel."""
    link = model.link_of(tunnel.site, tunnel.via)
    return next(
        (eth, own)
        for eth, entry, _other, _gateway, own in vyos_sketch.uplinks(model.BY_NAME[tunnel.site])
        if entry is link
    )


def pin_lookup(tunnel: model.Tunnel) -> str:
    """The route lookup a tunnel's own packets get at its site: by mark, or by source address."""
    _eth, own = site_uplink(tunnel)
    selector = f"mark {tunnel.mark()}" if KIND == vyos_sketch.WIREGUARD else f"from {own}"
    return f"ip route get {model.HUB_ENDPOINT} {selector} 2>&1"


def load_wgkeys() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cm_wgkeys", WGKEYS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {WGKEYS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["cm_wgkeys"] = module
    spec.loader.exec_module(module)
    return module


def scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_./-]*", text) else json.dumps(text)


def emit(value: Any, indent: int = 0) -> str:
    """Block-style YAML of dicts, lists, strings, and integers."""
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines += [f"{pad}{key}:", emit(item, indent + 1)]
            else:
                lines.append(f"{pad}{key}: {scalar(item)}")
    else:
        for item in value:
            if isinstance(item, (dict, list)):
                body = emit(item, indent + 1).splitlines()
                lines += [f"{pad}- {body[0].strip()}", *body[1:]]
            else:
                lines.append(f"{pad}- {scalar(item)}")
    return "\n".join(lines)


def vlan(link: model.Link) -> str:
    return f"{model.BY_NAME[link.a].short}-{model.BY_NAME[link.b].short}".upper()


def interface(name: str, vlan_name: str, address: str, mask: int, gateway: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "vlan": vlan_name, "address": address, "mask": mask}
    if gateway:
        entry["gateway"] = gateway
    return {**entry, "proto": "static", "type": "ethernet"}


def topology() -> dict[str, Any]:
    """The reduced lab as a phenix topology. A router's interfaces are in the order of its links."""
    nodes: list[dict[str, Any]] = []
    for router in model.ROUTERS:
        interfaces = [
            interface(eth, vlan(link), own, ipaddress.ip_network(link.net).prefixlen)
            for eth, link, _other, _gateway, own in vyos_sketch.uplinks(router)
        ]
        for lan in model.LANS:
            if lan.router == router.name:
                interfaces.append(interface(f"eth{len(interfaces)}", f"{router.short.upper()}-LAN", lan.gateway(), 24))
        nodes.append(
            {
                "type": "Router",
                "general": {"hostname": router.name},
                "hardware": {
                    "os_type": "vyos",
                    "drives": [{"image": "vyos.qc2", "inject_partition": 3}],
                    "memory": 2048,
                    "vcpus": 2,
                },
                "network": {"interfaces": interfaces},
            }
        )
    for lan in model.LANS:
        short = model.BY_NAME[lan.router].short.upper()
        nodes.append(
            {
                "type": "VirtualMachine",
                "general": {"hostname": lan.host},
                "hardware": {
                    "os_type": "linux",
                    "drives": [{"image": "cm-spike.qc2", "inject_partition": 1}],
                    "memory": 1024,
                    "vcpus": 1,
                },
                "network": {"interfaces": [interface("eth0", f"{short}-LAN", lan.host_address(), 24, lan.gateway())]},
            }
        )
    return {
        "apiVersion": "phenix.sandia.gov/v1",
        "kind": "Topology",
        "metadata": {"name": EXP},
        "spec": {"nodes": nodes},
    }


def scenario(configs: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "apiVersion": "phenix.sandia.gov/v2",
        "kind": "Scenario",
        "metadata": {"name": EXP, "annotations": {"topology": EXP}},
        "spec": {
            "apps": [
                {
                    "name": "vrouter",
                    "hosts": [{"hostname": name, "metadata": {"config": lines}} for name, lines in configs.items()],
                }
            ]
        },
    }


def vyos_op(lab: Lab, vm: str, command: str) -> str:
    """Run a VyOS operational command in a router."""
    script = (
        f"cat > /tmp/op.sh <<'OPEOF'\n#!/bin/vbash\n{VBASH}\nrun {command}\nOPEOF\n"
        "chmod +x /tmp/op.sh; sg vyattacfg -c /tmp/op.sh 2>&1"
    )
    return lab.shell(vm, script, wait_s=180)


def config_missing(lab: Lab, vm: str, lines: list[str]) -> list[str]:
    """The `set` lines of a config list that the router's committed configuration does not hold.

    A refused `set` in the first-boot script prints an error and the script goes
    on to commit the rest, so a count of BGP lines cannot show it. VyOS quotes
    values its own way, so quotes are dropped on both sides; lines with keys
    are left out, since VyOS may show them masked.
    """

    def norm(line: str) -> str:
        return " ".join(line.replace("'", "").split())

    shown = {
        norm(line) for line in vyos_op(lab, vm, "show configuration commands").splitlines() if line.startswith("set ")
    }
    return [line for line in lines if line.startswith("set ") and not SECRET.search(line) and norm(line) not in shown]


def bgp_states(lab: Lab, vm: str) -> dict[str, str]:
    """Neighbor -> state, from FRR on a router."""
    out = lab.shell(vm, "vtysh -c 'show bgp ipv4 unicast summary json' 2>&1")
    try:
        peers = json.loads(out[out.index("{") :]).get("peers", {})
    except ValueError:
        return {"(no answer)": out.strip()[:200]}
    return {f"{address} ({peer.get('desc', '')})": peer.get("state", "?") for address, peer in peers.items()}


def expected_sessions(router: model.Router) -> int:
    count = sum(1 for link in model.LINKS if router.name in (link.a, link.b) and link.relation != "static")
    return count + sum(1 for tunnel in model.TUNNELS if router.name in (tunnel.hub, tunnel.site))


PING_INTERVAL_S: float = 0.2
LONG_SILENCE_S: int = 200
# How often the fault on the head office's uplink runs: enough to see an outage
# that only some rounds have (three of eight IPsec runs had one near 5 s).
HQ_UPLINK_ROUNDS: int = 6
# An outage this long is more than BFD's detection time explains.
LONG_OUTAGE_S: float = 4.0
# How long the ping runs through a reboot of the head office's edge.
HQ_RESTART_WINDOW_S: int = 600
# How long the head office's edge waits between the reboot command and the reboot.
REBOOT_DELAY_S: int = 10
EVENT_LOG: str = "/tmp/frr-events.log"

HQ: str = "fr-fw-edge"
JOURNAL: str = (
    "journalctl -b --no-pager -o short-precise 2>/dev/null "
    "| grep -E 'bgpd|bfdd|zebra|staticd|mgmtd|commit\\[|vyos-router|vyos-configd' | tail -200"
)
FRR_DEBUG_LOG: str = "/tmp/frr-debug.log"


def hq_defaults(lab: Lab) -> tuple[str, list[tuple[str, str, str]]]:
    """(the head office's default route, the (ISP, its address, the head office's address) without a next hop in it)."""
    route = lab.shell(HQ, "ip route show default 2>&1")
    missing = [
        (isp, gateway, own)
        for _eth, _link, isp, gateway, own in vyos_sketch.uplinks(model.BY_NAME[HQ])
        if f"via {gateway} " not in route
    ]
    return route, missing


def vtysh(lab: Lab, vm: str, *commands: str) -> str:
    return lab.shell(vm, "vtysh " + " ".join(f"-c '{command}'" for command in commands) + " 2>&1", wait_s=120)


def explain_missing_default(lab: Lab, missing: list[tuple[str, str, str]]) -> bool:
    """The head office lacks a default route from an ISP: record both sides, then try to get it back.

    Three steps, each less gentle than the one before, and the first that works
    is recorded: the head office asks the ISP again (a route refresh), the ISP
    sends again, the session restarts. Returns whether both routes are there.
    """
    debug = ("configure terminal", f"log file {FRR_DEBUG_LOG} debugging")
    for vm in {HQ, *(isp for isp, _gateway, _own in missing)}:
        vtysh(lab, vm, *debug)
        vtysh(lab, vm, "debug bgp updates", "debug bgp neighbor-events")
    for isp, gateway, own in missing:
        lab.save(
            f"default-{isp}-side.txt",
            vtysh(lab, isp, f"show bgp ipv4 unicast neighbors {own} advertised-routes", f"show bgp neighbors {own}"),
        )
        lab.save(
            f"default-{HQ}-side-{isp}.txt",
            vtysh(
                lab, HQ, f"show bgp ipv4 unicast neighbors {gateway} received-routes", f"show bgp neighbors {gateway}"
            ),
        )
        lab.save(f"default-{isp}-journal.txt", lab.shell(isp, JOURNAL, wait_s=120))
    lab.save(f"default-{HQ}-journal.txt", lab.shell(HQ, JOURNAL, wait_s=120))

    def both() -> tuple[bool, str]:
        route, still = hq_defaults(lab)
        return not still, " ".join(route.split())

    fixed_by = ""
    steps = (
        ("a route refresh asked by the head office", HQ, "clear bgp ipv4 unicast {gateway} soft in"),
        ("the ISP sending its routes again", "{isp}", "clear bgp ipv4 unicast {own} soft out"),
        ("a restart of the session", HQ, "clear bgp {gateway}"),
    )
    for title, where, command in steps:
        for isp, gateway, own in missing:
            vtysh(lab, where.format(isp=isp), command.format(gateway=gateway, own=own))
        try:
            detail = wait_for(f"two default routes after {title}", 45, 5, both)
        except TimeoutError as exc:
            lab.fact(f"default routes after {title}", str(exc)[-300:])
            continue
        lab.fact(f"default routes after {title}", detail)
        fixed_by = title
        break
    for vm in {HQ, *(isp for isp, _gateway, _own in missing)}:
        lab.save(
            f"default-{vm}-debug.log",
            lab.shell(vm, f"grep -E '0\\.0\\.0\\.0/0|DENIED|Established|refresh|default' {FRR_DEBUG_LOG} | tail -150"),
        )
        vtysh(lab, vm, "no debug bgp updates", "no debug bgp neighbor-events")
    lab.fact("what brought the missing default route back", fixed_by or "nothing: it is still missing")
    return bool(fixed_by)


def blackhole(lab: Lab, vm: str, ifname: str) -> None:
    """Silently drop what a router forwards, sends, or receives on one interface. The link stays up."""
    lab.shell(
        vm,
        "nft add table inet cmfault; "
        "nft add chain inet cmfault cm_forward '{ type filter hook forward priority -300; }'; "
        "nft add chain inet cmfault cm_input '{ type filter hook input priority -300; }'; "
        "nft add chain inet cmfault cm_output '{ type filter hook output priority -300; }'; "
        f"nft add rule inet cmfault cm_forward iifname {ifname} counter drop; "
        f"nft add rule inet cmfault cm_forward oifname {ifname} counter drop; "
        f"nft add rule inet cmfault cm_input iifname {ifname} counter drop; "
        f"nft add rule inet cmfault cm_output oifname {ifname} counter drop; echo faulted",
    )


def heal(lab: Lab, vm: str) -> int:
    """Remove the fault; return how many packets its rules dropped."""
    out = lab.shell(vm, "nft list table inet cmfault 2>&1; nft delete table inet cmfault 2>&1; echo healed")
    return sum(int(count) for count in re.findall(r"counter packets (\d+)", out))


def start_ping(lab: Lab, tag: str, seconds: float) -> int:
    """Start a background ping from the site's host to the head office's; return how many it sends."""
    count = int(seconds / PING_INTERVAL_S)
    lab.shell(
        "fc-historian",
        f"setsid nohup ping -D -i {PING_INTERVAL_S} -W 1 -c {count} 10.20.30.11 >/tmp/ping-{tag}.log 2>&1 </dev/null & "
        "sleep 1; echo started",
    )
    return count


def ping_result(lab: Lab, tag: str, count: int) -> tuple[int, float, str]:
    """(lost, longest gap in seconds, the summary lines) of a ping that has finished."""

    def finished() -> tuple[bool, str]:
        out = lab.shell("fc-historian", f"tail -3 /tmp/ping-{tag}.log")
        return "packets transmitted" in out, out

    tail = wait_for(f"ping {tag}", count * PING_INTERVAL_S + 60, 5, finished)
    seqs = lab.shell(
        "fc-historian", f"grep -o 'icmp_seq=[0-9]* ttl' /tmp/ping-{tag}.log | grep -o '[0-9]*' | tr '\\n' ' '"
    )
    seen = sorted({int(seq) for seq in seqs.split()})
    longest, previous = 0, 0
    for seq in [*seen, count + 1]:
        longest, previous = max(longest, seq - previous - 1), seq
    return count - len(seen), round(longest * PING_INTERVAL_S, 1), " ".join(tail.split())


def gap_window(lab: Lab, tag: str) -> tuple[float, float]:
    """When the longest gap of a finished ping began and ended (the replies on each side of it), in epoch seconds.

    (0, 0) when the ping has fewer than two replies. The clock is the pinging host's.
    """
    out = lab.shell(
        "fc-historian",
        f"sed -n 's/^\\[\\([0-9.]*\\)\\].*icmp_seq=\\([0-9]*\\) ttl.*/\\2 \\1/p' /tmp/ping-{tag}.log",
        wait_s=120,
    )
    replies = sorted((int(seq), float(at)) for seq, at in (line.split() for line in out.splitlines() if line.strip()))
    widest: tuple[int, float, float] = (0, 0.0, 0.0)
    for (seq_a, at_a), (seq_b, at_b) in itertools.pairwise(replies):
        if seq_b - seq_a > widest[0]:
            widest = (seq_b - seq_a, at_a, at_b)
    return widest[1], widest[2]


def event_log(lab: Lab, routers: tuple[str, ...], on: bool) -> None:
    """Have FRR write BFD and BGP session events, with milliseconds, to a file on each router."""
    for vm in routers:
        if on:
            commands = (
                f"vtysh -c 'configure terminal' -c 'log file {EVENT_LOG} debugging' -c 'log timestamp precision 3'",
                "vtysh -c 'debug bfd peer'",
                "vtysh -c 'debug bgp neighbor-events'",
                "vtysh -c 'debug bgp bfd'",
            )
        else:
            commands = (
                "vtysh -c 'no debug bfd peer'",
                "vtysh -c 'no debug bgp neighbor-events'",
                "vtysh -c 'no debug bgp bfd'",
            )
        lab.shell(vm, "; ".join(f"{command} 2>&1" for command in commands) + "; echo set", wait_s=120)


def events_between(lab: Lab, vm: str, start: float, end: float) -> str:
    """The session events a router logged between two epoch times (its clock is the host's, to about a second)."""
    first, last = (time.strftime("%Y/%m/%d %H:%M:%S", time.gmtime(at)) for at in (start, end))
    return lab.shell(
        vm,
        f"awk -v a='{first}' -v b='{last}.999' '($1 \" \" $2) >= a && ($1 \" \" $2) <= b' {EVENT_LOG} 2>&1 "
        "| grep -E 'ADJCHANGE|state-change|BFD|Established|went from|nexthop' | tail -80",
        wait_s=120,
    )


def sessions_down(events: str) -> list[str]:
    """The neighbors whose BGP session a router's events show going down, each with the reason FRR gave."""
    found = re.findall(r"ADJCHANGE: neighbor (\S+?)\(\S*\) in vrf \S+ Down (.+)", events)
    return sorted({f"{neighbor}: {reason.strip()}" for neighbor, reason in found})


def site_tunnel(lab: Lab) -> str:
    """The interface the site uses toward the head office's block."""
    out = lab.shell("fc-fw-edge", "ip route show 10.20.0.0/16 2>&1")
    match = re.search(r"dev (\S+)", out)
    return match.group(1) if match else f"(none: {out.strip()[:80]})"


def tunnel_mtu(lab: Lab, site: str) -> None:
    """A full-size packet with DF set crosses each tunnel, and one byte more is refused at the edge."""
    mtu = vyos_sketch.VTI_MTU if KIND == vyos_sketch.VTI else WIREGUARD_MTU
    answers: dict[str, list[str]] = {}
    for tunnel in model.TUNNELS:
        far = tunnel.hub_address()
        fits = lab.shell(site, f"ping -M do -s {mtu - 28} -c 3 -W 2 {far} 2>&1 | tail -2")
        over = lab.shell(site, f"ping -M do -s {mtu - 27} -c 1 -W 2 {far} 2>&1 | head -3")
        answers[tunnel.name] = [" ".join(fits.split()), " ".join(over.split())]
    lab.check(
        f"a {mtu}-byte packet with DF set crosses each tunnel, and a larger one is refused at the edge",
        all(" 0% packet loss" in fits and "too long" in over for fits, over in answers.values()),
        json.dumps(answers),
    )


def edge_sources(lab: Lab, site: str) -> None:
    """Each edge's own traffic into a tunnel leaves from its LAN address, not from the tunnel's /31."""
    lans = {lan.router: lan for lan in model.LANS}
    to_hq = lab.shell(site, f"ip route get {lans[HQ].host_address()} 2>&1")
    to_site = lab.shell(HQ, f"ip route get {lans[site].host_address()} 2>&1")
    lab.check(
        "each edge sends its own traffic into a tunnel from its LAN address",
        f"src {lans[site].gateway()} " in f"{to_hq} " and f"src {lans[HQ].gateway()} " in f"{to_site} ",
        f"{site}: {' '.join(to_hq.split())}; {HQ}: {' '.join(to_site.split())}",
    )


def tunnels_intact(lab: Lab) -> tuple[bool, str]:
    """WireGuard: no endpoint moved off the head office's address. VTI: both interfaces and both SAs are up."""
    if KIND == vyos_sketch.WIREGUARD:
        out = lab.shell("fc-fw-edge", "wg show all endpoints 2>&1")
        seen = dict(re.findall(r"(wg\d+)\s+\S+\s+(\S+:\d+)", out))
        wanted = {tif(tunnel.name): f"{model.HUB_ENDPOINT}:{tunnel.port}" for tunnel in model.TUNNELS}
        return seen == wanted, json.dumps(seen)
    out = lab.shell(
        "fc-fw-edge",
        "for i in "
        + " ".join(tif(tunnel.name) for tunnel in model.TUNNELS)
        + "; do ip -br link show $i; done 2>&1; "
        + "; ".join(
            f"echo sas {tunnel.name} $(swanctl --list-sas --ike {tunnel.name} 2>/dev/null | grep -c INSTALLED)"
            for tunnel in model.TUNNELS
        ),
    )
    up = len(re.findall(r"<[^>]*\bUP\b[^>]*>", out))
    sas = {name: int(count) for name, count in re.findall(r"sas (\S+) (\d+)", out)}
    each = len(sas) == len(model.TUNNELS) and all(count >= 1 for count in sas.values())
    return up == len(model.TUNNELS) and each, " ".join(out.split())


def wait_tunnel(lab: Lab, want: str, timeout_s: float) -> tuple[bool, str]:
    try:
        return True, wait_for(f"the site on {want}", timeout_s, 3, lambda: ((got := site_tunnel(lab)) == want, got))
    except TimeoutError as exc:
        return False, str(exc)[-200:]


def private_exits(lab: Lab, routers: tuple[str, ...]) -> None:
    """No edge sends an unused private address out of an uplink: it is dropped by a blackhole.

    The probes are an unused address in each of the edge's own blocks, one in
    each private range, and one in each block of a site that has no tunnel in
    this reduced lab, which the head office would otherwise send to an ISP.
    """
    leaks: dict[str, dict[str, str]] = {}
    count = 0
    for vm in routers:
        uplinks = [eth for eth, *_rest in vyos_sketch.uplinks(model.BY_NAME[vm])]
        probes = model.private_probes(vm) + [
            model.private_probes(other)[0] for other in model.SITE_BLOCKS if other not in (HQ, *routers)
        ]
        count += len(probes)
        for probe in probes:
            answer = " ".join(lab.shell(vm, f"ip route get {probe} 2>&1").split())
            if any(f"dev {eth} " in answer + " " for eth in uplinks):
                leaks.setdefault(vm, {})[probe] = answer
    blackholes = {vm: "blackhole" in lab.shell(vm, "ip route show 10.0.0.0/8 2>&1") for vm in routers}
    lab.check(
        "no edge would send an unused private address out of an uplink",
        not leaks and all(blackholes.values()),
        json.dumps(leaks)[:600]
        if leaks
        else f"{count} probes; 10.0.0.0/8 is a blackhole at {sum(blackholes.values())} of {len(routers)} edges",
    )


def foreign_prefix(lab: Lab, site: str) -> None:
    """The site's edge announces a network of another site, and the head office must not take it.

    The edge's own export filter is opened first, as somebody who controls the
    edge would open it, so that what stops the route is the head office's
    filter for this site. FRR is told directly: nothing here is committed to
    the VyOS configuration.
    """
    asn = model.BY_NAME[site].asn
    own = f"SITE-{vyos_sketch.site_code(site)}"
    peers = [tunnel.hub_address() for tunnel in model.TUNNELS]
    # Another site's network, which the head office has from nobody in this
    # lab, and a /24 inside the head office's own block. The head office's
    # list of all sites' blocks (SITE-NETS) holds both, so only the filter for
    # this site refuses both.
    for prefix, name in (
        (model.CS_LAN, "another site's network"),
        (model.HQ_MORE_SPECIFIC, "a more-specific of the head office's own block"),
    ):
        vtysh(
            lab, site, "configure terminal", f"ip route {prefix} blackhole", f"ip prefix-list {own} seq 900 permit {prefix}",
            f"router bgp {asn}", "address-family ipv4 unicast", f"network {prefix}",
        )  # fmt: skip

        def offered(p: str = prefix) -> tuple[bool, str]:
            out = vtysh(lab, site, *[f"show bgp ipv4 unicast neighbors {peer} advertised-routes" for peer in peers])
            return out.count(p) >= len(peers), " ".join(out.split())[-300:]

        try:
            detail, sent = wait_for(f"{site} offering {prefix}", 60, 5, offered), True
        except TimeoutError as exc:
            detail, sent = str(exc)[-300:], False
        listed = vtysh(lab, HQ, f"show ip prefix-list SITE-NETS {prefix} first-match")
        held = vtysh(lab, HQ, f"show bgp ipv4 unicast {prefix}")
        route = lab.shell(HQ, f"ip route show {prefix} 2>&1").strip()
        lab.check(
            f"the head office refuses {name} when the site announces it",
            sent and "permit" in listed and "not in table" in held and not route,
            f"{site} offered {prefix} on {len(peers)} tunnels: {sent} ({detail[-120:]}). The head office's list of "
            f"all sites' blocks covers it: {'permit' in listed}. The head office: {' '.join(held.split())[:160]}; "
            f"kernel route: {route or 'none'}",
        )
        vtysh(
            lab, site, "configure terminal", f"router bgp {asn}", "address-family ipv4 unicast", f"no network {prefix}",
            "exit", "exit", f"no ip route {prefix} blackhole", f"no ip prefix-list {own} seq 900",
        )  # fmt: skip


def bfd_sessions(lab: Lab, vm: str) -> tuple[bool, str]:
    """Every BFD session the router's list configures is up, at the list's interval."""
    # One session for each BGP neighbor that uses the profile; the profile's own
    # lines (`set protocols bfd profile WAN interval ...`) are not sessions.
    want = sum(
        line.startswith("set protocols bgp neighbor ") and line.endswith(" bfd profile WAN")
        for line in vyos_sketch.commands(model.BY_NAME[vm], kind=KIND)
    )
    out = lab.shell(vm, "vtysh -c 'show bfd peers json' 2>&1")
    try:
        peers = json.loads(out[out.index("[") :])
    except ValueError:
        return False, " ".join(out.split())[:300]
    up = [peer for peer in peers if peer.get("status") == "up"]
    intervals = sorted({peer.get("transmit-interval") for peer in peers})
    ok = len(peers) == want and len(up) == want and intervals == [vyos_sketch.BFD_INTERVAL_MS]
    return ok, f"{len(up)} of {want} up; transmit intervals {intervals} ms"


def best_path(lab: Lab, vm: str, prefix: str) -> str:
    """The AS path of `vm`'s best route to `prefix`, from FRR's JSON, or "(none)"."""
    out = lab.shell(vm, f"vtysh -c 'show bgp ipv4 unicast {prefix} json' 2>&1")
    try:
        data = json.loads(out[out.index("{") :])
    except ValueError:
        return "(none)"
    for path in data.get("paths", []):
        if path.get("bestpath", {}).get("overall"):
            return path.get("aspath", {}).get("string", "") or "(local)"
    return "(none)"


EVENT_ROUTERS: tuple[str, ...] = (HQ, "fc-fw-edge", "shadecast", "tumbleweed")
# How long an ISP may take to withdraw or to send its conditional default route.
DEFAULT_CONDITION_WINDOW_S: int = 180


def lan_if(router: str) -> str:
    """The LAN interface of an edge: the one after its uplinks."""
    return f"eth{len(vyos_sketch.uplinks(model.BY_NAME[router]))}"


def source_filter(lab: Lab, site: str) -> None:
    """The head office drops and logs what comes out of a tunnel with a source that is not the site's.

    The site's edge sends pings from an address of another site's block, to a
    host at the head office (the forward chain) and to the head office's edge
    (the input chain). No reply would come back in any case, so the proof is
    the head office's log, which names the rule and the source.
    """
    lans = {lan.router: lan for lan in model.LANS}
    other = next(name for name in model.SITE_BLOCKS if name not in (HQ, site))
    forged = str(ipaddress.ip_network(model.SITE_BLOCKS[other][0])[99])
    honest = lab.shell(
        site, f"ping -c 3 -i 0.2 -W 2 -I {lans[site].gateway()} {lans[HQ].host_address()} 2>&1 | tail -2"
    )
    lab.shell(
        site,
        f"ip addr add {forged}/32 dev lo; "
        f"ping -c 3 -i 0.2 -W 1 -I {forged} {lans[HQ].host_address()} >/dev/null 2>&1; "
        f"ping -c 3 -i 0.2 -W 1 -I {forged} {lans[HQ].gateway()} >/dev/null 2>&1; "
        f"ip addr del {forged}/32 dev lo; echo sent",
    )
    logged = lab.shell(
        HQ,
        "(journalctl -k --no-pager 2>/dev/null; dmesg 2>/dev/null) | "
        f"grep -E 'ipv4-(FWD|INP)-filter-1[0-9][0-9]-D' | grep 'SRC={forged} ' | "
        "grep -oE 'ipv4-(FWD|INP)-filter-[0-9]+-D\\]? ?IN=[a-z0-9]+' | sort | uniq -c",
    )
    lab.fact(
        "the head office's tunnel filter rules",
        lab.shell(HQ, "nft list table ip vyos_filter 2>&1 | grep -E 'VPN-|filter-1[0-9][0-9]' | head -20"),
    )
    lab.check(
        "the head office drops and logs a source that is not the site's when it comes out of the site's tunnel",
        "FWD-filter" in logged and "INP-filter" in logged and " 0% packet loss" in honest,
        f"forged source {forged} (a block of {other}); logged at the head office: {' '.join(logged.split())}; "
        f"from the site's own address: {' '.join(honest.split())}",
    )


def nat_sources(lab: Lab, vm: str, host: str) -> dict[str, str]:
    """For the pings of `host` in an edge's connection table: the destination -> where its replies go."""
    table = lab.shell(vm, "conntrack -L -p icmp 2>/dev/null")
    return dict(re.findall(rf"src={re.escape(host)} dst=(\S+) type=8 code=0 id=\d+ src=\S+ dst=(\S+) type=0", table))


def internet_access(lab: Lab, site: str) -> None:
    """A host at each edge reaches public addresses, with the edge's NAT.

    The site's host reaches the head office's public endpoint outside the VPN,
    as its uplink's address. The head office's host reaches the ISPs' addresses
    on every link, so both of its uplinks carry a flow, as one address of the
    head office's prefix.
    """
    lans = {lan.router: lan for lan in model.LANS}
    site_uplinks = vyos_sketch.uplinks(model.BY_NAME[site])
    ping = lab.shell(lans[site].host, f"ping -c 5 -i 0.2 -W 2 {model.HUB_ENDPOINT} 2>&1 | tail -2")
    seen = nat_sources(lab, site, lans[site].host_address())
    routes = lab.shell(site, "ip -4 route show default 2>&1")
    lab.check(
        "a host at the site reaches a public address as the address of the site's first uplink",
        " 0% packet loss" in ping and seen.get(model.HUB_ENDPOINT) == site_uplinks[0][4],
        f"{' '.join(ping.split())}; replies go to {seen.get(model.HUB_ENDPOINT)} (the uplink is {site_uplinks[0][4]})",
    )
    lab.check(
        "the site holds a failover default route for each uplink",
        routes.count("proto failover") == len(site_uplinks)
        and all(f"via {gateway} " in routes for _eth, _link, _isp, gateway, _own in site_uplinks),
        " | ".join(routes.splitlines()),
    )

    # Every ISP address in the lab. The two on the head office's own links leave by different uplinks.
    isps = [router.name for router in model.ROUTERS if router.public_bgp and router.name != HQ]
    targets = [
        address
        for link in model.LINKS
        for name, address in zip((link.a, link.b), link.addresses(), strict=True)
        if name in isps
    ]
    address = lans[HQ].host_address()
    # One command, so that the connection table still holds every ping when it is read.
    pinged = lab.shell(
        lans[HQ].host,
        f"for t in {' '.join(targets)}; do ping -c 2 -i 0.2 -W 2 $t >/dev/null 2>&1 && echo reached $t; done; echo end",
        wait_s=120,
    )
    reached = set(re.findall(r"reached (\S+)", pinged))
    seen = nat_sources(lab, HQ, address)
    looked = lab.shell(
        HQ, f"for t in {' '.join(targets)}; do ip route get $t from {address} iif {lan_if(HQ)} 2>&1 | head -1; done"
    )
    out_by = dict(re.findall(r"^(\S+) from .* dev (\S+)", looked, flags=re.M))
    lab.fact("the head office's uplink for each public address", json.dumps(out_by))
    lab.check(
        "a host at the head office reaches public addresses through both ISPs as the head office's NAT address",
        reached == set(targets)
        and {seen.get(target) for target in targets} == {generator_model.NAT_ADDRESS}
        and len(set(out_by.values())) == len(vyos_sketch.uplinks(model.BY_NAME[HQ])),
        f"reached {len(reached)} of {len(targets)} ISP addresses; replies go to "
        f"{sorted({str(seen.get(target)) for target in targets})}; uplinks used: {sorted(set(out_by.values()))}",
    )


def tunnel_return_paths(lab: Lab) -> None:
    """The head office sends each tunnel's packets through the ISP of that tunnel's uplink at the site.

    With a default route only, the kernel's multipath hash picks the ISP, and a
    tunnel on the site's second ISP can return through the first. A fault of
    the first ISP then stops both tunnels.
    """
    eth = {isp: name for name, _link, isp, _gateway, _own in vyos_sketch.uplinks(model.BY_NAME[HQ])}
    found: dict[str, str] = {}
    wrong: list[str] = []
    for tunnel in model.TUNNELS:
        _eth, own = site_uplink(tunnel)
        answer = " ".join(lab.shell(HQ, f"ip route get {own} from {model.HUB_ENDPOINT} 2>&1 | head -1").split())
        found[tunnel.name] = answer
        if f"dev {eth[tunnel.via]} " not in answer + " ":
            wrong.append(f"{tunnel.name} wants {eth[tunnel.via]} ({tunnel.via})")
    lab.check(
        "the head office sends each tunnel's packets through the ISP of the site's uplink",
        not wrong,
        f"{'; '.join(wrong) or 'each tunnel returns through its own ISP'}: {json.dumps(found)}",
    )


def site_default_route(lab: Lab, site: str, want: str, timeout_s: float) -> tuple[bool, str]:
    """Wait until the site's best default route is through `want`; return (whether it is, the routes)."""

    def best() -> tuple[bool, str]:
        routes = lab.shell(site, "ip -4 route show default 2>&1")
        first = routes.splitlines()[0] if routes.strip() else ""
        return f"via {want} " in first + " ", " | ".join(routes.split("\n"))

    try:
        return True, wait_for(f"the default route of {site} through {want}", timeout_s, 2, best)
    except TimeoutError as exc:
        return False, str(exc)[-300:]


def default_condition(lab: Lab) -> None:
    """Each ISP takes its default route back when it loses the prefix that the route depends on.

    In this lab the prefix is the other ISP's, so a silent fault on the link
    between the ISPs ends both conditions, and the head office loses both
    default routes. The repair brings them back, and the tunnels with them.
    """
    isp = model.BY_NAME["shadecast"]
    eth = {other: name for name, _link, other, _gateway, _own in vyos_sketch.uplinks(isp)}

    def defaults(count: int) -> tuple[bool, str]:
        route, missing = hq_defaults(lab)
        return 2 - len(missing) == count, " ".join(route.split()) or "(no default route)"

    before = defaults(2)
    started = time.monotonic()
    blackhole(lab, "shadecast", eth["tumbleweed"])
    try:
        detail, gone = (
            wait_for("no default route at the head office", DEFAULT_CONDITION_WINDOW_S, 3, lambda: defaults(0)),
            True,
        )
    except TimeoutError as exc:
        detail, gone = str(exc)[-300:], False
    took = time.monotonic() - started
    customer = vyos_sketch.uplinks(model.BY_NAME[HQ])[0][4]
    lab.fact(
        "the ISPs without their peering",
        lab.shell(
            "shadecast",
            "vtysh -c 'show bgp ipv4 unicast summary' 2>&1 | tail -6; "
            f"vtysh -c 'show bgp ipv4 unicast neighbors {customer} advertised-routes' 2>&1 | tail -8",
        ),
    )
    lab.check(
        "each ISP takes its default route back when it loses the prefix that the route depends on",
        before[0] and gone,
        f"both default routes before: {before[0]}; none {took:.0f} s after the ISPs lost each other "
        f"(the BGP hold time is {model.CORE_HOLD_S} s): {detail}",
    )
    heal(lab, "shadecast")
    started = time.monotonic()
    try:
        detail, back = (
            wait_for("both default routes at the head office", DEFAULT_CONDITION_WINDOW_S, 3, lambda: defaults(2)),
            True,
        )
    except TimeoutError as exc:
        detail, back = str(exc)[-300:], False
    took = time.monotonic() - started
    on_primary, tunnel = wait_tunnel(lab, tif("fc-a"), 300)
    lab.check(
        "both default routes and the site's primary tunnel return when the ISPs peer again",
        back and on_primary,
        f"both default routes {took:.0f} s after the repair: {detail}; the site is on {tunnel}",
    )


def sessions_settled(lab: Lab) -> bool:
    """Wait until every BFD session of both edges is up again, so that one round does not disturb the next."""

    def probe() -> tuple[bool, str]:
        states = {vm: bfd_sessions(lab, vm) for vm in (HQ, "fc-fw-edge")}
        return all(ok for ok, _detail in states.values()), json.dumps({vm: d for vm, (_ok, d) in states.items()})

    try:
        wait_for("every BFD session up again", 90, 3, probe)
    except TimeoutError:
        return False
    return True


def hq_uplink_events(lab: Lab, tag: str, number: int, gap: float, faulted: float) -> dict[str, Any]:
    """Save what each router logged around one round's outage; return the round's summary."""
    start, end = gap_window(lab, tag)
    if not start:
        start, end = faulted, faulted + 10
    events = {vm: events_between(lab, vm, start - 4, end + 6) for vm in EVENT_ROUTERS}
    lab.save(
        f"{tag}-events.txt",
        f"outage {gap} s; replies stopped at {start:.3f} and came back at {end:.3f} (the site host's clock); "
        f"the fault was set at {faulted:.3f} (the runner's clock)\n"
        + "\n".join(f"== {vm}\n{text}" for vm, text in events.items()),
    )
    return {
        "round": number,
        "outage_s": gap,
        "sessions down": {vm: downs for vm, text in events.items() if (downs := sessions_down(text))},
    }


def hq_restart(lab: Lab, lines: list[str]) -> None:
    """The head office's edge reboots, with a ping from the site's host to the head office's running through it."""
    primary = tif("fc-a")
    want = expected_sessions(model.BY_NAME[HQ])
    boot_id = "cat /proc/sys/kernel/random/boot_id"
    before = lab.shell(HQ, boot_id).strip()
    count = start_ping(lab, "hq-restart", HQ_RESTART_WINDOW_S)
    # The reboot waits, so that the command's answer gets back first. In one run
    # with 2 s it did not, and the job stopped there. The times count from the reboot.
    try:
        lab.shell(HQ, f"(sleep {REBOOT_DELAY_S}; systemctl reboot) >/dev/null 2>&1 </dev/null & echo rebooting")
    except TimeoutError as exc:
        lab.fact("the reboot command's answer", f"none: {str(exc)[-160:]}")
    issued, issued_wall = time.monotonic() + REBOOT_DELAY_S, time.time() + REBOOT_DELAY_S

    def rebooted() -> tuple[bool, str]:
        now = lab.shell(HQ, boot_id, wait_s=20).strip()
        return bool(now) and now != before, now

    def established() -> tuple[bool, str]:
        states = bgp_states(lab, HQ)
        return len(states) == want and set(states.values()) == {"Established"}, json.dumps(states)

    times: dict[str, float] = {}
    try:
        wait_for("the head office's edge after its reboot", 420, 5, rebooted)
        times["edge answers"] = round(time.monotonic() - issued)
        wait_for("BGP at the head office after its reboot", 420, 5, established)
        times["its BGP sessions are up"] = round(time.monotonic() - issued)
    except TimeoutError as exc:
        lab.check("the head office's edge comes back from a reboot with its configuration", False, str(exc)[-300:])
        ping_result(lab, "hq-restart", count)
        return
    missing = config_missing(lab, HQ, lines)
    lab.check(
        "the head office's edge comes back from a reboot with its configuration",
        not missing,
        f"seconds after the reboot command: {json.dumps(times)}; "
        f"{len(missing)} of {len(lines)} config lines missing: {redact(json.dumps(missing[:6]))}",
    )
    back, detail = wait_tunnel(lab, primary, 420)
    times["the site routes through its primary tunnel"] = round(time.monotonic() - issued)
    lost, gap, tail = ping_result(lab, "hq-restart", count)
    _start, end = gap_window(lab, "hq-restart")
    if end:
        times["traffic is back"] = round(end - issued_wall)
    after_bgp = times.get("traffic is back", 0) - times["its BGP sessions are up"]
    lab.check(
        "after the head office's edge restarts, the site reaches the head office again by itself",
        back and 0 < gap < HQ_RESTART_WINDOW_S - 20,
        f"outage {gap} s ({lost} of {count} pings lost); seconds after the reboot command: {json.dumps(times)}; "
        f"traffic came back {after_bgp} s after the head office's BGP sessions were up (negative: before); "
        f"now on {detail}; {tail}",
    )
    lab.fact("head office restart", json.dumps({"outage_s": gap, "tunnels opened by": OPEN, **times}))
    intact, detail = tunnels_intact(lab)
    lab.check("both tunnels are as configured after the head office's restart", intact, detail)
    if KIND == vyos_sketch.VTI:
        for vm in (HQ, "fc-fw-edge"):
            sas = lab.shell(vm, "swanctl --list-sas 2>&1 | grep -E '^[a-z]|INSTALLED'")
            ike = {t.name: len(re.findall(rf"^{re.escape(t.name)}: #", sas, flags=re.M)) for t in model.TUNNELS}
            lab.fact(f"{vm} IKE SAs for each tunnel after the head office's restart", f"{json.dumps(ike)}: {sas[:600]}")


def faults(lab: Lab) -> None:
    """Silent faults on ShadeCast, and one link set down, with a ping running through each."""
    isp = model.BY_NAME["shadecast"]
    eth = {other: name for name, _link, other, _gateway, _own in vyos_sketch.uplinks(isp)}
    primary, backup = tif("fc-a"), tif("fc-b")
    detect_s = vyos_sketch.BFD_INTERVAL_MS * model.BFD_MULTIPLIER / 1000
    # A failover that waits for the BGP hold timer instead of BFD takes more
    # than the hold time less one keepalive; every outage below that bound came
    # from BFD.
    hold_bound = model.CORE_HOLD_S - model.CORE_KEEPALIVE_S
    states = {vm: bfd_sessions(lab, vm) for vm in (HQ, "fc-fw-edge")}
    lab.check(
        "every BFD session is up at the configured interval before the faults",
        all(ok for ok, _detail in states.values()),
        "; ".join(f"{vm}: {detail}" for vm, (_ok, detail) in states.items()),
    )

    # The site's ShadeCast uplink goes silent.
    count = start_ping(lab, "site-uplink", 60)
    blackhole(lab, "shadecast", eth["fc-fw-edge"])
    moved, detail = wait_tunnel(lab, backup, 40)
    lost, gap, tail = ping_result(lab, "site-uplink", count)
    lab.check(
        "the site moves to its second tunnel when its ShadeCast uplink goes silent",
        moved and 0 < gap < hold_bound,
        f"outage {gap} s ({lost} of {count} pings lost; BFD detects in {detect_s:.0f} s); now on {detail}; {tail}",
    )
    # The site's own internet access moves too: the failover route of the silent uplink goes.
    site_uplinks = vyos_sketch.uplinks(model.BY_NAME["fc-fw-edge"])
    on_second, routes = site_default_route(lab, "fc-fw-edge", site_uplinks[1][3], 30)
    fresh = lab.shell("fc-historian", f"ping -c 5 -i 0.2 -W 2 {model.HUB_ENDPOINT} 2>&1 | tail -2")
    lab.check(
        "the site's default route moves to its second uplink, and a new flow reaches a public address",
        on_second and " 0% packet loss" in fresh,
        f"default routes: {routes}; {' '.join(fresh.split())}",
    )
    count = start_ping(lab, "site-uplink-repair", 60)
    drops = heal(lab, "shadecast")
    back, detail = wait_tunnel(lab, primary, 50)
    on_first, routes = site_default_route(lab, "fc-fw-edge", site_uplinks[0][3], 30)
    lab.check("the site's default route returns to its first uplink after the repair", on_first, routes)
    lost, gap, tail = ping_result(lab, "site-uplink-repair", count)
    lab.check(
        "the site returns to its primary tunnel after the repair",
        back and gap <= 2.0,
        f"the fault dropped {drops} packets; outage on the way back {gap} s ({lost} lost); now on {detail}",
    )
    intact, detail = tunnels_intact(lab)
    lab.check("both tunnels are as configured after the site's uplink failed and came back", intact, detail)

    # The head office's ShadeCast uplink goes silent.
    event_log(lab, EVENT_ROUTERS, on=True)
    count = start_ping(lab, "hq-uplink", 60)
    faulted = time.time()
    blackhole(lab, "shadecast", eth["fr-fw-edge"])
    lost, gap, tail = ping_result(lab, "hq-uplink", count)
    rounds = [hq_uplink_events(lab, "hq-uplink", 1, gap, faulted)]
    best = best_path(lab, "shadecast", model.HQ_PREFIX)
    on = site_tunnel(lab)
    lab.check(
        "the site keeps the head office when the head office loses its ShadeCast uplink",
        0 < gap < hold_bound and best == "64501 64505" and on == primary,
        f"outage {gap} s ({lost} of {count} lost); the site stayed on {on}; "
        f"ShadeCast's best path to the head office is {best!r}",
    )
    intact, detail = tunnels_intact(lab)
    lab.check("both tunnels are as configured while the head office is on one ISP", intact, detail)
    count = start_ping(lab, "hq-uplink-repair", 60)
    drops = heal(lab, "shadecast")
    back, detail = wait_tunnel(lab, primary, 50)
    lost, gap, tail = ping_result(lab, "hq-uplink-repair", count)
    lab.check(
        "the repair costs nothing, and the site is on its primary tunnel",
        back and gap <= 2.0,
        f"the fault dropped {drops} packets; outage {gap} s ({lost} lost); now on {detail}",
    )

    # The same fault again and again: some IPsec runs lost 5 s to it, not 3.
    for number in range(2, HQ_UPLINK_ROUNDS + 1):
        settled = sessions_settled(lab)
        tag = f"hq-uplink-{number}"
        count = start_ping(lab, tag, 30)
        faulted = time.time()
        blackhole(lab, "shadecast", eth["fr-fw-edge"])
        lost, gap, tail = ping_result(lab, tag, count)
        entry = hq_uplink_events(lab, tag, number, gap, faulted)
        entry["sessions up before the fault"] = settled
        entry["site on"] = site_tunnel(lab)
        heal(lab, "shadecast")
        wait_tunnel(lab, primary, 50)
        rounds.append(entry)
    event_log(lab, EVENT_ROUTERS, on=False)
    gaps = [entry["outage_s"] for entry in rounds]
    long_rounds = [entry for entry in rounds if entry["outage_s"] >= LONG_OUTAGE_S]
    lab.fact("head office uplink fault, every round", json.dumps(rounds))
    # The second tunnel does not use ShadeCast in either direction, so this fault must not end its session.
    second = tunnel_named("fc-b")
    ended = [
        f"round {entry['round']}: {router}: {event}"
        for entry in rounds
        for router, address in ((HQ, second.site_address()), ("fc-fw-edge", second.hub_address()))
        for event in entry["sessions down"].get(router, [])
        if event.startswith(f"{address}:")
    ]
    lab.check(
        "the head office's ShadeCast fault never ends the BGP session of the site's second tunnel",
        not ended,
        "; ".join(ended) or f"the session of {second.name} stayed up in all {len(rounds)} rounds",
    )
    lab.check(
        f"in {HQ_UPLINK_ROUNDS} rounds of the head office's uplink fault, every outage is shorter than the hold time",
        all(0 < value < hold_bound for value in gaps),
        f"outages {gaps} s; {len(long_rounds)} of {len(rounds)} rounds reached {LONG_OUTAGE_S} s: "
        + json.dumps([{key: entry[key] for key in ("round", "outage_s", "sessions down")} for entry in long_rounds]),
    )

    # A loud fault: the link of the site's ShadeCast uplink goes down.
    first = tunnel_named("fc-a")
    uplink, own = site_uplink(first)
    other = next(name for name, *_rest in vyos_sketch.uplinks(model.BY_NAME["fc-fw-edge"]) if name != uplink)
    # Count what the first tunnel sends through the other uplink: WireGuard to
    # its port, IPsec from its address. Either would be the tunnel moving.
    lab.shell(
        "fc-fw-edge",
        "nft add table inet cmleak; "
        "nft add chain inet cmleak cm_out '{ type filter hook output priority -290; }'; "
        f"nft add rule inet cmleak cm_out oifname {other} udp dport {first.port} counter; "
        f"nft add rule inet cmleak cm_out oifname {other} ip saddr {own} counter; echo counting",
    )
    lab.shell("fc-fw-edge", f"ip link set {uplink} down; echo down")
    moved, detail = wait_tunnel(lab, backup, 40)
    lookup = lab.shell("fc-fw-edge", pin_lookup(first))
    # Long enough for WireGuard's keepalive and handshake retries, and for IKE's.
    leak = lab.shell(
        "fc-fw-edge", "sleep 20; nft list table inet cmleak 2>&1; nft delete table inet cmleak", wait_s=120
    )
    leaked = sum(int(count) for count in re.findall(r"counter packets (\d+)", leak))
    if KIND == vyos_sketch.VTI:
        # The counter matches by the tunnel's own address, so a tunnel that moved
        # with the other uplink's address would slip past it; the SA would show it.
        sas = lab.shell("fc-fw-edge", f"swanctl --list-sas --ike {first.name} 2>&1")
        locals_ = set(re.findall(r"local\s+'[^']*'\s+@\s+([0-9.]+)", sas))
        leaked += 0 if locals_ <= {own} else 1
        lab.fact("first tunnel's IKE SA with its link down", " ".join(sas.split())[:400])
    lab.check(
        "with its uplink's link down, a tunnel does not move to the other uplink",
        moved and "counter packets" in leak and leaked == 0 and " dev eth" not in lookup,
        f"{leaked} of the first tunnel's packets left through {other} in 20 s; its route lookup now answers "
        f"`{' '.join(lookup.split())}` (a blackhole route answers Invalid argument); traffic on {detail}",
    )
    lab.shell("fc-fw-edge", f"ip link set {uplink} up; echo up")
    back, detail = wait_tunnel(lab, primary, 90)
    lab.check("the tunnel returns when the link does", back, detail)

    # A long silence on the site's ShadeCast uplink: longer than IKEv2 goes on
    # retransmitting before it gives a peer up (about 165 s with strongSwan's
    # defaults) and longer than a WireGuard session key is used (180 s).
    if KIND == vyos_sketch.WIREGUARD:
        tunnel_state = f"wg show {primary} latest-handshakes; date +%s"
    else:
        tunnel_state = f"swanctl --list-sas 2>&1 | grep -E '^[a-z]|INSTALLED'; ip -br link show {primary}"
    blackhole(lab, "shadecast", eth["fc-fw-edge"])
    moved, detail = wait_tunnel(lab, backup, 40)
    for _ in range(4):
        lab.shell("fc-fw-edge", "sleep 50; echo waited", wait_s=120)
    before = " ".join(lab.shell("fc-fw-edge", tunnel_state).split())
    drops = heal(lab, "shadecast")
    healed = time.monotonic()
    back, detail = wait_tunnel(lab, primary, 300)
    took = time.monotonic() - healed
    after = " ".join(lab.shell("fc-fw-edge", tunnel_state).split())
    lab.check(
        f"after {LONG_SILENCE_S} s of silence on its uplink, the first tunnel comes back by itself",
        moved and back,
        f"on {detail} {took:.0f} s after the repair (the fault dropped {drops} packets). "
        f"The first tunnel before the repair: {before[:500]}. After: {after[:500]}",
    )
    intact, detail = tunnels_intact(lab)
    lab.check("both tunnels are as configured after all the faults", intact, detail)


def run() -> int:
    opened_by_both = KIND == vyos_sketch.VTI and OPEN == "both"
    lab = Lab(f"wan-bgp-vyos-{KIND}{'-both' if opened_by_both else ''}", EXP)
    lab.redact = redact
    lab.versions()
    lab.fact("tunnels", KIND)
    if KIND == vyos_sketch.VTI:
        lab.fact("the IPsec tunnels are opened by", "both ends" if opened_by_both else "the site only")
    vyos_sketch.HUB_OPENS = opened_by_both
    vyos_sketch.reduce_lab()
    model.PIN = model.PIN_MARK
    # The generator's model, reduced the same way. With no transit carrier, each
    # ISP's default route depends on the prefix of the ISP that it peers with.
    generator_model.load()
    generator_model.HUB_OPENS = opened_by_both
    vyos_sketch.reduce_lab(generator_model)
    for isp, peer in (("shadecast", "tumbleweed"), ("tumbleweed", "shadecast")):
        generator_model.BY_NAME[isp].default_requires = list(generator_model.BY_NAME[peer].originates)
    wgkeys = load_wgkeys()

    def keys(tunnel: model.Tunnel, side: str) -> tuple[str, str]:
        other = "site" if side == "hub" else "hub"
        return (
            wgkeys.derive(KEY_SEED, f"{tunnel.name}-{side}").private_key,
            wgkeys.derive(KEY_SEED, f"{tunnel.name}-{other}").public_key,
        )

    def psk(tunnel: model.Tunnel) -> str:
        return hashlib.sha256(f"{KEY_SEED}/{tunnel.name}".encode()).hexdigest()[:40]

    routers = [router.name for router in model.ROUTERS]
    hosts = [lan.host for lan in model.LANS]
    configs = {router.name: generator.commands(router, keys, KIND, psk) for router in generator_model.ROUTERS}
    spike_only = {router.name: vyos_sketch.commands(router, keys, KIND, psk) for router in model.ROUTERS}
    lab.fact(
        "commands that the generator adds to the spike's lists",
        json.dumps(
            {name: len([line for line in lines if line not in spike_only[name]]) for name, lines in configs.items()}
        ),
    )
    WORK.mkdir(parents=True, exist_ok=True)
    topology_file, scenario_file = WORK / "topology.yml", WORK / "scenario.yml"
    topology_file.write_text(emit(topology()) + "\n")
    scenario_file.write_text(emit(scenario(configs)) + "\n")
    lab.save("topology.yml", topology_file.read_text())
    lab.save("scenario.yml", SECRET.sub(r"\1 'REDACTED'", scenario_file.read_text()))
    lab.fact("commands", json.dumps({name: len(lines) for name, lines in configs.items()}))

    try:
        lab.create_experiment(topology_file, scenario_file)
        lab.start_cli()
        lab.wait_running(*routers, *hosts, timeout_s=300)
        for vm in routers:
            lab.wait_cc_vm(vm, timeout_s=1200)
        lab.wait_cc(*hosts, timeout_s=600)

        # 1. What phenix rendered.
        for vm in routers:
            boot = BOOT_DIR / f"{vm}.boot"
            text = boot.read_text() if boot.exists() else ""
            lab.save(f"{vm}.boot", SECRET.sub(r"\1 'REDACTED'", text))
            extra = text.split("Extra config", 1)[1] if "Extra config" in text else ""
            lab.check(
                f"phenix rendered the config list of {vm} after its own configuration, before the commit",
                all(line in extra for line in configs[vm]) and extra.rstrip().endswith("commit\nsave\nexit"),
                f"{len(configs[vm])} commands; {boot}",
            )

        version = vyos_op(lab, "fr-fw-edge", "show version")
        lab.fact("fr-fw-edge show version", version)
        lab.fact(
            "fr-fw-edge kernel, FRR, WireGuard",
            lab.shell(
                "fr-fw-edge",
                "uname -r; vtysh -c 'show version' 2>&1 | head -1; "
                "modinfo wireguard 2>/dev/null | grep -E '^(filename|version|intree)' || echo 'wireguard: no module info'",
            ),
        )

        # 2. The commit at first boot. The boot script runs after the cc client registers, so poll.
        probe = (
            f"cat > /tmp/op.sh <<'OPEOF'\n#!/bin/vbash\n{VBASH}\nrun show configuration commands\nOPEOF\n"
            "chmod +x /tmp/op.sh; sg vyattacfg -c /tmp/op.sh 2>&1 | grep -c 'protocols bgp'; "
            "journalctl -u vyos-router --no-pager 2>/dev/null | grep -iE 'vyos.script|error|fail|commit' | tail -20"
        )
        committed: dict[str, bool] = {}
        for vm in routers:

            def applied(v: str = vm) -> tuple[bool, str]:
                out = lab.shell(v, probe, wait_s=180)
                return bool(re.match(r"\s*([1-9]\d*)", out)), out

            try:
                detail = wait_for(f"phenix's config on {vm}", 300, 10, applied)
                committed[vm] = True
            except TimeoutError as exc:
                detail, committed[vm] = str(exc), False
            lab.check(f"{vm} committed the rendered configuration at first boot", committed[vm], detail[:600])
            if committed[vm]:
                missing = config_missing(lab, vm, configs[vm])
                lab.check(
                    f"{vm} holds every line of its config list after the commit",
                    not missing,
                    f"{len(missing)} of {len(configs[vm])} lines missing: {redact(json.dumps(missing[:8]))}"
                    if missing
                    else f"all {len(configs[vm])} lines are in `show configuration commands`",
                )
            if not committed[vm]:
                # Find out which lines VyOS refuses, and leave the router configured if it takes the rest.
                lab.put_file(vm, "/tmp/vyos_commit_check.sh", HERE / "vyos_commit_check.sh")
                lab.put_file(vm, "/tmp/generated.sh", BOOT_DIR / f"{vm}.boot")
                out = lab.shell(
                    vm, "sg vyattacfg -c 'vbash /tmp/vyos_commit_check.sh /tmp/generated.sh' 2>&1", wait_s=600
                )
                lab.save(f"commit-check-{vm}.txt", SECRET.sub(r"\1 'REDACTED'", out))
                lab.fact(f"{vm} commit check", SECRET.sub(r"\1 'REDACTED'", out)[-3500:])

        # 3. BGP.
        for router in model.ROUTERS:
            want = expected_sessions(router)

            def established(name: str = router.name, count: int = want) -> tuple[bool, str]:
                states = bgp_states(lab, name)
                return len(states) == count and set(states.values()) == {"Established"}, json.dumps(states)

            try:
                detail, ok = wait_for(f"BGP on {router.name}", 240, 10, established), True
            except TimeoutError as exc:
                detail, ok = str(exc), False
            lab.check(f"all {want} BGP sessions of {router.name} are Established", ok, detail[:900])

        # An ISP sends its default route once it holds the other ISP's prefix.
        try:
            wait_for("both default routes at the head office", 120, 5, lambda: (not hq_defaults(lab)[1], ""))
        except TimeoutError:
            pass
        default, missing = hq_defaults(lab)
        both_defaults = lab.check(
            "the head office installs a default route through each ISP, with its endpoint as the source",
            not missing and default.count("nexthop via") == 2 and f"src {model.HUB_ENDPOINT}" in default,
            " ".join(default.split()),
        )
        # Without both routes the faults below measure something else, so try to get the route back.
        recovered = bool(missing) and explain_missing_default(lab, missing)
        paths = lab.shell(
            "shadecast",
            f"vtysh -c 'show bgp ipv4 unicast {model.HQ_PREFIX}' 2>&1 | head -20; "
            "vtysh -c 'show bgp ipv4 unicast 198.18.101.0/24' 2>&1 | head -12",
        )
        lab.fact("shadecast's routes to the head office and to Tumbleweed", paths)

        # 4. The tunnels and the pin.
        site = "fc-fw-edge"
        rules = lab.shell(site, "ip rule 2>&1")
        for tunnel in model.TUNNELS:
            eth, own = site_uplink(tunnel)
            got = lab.shell(site, pin_lookup(tunnel))
            rule = f"fwmark {hex(tunnel.mark())}" if KIND == vyos_sketch.WIREGUARD else f"from {own}"
            lab.check(
                f"the packets of {tunnel.name} leave through {eth}, the uplink to {tunnel.via}",
                f"dev {eth} " in got and rule in rules,
                f"wants {eth} by the rule `{rule}`: {' '.join(got.split())}",
            )
        lab.fact("fc-fw-edge ip rule", rules)
        if KIND == vyos_sketch.WIREGUARD:
            wg = lab.shell(
                site, "wg show all endpoints 2>&1; wg show all latest-handshakes 2>&1; wg show all fwmark 2>&1"
            )
            lab.fact("fc-fw-edge wg", wg)
            handshakes = [int(stamp) for stamp in re.findall(r"wg\d+\s+\S+\s+(\d+)\s*$", wg, flags=re.M)]
            intact, detail = tunnels_intact(lab)
            lab.check(
                "both tunnels have a handshake, to the endpoint the site was given",
                intact and len([stamp for stamp in handshakes if stamp > 0]) >= len(model.TUNNELS),
                f"endpoints {detail}, handshakes {handshakes}",
            )
            lab.fact("fr-fw-edge wg", lab.shell("fr-fw-edge", "wg show all endpoints 2>&1"))
        else:
            for vm in ("fr-fw-edge", site):
                lab.fact(f"{vm} show vpn ipsec sa", vyos_op(lab, vm, "show vpn ipsec sa"))
            intact, detail = tunnels_intact(lab)
            lab.check("both VTIs are up, each with its IPsec SA installed", intact, detail)
        route = lab.shell(site, "ip route get 10.20.30.11 2>&1; ip route show 10.20.0.0/16 2>&1")
        lab.check(
            "the site reaches the head office's block through its primary tunnel",
            f"dev {tif('fc-a')}" in route,
            " ".join(route.split()),
        )
        back = lab.shell("fr-fw-edge", "ip route show 10.21.0.0/16 2>&1")
        lab.check(
            "the head office reaches the site's block through the same tunnel",
            f"dev {tif('fc-a')}" in back,
            back.strip(),
        )
        lab.fact("fc-fw-edge bfd", lab.shell(site, "vtysh -c 'show bfd peers brief' 2>&1"))
        edge_sources(lab, site)
        tunnel_mtu(lab, site)

        # 5. End to end.
        ping = lab.shell("fc-historian", "ping -c 10 -i 0.2 -W 2 10.20.30.11 2>&1 | tail -3")
        lab.check("a host at the site reaches a host at the head office", " 0% packet loss" in ping, ping.strip())
        private_exits(lab, (HQ, site))
        foreign_prefix(lab, site)

        # 6. The tunnel's source filter, and NAT at both edges.
        source_filter(lab, site)
        internet_access(lab, site)
        tunnel_return_paths(lab)

        # 7 to 9. Faults, when the baseline works (a default route that had to be asked for again counts).
        failed = [check.name for check in lab.evidence.checks if not check.passed]
        if not failed or (recovered and not both_defaults and len(failed) == 1):
            faults(lab)
            hq_restart(lab, configs[HQ])
            default_condition(lab)
        else:
            lab.check("the faults ran", False, "skipped: a check before them failed")

        for vm in routers:
            lab.save(
                f"state-{vm}.txt",
                lab.shell(
                    vm,
                    "for c in 'show bgp ipv4 unicast summary' 'show bgp ipv4 unicast' 'show ip route' "
                    '\'show bfd peers\'; do echo "== $c"; vtysh -c "$c" 2>&1; done; '
                    "echo '== ip rule'; ip rule; echo '== ip route show table all'; "
                    "ip route show table all | grep -v ' table local' | head -60; echo '== wg show'; "
                    "wg show 2>&1 | grep -v 'private key'; echo '== swanctl --list-sas'; "
                    "swanctl --list-sas 2>&1 | head -60; ip -br link show type xfrm 2>&1; "
                    f"echo '== journal'; {JOURNAL}",
                    wait_s=180,
                ),
            )
        log("done")
    finally:
        lab.collect()
        lab.stop_experiment()
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
