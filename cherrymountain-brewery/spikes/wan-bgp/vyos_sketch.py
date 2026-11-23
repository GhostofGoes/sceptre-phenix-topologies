#!/usr/bin/env python3
"""Write the lab's routers as VyOS configuration commands.

  python vyos_sketch.py            # writes vyos-sketch/<router>.set

The output is what `netns_lab.py` configures on each router with FRR,
`ip rule`, and `wg`, said in VyOS's own commands: one file per router, one
`set` command per line, in the form the `config` list of the patched `vrouter`
app takes (patches/). `vyos_ci.py` gives these commands to real VyOS routers.

Every command path was read in the vyos-1x source (branch `rolling`, commit
`f1789c0d`, 2026-11-21) and its smoke tests. README.md says which of them a
VyOS router has taken. Interface numbers (`eth0`, `eth1`) follow the order of
the lab's links, and the committed files hold placeholders for the keys.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from pathlib import Path

import netns_lab as lab

OUT: Path = Path(__file__).resolve().parent / "vyos-sketch"
# A VM that waits for a CPU must not look like a dead link: one second, three
# missed. The lab's namespaces use 300 ms.
BFD_INTERVAL_MS: int = 1000

WIREGUARD: str = "wireguard"
# Route-based IPsec: IKEv2 with a pre-shared key, and a VTI for each tunnel.
VTI: str = "vti"
# A VTI's MTU: 1,500 less the most ESP adds here (outer header 20, ESP header
# 8, IV 16, padding up to 15, trailer 2, ICV 16), rounded down.
VTI_MTU: int = 1400

# (tunnel, "hub" or "site") -> (this side's private key, the other side's public key)
Keys = Callable[[lab.Tunnel, str], tuple[str, str]]
# tunnel -> its pre-shared key
PreSharedKey = Callable[[lab.Tunnel], str]


def placeholder_keys(_tunnel: lab.Tunnel, _side: str) -> tuple[str, str]:
    return "PRIVATE-KEY-OF-THIS-ROUTER", "PUBLIC-KEY-OF-THE-PEER"


def placeholder_psk(_tunnel: lab.Tunnel) -> str:
    return "PRE-SHARED-KEY-OF-THIS-TUNNEL"


def tunnel_if(kind: str, tunnel: lab.Tunnel) -> str:
    """The name of a tunnel's interface on a VyOS router."""
    return f"{'wg' if kind == WIREGUARD else 'vti'}{tunnel.mark()}"


def reduce_lab() -> None:
    """Shrink the lab's model to what a GitHub runner boots as VMs.

    Two ISPs that peer, the head office on both, and the Fort Collins site on
    both. Tumbleweed stands in for Marmot as the site's second ISP, so there is
    no transit carrier and no third ISP.
    """
    keep = ("shadecast", "tumbleweed", "fr-fw-edge", "fc-fw-edge")
    lab.ROUTERS[:] = [router for router in lab.ROUTERS if router.name in keep]
    lab.BY_NAME.clear()
    lab.BY_NAME.update({router.name: router for router in lab.ROUTERS})
    lab.LINKS[:] = [link for link in lab.LINKS if link.a in keep and link.b in keep]
    lab.LINKS.append(lab.Link(a="tumbleweed", b="fc-fw-edge", net="198.18.101.8/30", relation="static"))
    tunnels = {tunnel.name: tunnel for tunnel in lab.TUNNELS}
    lab.TUNNELS[:] = [tunnels["fc-a"], tunnels["fc-b"].model_copy(update={"via": "tumbleweed"})]
    lab.LANS[:] = [lan for lan in lab.LANS if lan.router in keep]


def uplinks(router: lab.Router) -> list[tuple[str, lab.Link, str, str, str]]:
    """(ethN, link, neighbor, the neighbor's address, this router's address) for each link."""
    found = []
    for link in lab.LINKS:
        if router.name in (link.a, link.b):
            other, gateway, own = lab.peer_of(link, router.name)
            found.append((f"eth{len(found)}", link, other, gateway, own))
    return found


def site_blocks(router: lab.Router) -> list[str]:
    """The private blocks of the site behind `router` (PLANS.md section 3)."""
    return lab.SITE_BLOCKS[router.name]


def site_code(name: str) -> str:
    return name.split("-")[0].upper()


def site_prefix_lists(names: list[str]) -> list[str]:
    """`SITE-<CODE>` with the blocks of each named site, and `SITE-NETS` with all of them."""
    lines: list[str] = []
    rule = 10
    for name in names:
        for index, block in enumerate(lab.SITE_BLOCKS[name], start=1):
            le = 0 if block.endswith("/24") else 24
            lines += prefix_list(f"SITE-{site_code(name)}", block, le=le, rule=index * 10)
            lines += prefix_list("SITE-NETS", block, le=le, rule=rule)
            rule += 10
    return lines


def prefix_list(name: str, prefix: str, ge: int = 0, le: int = 0, rule: int = 10, action: str = "permit") -> list[str]:
    lines = [
        f"set policy prefix-list {name} rule {rule} action {action}",
        f"set policy prefix-list {name} rule {rule} prefix {prefix}",
    ]
    if ge:
        lines.append(f"set policy prefix-list {name} rule {rule} ge {ge}")
    if le:
        lines.append(f"set policy prefix-list {name} rule {rule} le {le}")
    return lines


def route_map(
    name: str,
    prefix_list_name: str = "",
    local_preference: int = 0,
    source: str = "",
    community: str = "",
    rule: int = 10,
) -> list[str]:
    base = f"set policy route-map {name} rule {rule}"
    lines = [f"{base} action permit"]
    if prefix_list_name:
        lines.append(f"{base} match ip address prefix-list {prefix_list_name}")
    if local_preference:
        lines.append(f"{base} set local-preference {local_preference}")
    if community:
        lines.append(f"{base} set community replace {community}")
    if source:
        lines.append(f"{base} set src {source}")
    return lines


def bfd_profile() -> list[str]:
    return [
        f"set protocols bfd profile WAN interval transmit {BFD_INTERVAL_MS}",
        f"set protocols bfd profile WAN interval receive {BFD_INTERVAL_MS}",
        f"set protocols bfd profile WAN interval multiplier {lab.BFD_MULTIPLIER}",
    ]


def neighbor(
    address: str, remote_as: int, description: str, route_in: str, route_out: str, bfd: bool = True
) -> list[str]:
    base = f"set protocols bgp neighbor {address}"
    return [
        f"{base} remote-as {remote_as}",
        f"{base} description '{description}'",
        # VyOS ignores one timer without the other.
        f"{base} timers keepalive {lab.CORE_KEEPALIVE_S}",
        f"{base} timers holdtime {lab.CORE_HOLD_S}",
        # FRR waits 120 s before it tries a lost neighbor again; a repaired link should not.
        f"{base} timers connect 5",
        *([f"{base} bfd profile WAN"] if bfd else []),
        f"{base} address-family ipv4-unicast route-map import {route_in}",
        f"{base} address-family ipv4-unicast route-map export {route_out}",
    ]


def isp_sketch(router: lab.Router) -> list[str]:
    """An ISP or transit router: the customer, peer, and provider rules of the lab."""
    asn = router.asn
    lines: list[str] = [
        *bfd_profile(),
        *[
            line
            for rule, block in enumerate(lab.PRIVATE, start=1)
            for line in prefix_list("PUBLIC", block, le=32, rule=rule, action="deny")
        ],
        *prefix_list("PUBLIC", "0.0.0.0/0", rule=10, action="deny"),
        *prefix_list("PUBLIC", "0.0.0.0/0", le=24, rule=15),
        "set policy community-list FROM-CUSTOMERS rule 10 action permit",
        # Anchored: FRR's `_` matches a space or either end, so 64500:1 does not
        # also match 64500:10 or 164500:1.
        f"set policy community-list FROM-CUSTOMERS rule 10 regex '_{asn}:{lab.RELATION_TAG['customer']}_'",
        # A peer or a provider hears this network's own prefixes and its
        # customers' prefixes, never a route learned from another peer or provider.
        "set policy route-map TO-UPSTREAM rule 10 action permit",
        "set policy route-map TO-UPSTREAM rule 10 match community community-list FROM-CUSTOMERS",
        *route_map("TO-UPSTREAM", "OWN", rule=20),
        *route_map("TO-CUSTOMER"),
        f"set protocols bgp system-as {asn}",
        f"set protocols bgp parameters router-id {router.router_id}",
        "set protocols bgp parameters log-neighbor-changes",
    ]
    for index, prefix in enumerate(router.originates or ["0.0.0.0/32"], start=1):
        lines += prefix_list("OWN", prefix, rule=index * 10, action="permit" if router.originates else "deny")
    for relation in ("customer", "peer", "provider"):
        lines += route_map(
            f"FROM-{relation.upper()}",
            "PUBLIC",
            local_preference=lab.RELATION_LOCAL_PREF[relation],
            community=f"{asn}:{lab.RELATION_TAG[relation]}",
        )
    for prefix in router.originates:
        lines += [
            f"set protocols bgp address-family ipv4-unicast network {prefix}",
            # An originated aggregate needs a route to stand on.
            f"set protocols static route {prefix} blackhole distance 250",
        ]
    for _eth, link, other, address, _own in uplinks(router):
        relation = lab.relation_seen_by(link, router.name)
        if relation == "static":
            continue
        remote_as = lab.BY_NAME[other].asn
        title = lab.BY_NAME[other].title
        if relation == "ibgp":
            base = f"set protocols bgp neighbor {address}"
            lines += [
                f"{base} remote-as {remote_as}",
                f"{base} description '{title}'",
                f"{base} timers keepalive {lab.CORE_KEEPALIVE_S}",
                f"{base} timers holdtime {lab.CORE_HOLD_S}",
                f"{base} timers connect 5",
                f"{base} address-family ipv4-unicast nexthop-self",
            ]
            continue
        inbound = f"FROM-{relation.upper()}"
        if relation == "customer" and link.customer_prefixes:
            # A customer with its own prefix: accept that prefix only, and give it a default route.
            name = other.upper()
            for index, prefix in enumerate(link.customer_prefixes, start=1):
                lines += prefix_list(f"CUST-{name}", prefix, rule=index * 10)
            inbound = f"FROM-CUST-{name}"
            lines += route_map(
                inbound,
                f"CUST-{name}",
                local_preference=lab.RELATION_LOCAL_PREF["customer"],
                community=f"{asn}:{lab.RELATION_TAG['customer']}",
            )
            lines.append(f"set protocols bgp neighbor {address} address-family ipv4-unicast default-originate")
        outbound = "TO-CUSTOMER" if relation == "customer" else "TO-UPSTREAM"
        lines += neighbor(address, remote_as, title, inbound, outbound, bfd=link.bfd)
    return lines


def ipsec_groups(at_hub: bool) -> list[str]:
    """The IKE and ESP groups of the VTI tunnels.

    A site opens its tunnels and opens them again when they end. The head
    office only answers: when both ends open tunnels, a tunnel can come back
    from a long silence with two IKE SAs.
    """
    return [
        # strongSwan would otherwise install a route for each tunnel's selectors.
        "set vpn ipsec options disable-route-autoinstall",
        "set vpn ipsec ike-group WAN key-exchange ikev2",
        "set vpn ipsec ike-group WAN lifetime 28800",
        f"set vpn ipsec ike-group WAN close-action {'none' if at_hub else 'start'}",
        f"set vpn ipsec ike-group WAN dead-peer-detection action {'clear' if at_hub else 'restart'}",
        "set vpn ipsec ike-group WAN dead-peer-detection interval 10",
        "set vpn ipsec ike-group WAN dead-peer-detection timeout 30",
        # MOBIKE would let a tunnel move to another address pair, as a WireGuard endpoint roams.
        "set vpn ipsec ike-group WAN disable-mobike",
        "set vpn ipsec ike-group WAN proposal 10 dh-group 14",
        "set vpn ipsec ike-group WAN proposal 10 encryption aes256",
        "set vpn ipsec ike-group WAN proposal 10 hash sha256",
        "set vpn ipsec esp-group WAN lifetime 3600",
        "set vpn ipsec esp-group WAN pfs dh-group14",
        "set vpn ipsec esp-group WAN proposal 10 encryption aes256",
        "set vpn ipsec esp-group WAN proposal 10 hash sha256",
    ]


def vti_tunnel(router: lab.Router, tunnel: lab.Tunnel, psk: PreSharedKey) -> list[str]:
    """One route-based IPsec tunnel on a site edge."""
    at_hub = router.name == tunnel.hub
    _isp, gateway, uplink = lab.peer_of(lab.link_of(tunnel.site, tunnel.via), tunnel.site)
    local, remote = (lab.HUB_ENDPOINT, uplink) if at_hub else (uplink, lab.HUB_ENDPOINT)
    own = tunnel.hub_address() if at_hub else tunnel.site_address()
    vti = tunnel_if(VTI, tunnel)
    peer = f"set vpn ipsec site-to-site peer {tunnel.name}"
    lines = [
        f"set interfaces vti {vti} description '{tunnel.name} over {lab.BY_NAME[tunnel.via].title}'",
        f"set interfaces vti {vti} address {own}/{ipaddress.ip_network(tunnel.net).prefixlen}",
        # Room for ESP in a 1,500-byte underlay (VTI_MTU), and TCP told so.
        f"set interfaces vti {vti} mtu {VTI_MTU}",
        f"set interfaces vti {vti} ip adjust-mss clamp-mss-to-pmtu",
        f"set vpn ipsec authentication psk {tunnel.name} id '{local}'",
        f"set vpn ipsec authentication psk {tunnel.name} id '{remote}'",
        f"set vpn ipsec authentication psk {tunnel.name} secret '{psk(tunnel)}'",
        f"{peer} authentication mode pre-shared-secret",
        f"{peer} authentication local-id '{local}'",
        f"{peer} authentication remote-id '{remote}'",
        # The site opens the tunnel; the head office answers.
        f"{peer} connection-type {'none' if at_hub else 'initiate'}",
        f"{peer} ike-group WAN",
        f"{peer} default-esp-group WAN",
        f"{peer} local-address {local}",
        f"{peer} remote-address {remote}",
        f"{peer} vti bind {vti}",
        f"{peer} vti esp-group WAN",
    ]
    if not at_hub:
        mark = tunnel.mark()
        lines += [
            # The pin: the tunnel's packets leave from this uplink's address, a
            # rule sends that source to its own table, and the table holds this
            # uplink and, behind it, a blackhole.
            f"set policy local-route rule {mark} source address {uplink}",
            f"set policy local-route rule {mark} set table {mark}",
            f"set protocols static table {mark} route 0.0.0.0/0 next-hop {gateway}",
            f"set protocols static table {mark} route 0.0.0.0/0 blackhole distance 250",
        ]
    return lines


def sketch(
    router: lab.Router, keys: Keys = placeholder_keys, kind: str = WIREGUARD, psk: PreSharedKey = placeholder_psk
) -> list[str]:
    """A site edge: the head office, or a site with ISP-assigned addresses."""
    hub = router.multipath
    # The sites this router has a tunnel with.
    peers = sorted({t.site if router.name == t.hub else t.hub for t in lab.TUNNELS if router.name in (t.hub, t.site)})
    lines: list[str] = [
        *bfd_profile(),
        # Every edge knows every site's blocks, whether or not it has a tunnel
        # to that site: a site reaches the others through the head office.
        *site_prefix_lists(list(lab.SITE_BLOCKS)),
    ]
    if hub:
        # The head office takes from each site only that site's blocks, and
        # gives every site the blocks of all of them.
        for name in peers:
            code = site_code(name)
            lines += route_map(f"OVERLAY-PRIMARY-{code}", f"SITE-{code}", local_preference=200)
            lines += route_map(f"OVERLAY-BACKUP-{code}", f"SITE-{code}", local_preference=100)
        lines += route_map("OVERLAY-OUT", "SITE-NETS")
    else:
        # A site takes any site's blocks from the head office, and gives it only its own.
        lines += route_map("OVERLAY-PRIMARY", "SITE-NETS", local_preference=200)
        lines += route_map("OVERLAY-BACKUP", "SITE-NETS", local_preference=100)
        lines += route_map("OVERLAY-OUT", f"SITE-{site_code(router.name)}")
    lines += [
        f"set protocols bgp system-as {router.asn}",
        f"set protocols bgp parameters router-id {router.router_id}",
        "set protocols bgp parameters log-neighbor-changes",
    ]
    for block in site_blocks(router):
        # The lab announces the one /24 it has behind each edge. A site of the
        # range announces its blocks (PLANS.md section 3). VyOS announces a
        # `network` whether or not a route for it is installed.
        lines.append(f"set protocols bgp address-family ipv4-unicast network {block}")

    if hub:
        # The head office: its own prefix to two ISPs, a default route from each.
        lines += [
            f"set interfaces dummy dum0 address {lab.HUB_ENDPOINT}/32",
            *prefix_list("DEFAULT", "0.0.0.0/0"),
            *prefix_list("OWN", lab.HQ_PREFIX),
            *route_map("DEFAULT-ONLY", "DEFAULT"),
            *route_map("OWN-ONLY", "OWN"),
            # What the head office sends on a default route carries the endpoint
            # address as its source, so a tunnel's endpoint never looks like it
            # moved.
            *route_map("SET-SOURCE", "DEFAULT", source=lab.HUB_ENDPOINT),
            "set protocols bgp parameters bestpath as-path multipath-relax",
            "set protocols bgp address-family ipv4-unicast maximum-paths ebgp 2",
            f"set protocols bgp address-family ipv4-unicast network {lab.HQ_PREFIX}",
            # An originated aggregate needs a route to stand on.
            f"set protocols static route {lab.HQ_PREFIX} blackhole distance 250",
        ]
        for _eth, _link, isp, gateway, _own in uplinks(router):
            lines += neighbor(gateway, lab.BY_NAME[isp].asn, lab.BY_NAME[isp].title, "DEFAULT-ONLY", "OWN-ONLY")
            # Keep what the ISP sent, so that a change of policy is applied to
            # the stored routes and asks the ISP for nothing: FRR leaves the
            # default route out of its answer to a route refresh that arrives
            # just after a session comes up, and the head office then drops it.
            lines.append(
                f"set protocols bgp neighbor {gateway} address-family ipv4-unicast soft-reconfiguration inbound"
            )
    else:
        # ISP-assigned addresses: a default route for each uplink, the first preferred.
        for index, (_eth, _link, _isp, gateway, _own) in enumerate(uplinks(router)):
            lines.append(f"set protocols static route 0.0.0.0/0 next-hop {gateway} distance {10 * (index + 1)}")
    # What the edge itself sends into a tunnel (its syslog, NTP, and DNS)
    # leaves from its LAN address, not from the tunnel's /31, so the far side
    # sees an address in the site's own blocks. Rule 20 leaves every other
    # route as it is.
    lan = next(lan for lan in lab.LANS if lan.router == router.name)
    lines += [
        *route_map("SET-SOURCE", "SITE-NETS", source=lan.gateway(), rule=15),
        "set policy route-map SET-SOURCE rule 20 action permit",
        "set system ip protocol bgp route-map SET-SOURCE",
    ]
    # An unused address in the edge's own blocks, and any private address while
    # no tunnel carries it, are dropped here instead of following the default
    # route out of an uplink unencrypted (lab.discards). The blackholes of the
    # own blocks also back their `network` statements.
    lines += [
        f"set protocols static route {block} blackhole distance {distance}"
        for block, distance in lab.discards(router.name)
    ]

    if kind == VTI:
        lines += ipsec_groups(hub)
    for tunnel in lab.TUNNELS:
        if router.name not in (tunnel.hub, tunnel.site):
            continue
        wg = tunnel_if(WIREGUARD, tunnel)
        at_hub = router.name == tunnel.hub
        own = tunnel.hub_address() if at_hub else tunnel.site_address()
        peer = tunnel.site_address() if at_hub else tunnel.hub_address()
        peer_as = lab.BY_NAME[tunnel.site if at_hub else tunnel.hub].asn
        prefix = ipaddress.ip_network(tunnel.net).prefixlen
        policy = "OVERLAY-PRIMARY" if tunnel.primary else "OVERLAY-BACKUP"
        if at_hub:
            policy += f"-{site_code(tunnel.site)}"
        if kind == VTI:
            lines += vti_tunnel(router, tunnel, psk)
            lines += neighbor(peer, peer_as, f"overlay {tunnel.name}", policy, "OVERLAY-OUT")
            continue
        private_key, public_key = keys(tunnel, "hub" if at_hub else "site")
        lines += [
            f"set interfaces wireguard {wg} description '{tunnel.name} over {lab.BY_NAME[tunnel.via].title}'",
            f"set interfaces wireguard {wg} address {own}/{prefix}",
            f"set interfaces wireguard {wg} private-key '{private_key}'",
            f"set interfaces wireguard {wg} peer {tunnel.name} public-key '{public_key}'",
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
                # own table, and the table holds this uplink and, behind it, a
                # blackhole, so that the tunnel never leaves by the other uplink.
                f"set interfaces wireguard {wg} fwmark {mark}",
                f"set policy local-route rule {mark} fwmark {mark}",
                f"set policy local-route rule {mark} set table {mark}",
                f"set protocols static table {mark} route 0.0.0.0/0 next-hop {gateway}",
                f"set protocols static table {mark} route 0.0.0.0/0 blackhole distance 250",
            ]
        lines += neighbor(peer, peer_as, f"overlay {tunnel.name}", policy, "OVERLAY-OUT")
    return lines


def is_site_edge(router: lab.Router) -> bool:
    return any(router.name in (tunnel.hub, tunnel.site) for tunnel in lab.TUNNELS)


def commands(
    router: lab.Router, keys: Keys = placeholder_keys, kind: str = WIREGUARD, psk: PreSharedKey = placeholder_psk
) -> list[str]:
    """The `config` list of one router, checked against the patch's rule for a line."""
    lines = sketch(router, keys, kind, psk) if is_site_edge(router) else isp_sketch(router)
    for line in lines:
        if why := refused(line):
            raise ValueError(f"{router.name}: `{line}` {why}")
    return lines


def refused(line: str) -> str:
    """Why the patched `vrouter` app would refuse a `config` line, or "" if it would not.

    The same rule as `checkVyosConfigLine` in the patch: `set` or `delete`, a
    next word that starts with a lowercase letter (VyOS hands `set -x` to the
    shell's own `set`), no control character, and nothing the shell acts on
    outside single quotes.
    """
    if not line.startswith(("set ", "delete ")):
        return "does not start with set or delete"
    rest = line.split(" ", 1)[1]
    if not rest or not ("a" <= rest[0] <= "z"):
        return "the word after the verb does not start with a lowercase letter"
    quoted = False
    for char in line:
        if ord(char) < 32 or ord(char) == 127:
            return "has a control character"
        if char == "'":
            quoted = not quoted
        elif not quoted and char in '"`$;|&<>(){}[]\\#!*?~':
            return f"has {char!r} outside single quotes"
    return "has an unclosed single quote" if quoted else ""


def main() -> int:
    lab.PIN = lab.PIN_MARK
    OUT.mkdir(exist_ok=True)
    for router in lab.ROUTERS:
        lines = commands(router)
        (OUT / f"{router.name}.set").write_text("\n".join(lines) + "\n")
        print(f"{router.name}: {len(lines)} commands")
        if is_site_edge(router):
            # The same edge with route-based IPsec in place of WireGuard.
            lines = commands(router, kind=VTI)
            (OUT / f"{router.name}.vti.set").write_text("\n".join(lines) + "\n")
            print(f"{router.name}, VTI: {len(lines)} commands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
