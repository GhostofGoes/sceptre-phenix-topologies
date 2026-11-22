#!/usr/bin/env python3
"""Write the lab's site edges as VyOS configuration commands.

  python vyos_sketch.py            # writes vyos-sketch/<router>.set

The output is what `netns_lab.py` configures on each site edge with FRR,
`ip rule`, and `wg`, said in VyOS's own commands: one file per router, one
`set` command per line, in the form the `config` list of the patched `vrouter`
app takes (patches/vrouter-config-passthrough.patch).

**No VyOS router has taken these files.** Every command path was read in the
vyos-1x source (branch `rolling`, commit `f1789c0d`, 2026-11-21) and its smoke
tests; none was run. README.md lists what the spike on VyOS VMs has to show.
Interface numbers (`eth0`, `eth1`) follow the order of the lab's links, and the
keys are placeholders.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import netns_lab as lab

OUT: Path = Path(__file__).resolve().parent / "vyos-sketch"
# A VM that waits for a CPU must not look like a dead link: one second, three
# missed. The lab's namespaces use 300 ms.
BFD_INTERVAL_MS: int = 1000
PRIVATE_KEY: str = "'PRIVATE-KEY-OF-THIS-ROUTER'"
PUBLIC_KEY: str = "'PUBLIC-KEY-OF-THE-PEER'"


def uplinks(router: lab.Router) -> list[tuple[str, lab.Link, str, str, str]]:
    """(ethN, link, ISP, the ISP's address, this router's address) for each uplink."""
    found = []
    for link in lab.LINKS:
        if router.name in (link.a, link.b):
            isp, gateway, own = lab.peer_of(link, router.name)
            found.append((f"eth{len(found)}", link, isp, gateway, own))
    return found


def prefix_list(name: str, prefix: str, ge: int = 0, le: int = 0) -> list[str]:
    lines = [
        f"set policy prefix-list {name} rule 10 action permit",
        f"set policy prefix-list {name} rule 10 prefix {prefix}",
    ]
    if ge:
        lines.append(f"set policy prefix-list {name} rule 10 ge {ge}")
    if le:
        lines.append(f"set policy prefix-list {name} rule 10 le {le}")
    return lines


def route_map(name: str, prefix_list_name: str = "", local_preference: int = 0, source: str = "") -> list[str]:
    lines = [f"set policy route-map {name} rule 10 action permit"]
    if prefix_list_name:
        lines.append(f"set policy route-map {name} rule 10 match ip address prefix-list {prefix_list_name}")
    if local_preference:
        lines.append(f"set policy route-map {name} rule 10 set local-preference {local_preference}")
    if source:
        lines.append(f"set policy route-map {name} rule 10 set src {source}")
    return lines


def neighbor(address: str, remote_as: int, description: str, route_in: str, route_out: str) -> list[str]:
    base = f"set protocols bgp neighbor {address}"
    return [
        f"{base} remote-as {remote_as}",
        f"{base} description '{description}'",
        # VyOS ignores one timer without the other.
        f"{base} timers keepalive {lab.CORE_KEEPALIVE_S}",
        f"{base} timers holdtime {lab.CORE_HOLD_S}",
        f"{base} bfd profile WAN",
        f"{base} address-family ipv4-unicast route-map import {route_in}",
        f"{base} address-family ipv4-unicast route-map export {route_out}",
    ]


def sketch(router: lab.Router) -> list[str]:
    hub = router.multipath
    lines: list[str] = [
        f"set protocols bfd profile WAN interval transmit {BFD_INTERVAL_MS}",
        f"set protocols bfd profile WAN interval receive {BFD_INTERVAL_MS}",
        f"set protocols bfd profile WAN interval multiplier {lab.BFD_MULTIPLIER}",
        *prefix_list("SITE-NETS", "10.0.0.0/8", ge=16, le=24),
        *route_map("OVERLAY-PRIMARY", "SITE-NETS", local_preference=200),
        *route_map("OVERLAY-BACKUP", "SITE-NETS", local_preference=100),
        *route_map("OVERLAY-OUT", "SITE-NETS"),
        f"set protocols bgp system-as {router.asn}",
        f"set protocols bgp parameters router-id {router.router_id}",
    ]
    for lan in lab.LANS:
        if lan.router == router.name:
            lines.append(f"set protocols bgp address-family ipv4-unicast network {lan.net}")

    if hub:
        # The head office: its own prefix to two ISPs, a default route from each.
        lines += [
            f"set interfaces dummy dum0 address {lab.HUB_ENDPOINT}/32",
            *prefix_list("DEFAULT", "0.0.0.0/0"),
            *prefix_list("OWN", lab.HQ_PREFIX),
            *route_map("DEFAULT-ONLY", "DEFAULT"),
            *route_map("OWN-ONLY", "OWN"),
            # A route learned from an ISP sends with the endpoint address as its
            # source, so a tunnel's endpoint never looks like it moved.
            *route_map("SET-SOURCE", source=lab.HUB_ENDPOINT),
            "set system ip protocol bgp route-map SET-SOURCE",
            "set protocols bgp parameters bestpath as-path multipath-relax",
            "set protocols bgp address-family ipv4-unicast maximum-paths ebgp 2",
            f"set protocols bgp address-family ipv4-unicast network {lab.HQ_PREFIX}",
            # An originated aggregate needs a route to stand on.
            f"set protocols static route {lab.HQ_PREFIX} blackhole distance 250",
        ]
        for _eth, _link, isp, gateway, _own in uplinks(router):
            lines += neighbor(gateway, lab.BY_NAME[isp].asn, lab.BY_NAME[isp].title, "DEFAULT-ONLY", "OWN-ONLY")
    else:
        # ISP-assigned addresses: a default route for each uplink, the first preferred.
        for index, (_eth, _link, _isp, gateway, _own) in enumerate(uplinks(router)):
            lines.append(f"set protocols static route 0.0.0.0/0 next-hop {gateway} distance {10 * (index + 1)}")

    for tunnel in lab.TUNNELS:
        if router.name not in (tunnel.hub, tunnel.site):
            continue
        wg = f"wg{tunnel.mark()}"
        at_hub = router.name == tunnel.hub
        own = tunnel.hub_address() if at_hub else tunnel.site_address()
        peer = tunnel.site_address() if at_hub else tunnel.hub_address()
        peer_as = lab.BY_NAME[tunnel.site if at_hub else tunnel.hub].asn
        prefix = ipaddress.ip_network(tunnel.net).prefixlen
        lines += [
            f"set interfaces wireguard {wg} description '{tunnel.name} over {lab.BY_NAME[tunnel.via].title}'",
            f"set interfaces wireguard {wg} address {own}/{prefix}",
            f"set interfaces wireguard {wg} private-key {PRIVATE_KEY}",
            f"set interfaces wireguard {wg} peer {tunnel.name} public-key {PUBLIC_KEY}",
            # BGP runs inside the tunnel, so WireGuard lets every address through.
            # VyOS installs no route for allowed-ips.
            f"set interfaces wireguard {wg} peer {tunnel.name} allowed-ips 0.0.0.0/0",
        ]
        if at_hub:
            lines.append(f"set interfaces wireguard {wg} port {tunnel.port}")
        else:
            _isp, gateway, _own = lab.peer_of(lab.link_of(router.name, tunnel.via), router.name)
            mark = tunnel.mark()
            lines += [
                f"set interfaces wireguard {wg} peer {tunnel.name} address {lab.HUB_ENDPOINT}",
                f"set interfaces wireguard {wg} peer {tunnel.name} port {tunnel.port}",
                f"set interfaces wireguard {wg} peer {tunnel.name} persistent-keepalive 5",
                # The pin: this tunnel's packets carry a mark, the mark has its
                # own table, and the table's only route is this uplink.
                f"set interfaces wireguard {wg} fwmark {mark}",
                f"set policy local-route rule {mark} fwmark {mark}",
                f"set policy local-route rule {mark} set table {mark}",
                f"set protocols static table {mark} route 0.0.0.0/0 next-hop {gateway}",
            ]
        policy = "OVERLAY-PRIMARY" if tunnel.primary else "OVERLAY-BACKUP"
        lines += neighbor(peer, peer_as, f"overlay {tunnel.name}", policy, "OVERLAY-OUT")
    return lines


def refused(line: str) -> str:
    """Why the patched `vrouter` app would refuse a `config` line, or "" if it would not.

    The same rule as `vyosConfigLines` in the patch: `set` or `delete`, no
    control character, and nothing the shell acts on outside single quotes.
    """
    if not line.startswith(("set ", "delete ")):
        return "does not start with set or delete"
    quoted = False
    for char in line:
        if ord(char) < 32 or ord(char) == 127:
            return "has a control character"
        if char == "'":
            quoted = not quoted
        elif not quoted and char in '"`$;|&<>(){}\\#!*?~':
            return f"has {char!r} outside single quotes"
    return "has an unclosed single quote" if quoted else ""


def main() -> int:
    lab.PIN = lab.PIN_MARK
    OUT.mkdir(exist_ok=True)
    for router in lab.ROUTERS:
        if not any(router.name in (t.hub, t.site) for t in lab.TUNNELS):
            continue
        for line in sketch(router):
            if why := refused(line):
                raise ValueError(f"{router.name}: `{line}` {why}")
        text = "\n".join(sketch(router)) + "\n"
        (OUT / f"{router.name}.set").write_text(text)
        print(f"{router.name}: {len(text.splitlines())} commands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
