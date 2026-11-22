#!/usr/bin/env python3
"""CI driver for the wan-bgp spike on VyOS: the lab's WAN on four real routers.

Run as root on a runner prepared by .github/actions/cm-lab, with phenix
patched by patches/vrouter-config-passthrough.ci-e2e-minimega.patch:
  sudo -E /opt/cm-spike-venv/bin/python vyos_ci.py

Four VyOS routers and two hosts boot under phenix and minimega: two ISPs that
peer (shadecast, tumbleweed), the head office's edge on both (fr-fw-edge), and
the High Country edge on both (hc-fw-edge). Every router gets the commands of
vyos_sketch.py as the `config` list of the vrouter app, so phenix writes them
into the script the image runs at first boot.

Checked, in order:
  1. phenix renders the list after its own configuration, before the commit;
  2. every router commits it at first boot (when one does not,
     vyos_commit_check.sh names the lines it refuses);
  3. BGP between the ISPs and to the head office, the head office's two
     default routes and their preferred source;
  4. the two WireGuard tunnels, the mark that ties each to an uplink, and BGP
     with BFD inside them;
  5. a host at the site reaches a host at the head office through the tunnel;
  6. silent faults on ShadeCast (the site's uplink, then the head office's) and
     the site's uplink set down: where the traffic goes, how long it takes, and
     whether a tunnel endpoint moves.
"""

from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))
sys.path.insert(0, str(HERE))

from vmlab import Lab, log, main_guard, wait_for  # noqa: E402

import netns_lab as model  # noqa: E402
import vyos_sketch  # noqa: E402

EXP: str = "spike-wan-bgp"
BOOT_DIR: Path = Path("/phenix/experiments") / EXP / "vrouter"
WORK: Path = Path("/tmp/cm-wan-bgp-vyos")
WGKEYS: Path = HERE.parents[1] / "tools" / "plantgen" / "plantgen" / "wgkeys.py"
# Anyone can derive these keys from this seed: they protect nothing but this run.
KEY_SEED: str = "cherrymountain-wan-bgp-spike-only"
VBASH: str = "source /opt/vyatta/etc/functions/script-template"
SECRET: re.Pattern[str] = re.compile(r"(private-key|public-key|secret) '[^']+'")
# The tunnels: `wireguard`, or `vti` for route-based IPsec.
KIND: str = os.environ.get("CM_TUNNEL", vyos_sketch.WIREGUARD)


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
        "hc-historian",
        f"setsid nohup ping -i {PING_INTERVAL_S} -W 1 -c {count} 10.20.30.11 >/tmp/ping-{tag}.log 2>&1 </dev/null & "
        "sleep 1; echo started",
    )
    return count


def ping_result(lab: Lab, tag: str, count: int) -> tuple[int, float, str]:
    """(lost, longest gap in seconds, the summary lines) of a ping that has finished."""

    def finished() -> tuple[bool, str]:
        out = lab.shell("hc-historian", f"tail -3 /tmp/ping-{tag}.log")
        return "packets transmitted" in out, out

    tail = wait_for(f"ping {tag}", count * PING_INTERVAL_S + 60, 5, finished)
    seqs = lab.shell(
        "hc-historian", f"grep -o 'icmp_seq=[0-9]* ttl' /tmp/ping-{tag}.log | grep -o '[0-9]*' | tr '\\n' ' '"
    )
    seen = sorted({int(seq) for seq in seqs.split()})
    longest, previous = 0, 0
    for seq in [*seen, count + 1]:
        longest, previous = max(longest, seq - previous - 1), seq
    return count - len(seen), round(longest * PING_INTERVAL_S, 1), " ".join(tail.split())


def site_tunnel(lab: Lab) -> str:
    """The interface the site uses toward the head office's block."""
    out = lab.shell("hc-fw-edge", "ip route show 10.20.0.0/16 2>&1")
    match = re.search(r"dev (\S+)", out)
    return match.group(1) if match else f"(none: {out.strip()[:80]})"


def tunnels_intact(lab: Lab) -> tuple[bool, str]:
    """WireGuard: no endpoint moved off the head office's address. VTI: both interfaces and both SAs are up."""
    if KIND == vyos_sketch.WIREGUARD:
        out = lab.shell("hc-fw-edge", "wg show all endpoints 2>&1")
        seen = dict(re.findall(r"(wg\d+)\s+\S+\s+(\S+:\d+)", out))
        wanted = {tif(tunnel.name): f"{model.HUB_ENDPOINT}:{tunnel.port}" for tunnel in model.TUNNELS}
        return seen == wanted, json.dumps(seen)
    out = lab.shell(
        "hc-fw-edge",
        "for i in " + " ".join(tif(tunnel.name) for tunnel in model.TUNNELS) + "; do ip -br link show $i; done 2>&1; "
        "echo sas $(swanctl --list-sas 2>/dev/null | grep -c INSTALLED)",
    )
    up = len(re.findall(r"<[^>]*\bUP\b[^>]*>", out))
    sas = int(match.group(1)) if (match := re.search(r"sas (\d+)", out)) else 0
    return up == len(model.TUNNELS) and sas >= len(model.TUNNELS), " ".join(out.split())


def wait_tunnel(lab: Lab, want: str, timeout_s: float) -> tuple[bool, str]:
    try:
        return True, wait_for(f"the site on {want}", timeout_s, 3, lambda: ((got := site_tunnel(lab)) == want, got))
    except TimeoutError as exc:
        return False, str(exc)[-200:]


def faults(lab: Lab) -> None:
    """Silent faults on ShadeCast, and one link set down, with a ping running through each."""
    isp = model.BY_NAME["shadecast"]
    eth = {other: name for name, _link, other, _gateway, _own in vyos_sketch.uplinks(isp)}
    primary, backup = tif("hc-a"), tif("hc-b")
    detect_s = vyos_sketch.BFD_INTERVAL_MS * model.BFD_MULTIPLIER / 1000

    # The site's ShadeCast uplink goes silent.
    count = start_ping(lab, "site-uplink", 60)
    blackhole(lab, "shadecast", eth["hc-fw-edge"])
    moved, detail = wait_tunnel(lab, backup, 40)
    lost, gap, tail = ping_result(lab, "site-uplink", count)
    lab.check(
        "the site moves to its second tunnel when its ShadeCast uplink goes silent",
        moved and 0 < gap <= detect_s + 6,
        f"outage {gap} s ({lost} of {count} pings lost; BFD detects in {detect_s:.0f} s); now on {detail}; {tail}",
    )
    count = start_ping(lab, "site-uplink-repair", 60)
    drops = heal(lab, "shadecast")
    back, detail = wait_tunnel(lab, primary, 50)
    lost, gap, tail = ping_result(lab, "site-uplink-repair", count)
    lab.check(
        "the site returns to its primary tunnel after the repair",
        back and gap <= 2.0,
        f"the fault dropped {drops} packets; outage on the way back {gap} s ({lost} lost); now on {detail}",
    )
    intact, detail = tunnels_intact(lab)
    lab.check("both tunnels are as configured after the site's uplink failed and came back", intact, detail)

    # The head office's ShadeCast uplink goes silent.
    count = start_ping(lab, "hq-uplink", 60)
    blackhole(lab, "shadecast", eth["fr-fw-edge"])
    lost, gap, tail = ping_result(lab, "hq-uplink", count)
    path = lab.shell(
        "shadecast", f"vtysh -c 'show bgp ipv4 unicast {model.HQ_PREFIX}' 2>&1 | grep -A1 -E '^  [0-9]' | head -6"
    )
    lab.check(
        "the site keeps the head office when the head office loses its ShadeCast uplink",
        0 < gap <= detect_s + 8 and "64501 64505" in path,
        f"outage {gap} s ({lost} of {count} lost); the site is on {site_tunnel(lab)}; "
        f"ShadeCast's route: {' '.join(path.split())[:200]}",
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

    # A loud fault: the link of the site's ShadeCast uplink goes down.
    first = tunnel_named("hc-a")
    uplink, own = site_uplink(first)
    other = next(name for name, *_rest in vyos_sketch.uplinks(model.BY_NAME["hc-fw-edge"]) if name != uplink)
    # Count what the first tunnel sends through the other uplink: WireGuard to
    # its port, IPsec from its address. Either would be the tunnel moving.
    lab.shell(
        "hc-fw-edge",
        "nft add table inet cmleak; "
        "nft add chain inet cmleak cm_out '{ type filter hook output priority -290; }'; "
        f"nft add rule inet cmleak cm_out oifname {other} udp dport {first.port} counter; "
        f"nft add rule inet cmleak cm_out oifname {other} ip saddr {own} counter; echo counting",
    )
    lab.shell("hc-fw-edge", f"ip link set {uplink} down; echo down")
    moved, detail = wait_tunnel(lab, backup, 40)
    lookup = lab.shell("hc-fw-edge", pin_lookup(first))
    # Long enough for WireGuard's keepalive and handshake retries, and for IKE's.
    leak = lab.shell("hc-fw-edge", "sleep 20; nft list table inet cmleak 2>&1; nft delete table inet cmleak", wait_s=120)
    leaked = sum(int(count) for count in re.findall(r"counter packets (\d+)", leak))
    lab.check(
        "with its uplink's link down, a tunnel does not move to the other uplink",
        moved and "counter packets" in leak and leaked == 0 and " dev eth" not in lookup,
        f"{leaked} of the first tunnel's packets left through {other} in 20 s; its route lookup now answers "
        f"`{' '.join(lookup.split())}` (a blackhole route answers Invalid argument); traffic on {detail}",
    )
    lab.shell("hc-fw-edge", f"ip link set {uplink} up; echo up")
    back, detail = wait_tunnel(lab, primary, 90)
    lab.check("the tunnel returns when the link does", back, detail)
    intact, detail = tunnels_intact(lab)
    lab.check("both tunnels are as configured after all the faults", intact, detail)


def run() -> int:
    lab = Lab(f"wan-bgp-vyos-{KIND}", EXP)
    lab.versions()
    lab.fact("tunnels", KIND)
    vyos_sketch.reduce_lab()
    model.PIN = model.PIN_MARK
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
    configs = {router.name: vyos_sketch.commands(router, keys, KIND, psk) for router in model.ROUTERS}
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
        site = "hc-fw-edge"
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
        lab.fact("hc-fw-edge ip rule", rules)
        if KIND == vyos_sketch.WIREGUARD:
            wg = lab.shell(
                site, "wg show all endpoints 2>&1; wg show all latest-handshakes 2>&1; wg show all fwmark 2>&1"
            )
            lab.fact("hc-fw-edge wg", wg)
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
            f"dev {tif('hc-a')}" in route,
            " ".join(route.split()),
        )
        back = lab.shell("fr-fw-edge", "ip route show 10.21.0.0/16 2>&1")
        lab.check(
            "the head office reaches the site's block through the same tunnel",
            f"dev {tif('hc-a')}" in back,
            back.strip(),
        )
        lab.fact("hc-fw-edge bfd", lab.shell(site, "vtysh -c 'show bfd peers brief' 2>&1"))

        # 5. End to end.
        ping = lab.shell("hc-historian", "ping -c 10 -i 0.2 -W 2 10.20.30.11 2>&1 | tail -3")
        lab.check("a host at the site reaches a host at the head office", " 0% packet loss" in ping, ping.strip())

        # 6. Faults, when the baseline works (a default route that had to be asked for again counts).
        failed = [check.name for check in lab.evidence.checks if not check.passed]
        if not failed or (recovered and not both_defaults and len(failed) == 1):
            faults(lab)
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
