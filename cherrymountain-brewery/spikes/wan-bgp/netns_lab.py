#!/usr/bin/env python3
"""Mock internet with BGP and a site-to-site VPN overlay, in network namespaces.

Needs root, iproute2, nftables, FRR (zebra, bgpd, bfdd, staticd, vtysh),
wireguard-tools, and either the kernel's WireGuard or wireguard-go:
  sudo -E python netns_lab.py            # build, test, tear down
  sudo -E python netns_lab.py --keep     # leave the lab up for a look around
  sudo -E python netns_lab.py --down     # tear a kept lab down
  sudo -E python netns_lab.py --pin destination   # the first design, which drifts

Every router is a namespace that runs its own FRR. The lab is the stand-in for
the multi-site range's WAN: four ISPs and a transit carrier that speak BGP, a
head office that is multihomed with its own AS and prefix, three more sites
with ISP-assigned addresses, and one WireGuard tunnel per site uplink to the
head office with eBGP and BFD inside the tunnels. README.md has the question,
the diagram, and the result.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))

from vmlab import Check, Evidence  # noqa: E402

EVIDENCE: Path = Path(os.environ.get("CM_EVIDENCE_DIR", "evidence")) / "wan-bgp-netns"
FRR_BIN: Path = Path(os.environ.get("FRR_BIN", "/usr/lib/frr"))
FRR_ETC: Path = Path("/etc/frr")
FRR_RUN: Path = Path("/var/run/frr")
WG_DIR: Path = Path("/run/cm-wan-bgp")
PING_INTERVAL_S: float = 0.1

# Underlay BGP timers. Real eBGP defaults to 60 s keepalive and 180 s hold; the
# lab uses short ones so that a silent failure in the ISP core ends in seconds.
CORE_KEEPALIVE_S: int = 3
CORE_HOLD_S: int = 9
BFD_INTERVAL_MS: int = 300
BFD_MULTIPLIER: int = 3

# How a site ties each tunnel to one uplink.
#   mark:        the tunnel's packets carry a firewall mark, and a rule sends that
#                mark to a routing table whose default route is the uplink. The
#                head office has one endpoint address and a port per tunnel.
#   destination: a static host route for the tunnel's endpoint, with a second
#                endpoint address at the head office for the backup tunnels. A
#                WireGuard endpoint roams, so this pin does not hold (README,
#                finding 2).
PIN_MARK: str = "mark"
PIN_DESTINATION: str = "destination"
PIN: str = PIN_MARK
HUB_ENDPOINT: str = "198.18.105.1"

RELATION_LOCAL_PREF: dict[str, int] = {"customer": 200, "peer": 150, "provider": 100}
RELATION_TAG: dict[str, int] = {"customer": 1, "peer": 2, "provider": 3}
INVERSE_RELATION: dict[str, str] = {"customer": "provider", "provider": "customer", "peer": "peer"}


class Router(BaseModel):
    """One namespace that runs FRR."""

    name: str
    # Short form for interface names (15 characters at most).
    short: str
    asn: int
    router_id: str
    title: str
    # Prefixes this AS originates into the internet.
    originates: list[str] = Field(default_factory=list)
    # Extra addresses on the loopback (the head office's VPN endpoints).
    loopbacks: list[str] = Field(default_factory=list)
    # False for a site edge that has ISP-assigned addresses and no BGP session
    # with its ISPs.
    public_bgp: bool = True
    # Install the default route of every ISP, not only the best one, so that a
    # route exists through each uplink.
    multipath: bool = False


class Link(BaseModel):
    """A point-to-point underlay link. `a` gets the first host address."""

    a: str
    b: str
    net: str
    # How `a` sees `b`: customer, peer, provider, ibgp, or static (no BGP).
    relation: str
    # With relation customer: the prefixes `a` accepts from `b`.
    customer_prefixes: list[str] = Field(default_factory=list)
    bfd: bool = False

    def addresses(self) -> tuple[str, str]:
        hosts = list(ipaddress.ip_network(self.net).hosts())
        return str(hosts[0]), str(hosts[1])

    def ifname(self, end: str) -> str:
        other = self.b if end == self.a else self.a
        return f"{BY_NAME[end].short}-{BY_NAME[other].short}"


class Lan(BaseModel):
    """A site LAN with one host on it."""

    router: str
    host: str
    net: str
    advertise_public: bool = False

    def gateway(self) -> str:
        return str(next(ipaddress.ip_network(self.net).hosts()))

    def host_address(self) -> str:
        return str(list(ipaddress.ip_network(self.net).hosts())[10])


class Tunnel(BaseModel):
    """One WireGuard tunnel from a site uplink to a head-office endpoint."""

    name: str
    site: str
    hub: str
    # The site's uplink that carries the tunnel, as the name of its ISP.
    via: str
    hub_endpoint: str
    port: int
    net: str
    primary: bool

    def endpoint(self) -> str:
        """The head-office address the site is configured to send to."""
        return HUB_ENDPOINT if PIN == PIN_MARK else self.hub_endpoint

    def mark(self) -> int:
        """The firewall mark and routing table of this tunnel at its site."""
        return self.port - 51720

    def hub_address(self) -> str:
        return str(ipaddress.ip_network(self.net)[0])

    def site_address(self) -> str:
        return str(ipaddress.ip_network(self.net)[1])

    def hub_if(self) -> str:
        return f"wgh-{self.name}"

    def site_if(self) -> str:
        return f"wgs-{self.name}"


ROUTERS: list[Router] = [
    Router(
        name="cb-west",
        short="cbw",
        asn=64496,
        router_id="198.18.0.251",
        title="Continental Backbone, Denver",
        originates=["198.18.0.0/24", "198.18.110.0/24"],
    ),
    Router(
        name="cb-south",
        short="cbs",
        asn=64496,
        router_id="198.18.0.252",
        title="Continental Backbone, Colorado Springs",
    ),
    Router(
        name="shadecast",
        short="sc",
        asn=64500,
        router_id="203.0.113.254",
        title="ShadeCast Cable",
        originates=["203.0.113.0/24"],
    ),
    Router(
        name="tumbleweed",
        short="tw",
        asn=64501,
        router_id="198.18.101.254",
        title="Tumbleweed Fiber",
        originates=["198.18.101.0/24"],
    ),
    Router(
        name="marmot",
        short="mm",
        asn=64502,
        router_id="198.18.102.254",
        title="Marmot Wireless",
        originates=["198.18.102.0/24"],
    ),
    Router(
        name="bristlecone",
        short="bc",
        asn=64503,
        router_id="198.18.103.254",
        title="Bristlecone Broadband",
        originates=["198.18.103.0/24"],
    ),
    Router(
        name="fr-fw-edge",
        short="fr",
        asn=64505,
        router_id="198.18.105.1",
        title="Head office and Front Range brewery edge",
        originates=["198.18.105.0/24"],
        loopbacks=["198.18.105.1/32", "198.18.105.2/32"],
        multipath=True,
    ),
    Router(
        name="fc-fw-edge",
        short="fc",
        asn=65020,
        router_id="10.255.2.1",
        title="Fort Collins brewpub edge",
        public_bgp=False,
    ),
    Router(
        name="ep-fw-edge",
        short="ep",
        asn=65030,
        router_id="10.255.3.1",
        title="Eastern Plains brewery edge",
        public_bgp=False,
    ),
    Router(
        name="cs-fw-edge",
        short="cs",
        asn=65040,
        router_id="10.255.4.1",
        title="Colorado Springs brewery edge",
        public_bgp=False,
    ),
]

HQ_PREFIX: str = "198.18.105.0/24"
HQ_LAN: str = "10.20.30.0/24"
FC_LAN: str = "10.21.10.0/24"
CS_LAN: str = "192.168.20.0/24"
# A /24 inside the head office's own block that nothing announces. A site that
# announces it would draw the head office's traffic for it into its tunnel.
HQ_MORE_SPECIFIC: str = "10.20.99.0/24"

# The private blocks of each site. The head office takes from a site only what
# is in that site's blocks, and a site announces only its own.
SITE_BLOCKS: dict[str, list[str]] = {
    "fr-fw-edge": ["10.10.0.0/16", "10.20.0.0/16", "10.30.0.0/16", "10.40.0.0/16"],
    "fc-fw-edge": ["10.11.0.0/16", "10.21.0.0/16"],
    "ep-fw-edge": ["10.12.0.0/16", "10.22.0.0/16", "10.32.0.0/16", "10.42.0.0/16"],
    "cs-fw-edge": ["192.168.10.0/24", "192.168.20.0/24", "192.168.30.0/24"],
}
PRIVATE: tuple[str, ...] = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")

LINKS: list[Link] = [
    Link(a="cb-west", b="cb-south", net="198.18.0.0/30", relation="ibgp"),
    Link(a="cb-west", b="shadecast", net="198.18.0.4/30", relation="customer"),
    Link(a="cb-west", b="tumbleweed", net="198.18.0.8/30", relation="customer"),
    Link(a="cb-south", b="bristlecone", net="198.18.0.12/30", relation="customer"),
    Link(a="shadecast", b="tumbleweed", net="198.18.0.16/30", relation="peer"),
    Link(a="tumbleweed", b="marmot", net="198.18.0.20/30", relation="customer"),
    Link(
        a="shadecast",
        b="fr-fw-edge",
        net="203.0.113.0/30",
        relation="customer",
        customer_prefixes=[HQ_PREFIX],
        bfd=True,
    ),
    Link(
        a="tumbleweed",
        b="fr-fw-edge",
        net="198.18.101.0/30",
        relation="customer",
        customer_prefixes=[HQ_PREFIX],
        bfd=True,
    ),
    Link(a="shadecast", b="fc-fw-edge", net="203.0.113.8/30", relation="static"),
    Link(a="marmot", b="fc-fw-edge", net="198.18.102.0/30", relation="static"),
    Link(a="tumbleweed", b="ep-fw-edge", net="198.18.101.8/30", relation="static"),
    Link(a="shadecast", b="ep-fw-edge", net="203.0.113.16/30", relation="static"),
    Link(a="bristlecone", b="cs-fw-edge", net="198.18.103.0/30", relation="static"),
]

LANS: list[Lan] = [
    Lan(router="fr-fw-edge", host="fr-historian", net=HQ_LAN),
    # The brewpub has one flat control network, and the site that was bought
    # keeps its own address plan (PLANS.md 4.1). The hosts keep the lab's names.
    Lan(router="fc-fw-edge", host="fc-historian", net=FC_LAN),
    Lan(router="ep-fw-edge", host="ep-historian", net="10.22.30.0/24"),
    Lan(router="cs-fw-edge", host="cs-historian", net=CS_LAN),
    Lan(router="cb-west", host="inet", net="198.18.110.0/24", advertise_public=True),
]

TUNNELS: list[Tunnel] = [
    Tunnel(
        name="fc-a",
        site="fc-fw-edge",
        hub="fr-fw-edge",
        via="shadecast",
        hub_endpoint="198.18.105.1",
        port=51821,
        net="10.255.0.0/31",
        primary=True,
    ),
    Tunnel(
        name="fc-b",
        site="fc-fw-edge",
        hub="fr-fw-edge",
        via="marmot",
        hub_endpoint="198.18.105.2",
        port=51822,
        net="10.255.0.2/31",
        primary=False,
    ),
    Tunnel(
        name="ep-a",
        site="ep-fw-edge",
        hub="fr-fw-edge",
        via="tumbleweed",
        hub_endpoint="198.18.105.1",
        port=51823,
        net="10.255.0.4/31",
        primary=True,
    ),
    Tunnel(
        name="ep-b",
        site="ep-fw-edge",
        hub="fr-fw-edge",
        via="shadecast",
        hub_endpoint="198.18.105.2",
        port=51824,
        net="10.255.0.6/31",
        primary=False,
    ),
    Tunnel(
        name="cs-a",
        site="cs-fw-edge",
        hub="fr-fw-edge",
        via="bristlecone",
        hub_endpoint="198.18.105.1",
        port=51825,
        net="10.255.0.8/31",
        primary=True,
    ),
]

BY_NAME: dict[str, Router] = {r.name: r for r in ROUTERS}
HOSTS: list[str] = [lan.host for lan in LANS]


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(argv: list[str], check: bool = True, timeout_s: float = 60.0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def ns(name: str, *argv: str, check: bool = True, timeout_s: float = 60.0) -> str:
    return run(["ip", "netns", "exec", name, *argv], check=check, timeout_s=timeout_s).stdout


def vtysh(name: str, command: str) -> str:
    return ns(name, "vtysh", "-N", name, "-c", command, check=False)


def vtysh_json(name: str, command: str) -> dict:
    out = vtysh(name, command)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def link_of(router: str, other: str) -> Link:
    for link in LINKS:
        if {link.a, link.b} == {router, other}:
            return link
    raise KeyError(f"no link between {router} and {other}")


def relation_seen_by(link: Link, router: str) -> str:
    if link.relation in {"ibgp", "static"}:
        return link.relation
    return link.relation if router == link.a else INVERSE_RELATION[link.relation]


def peer_of(link: Link, router: str) -> tuple[str, str, str]:
    """The neighbor's name, its address, and this router's own address on `link`."""
    first, second = link.addresses()
    if router == link.a:
        return link.b, second, first
    return link.a, first, second


# ------------------------------- FRR config -------------------------------


def site_prefix_lists() -> list[str]:
    """`SITE-<CODE>` for each site's blocks, and `SITE-NETS` for all of them: a /16 down to its /24s."""
    lines: list[str] = []
    seq = 5
    for name, blocks in SITE_BLOCKS.items():
        for number, block in enumerate(blocks, start=1):
            le = "" if block.endswith("/24") else " le 24"
            lines.append(f"ip prefix-list SITE-{name.split('-')[0].upper()} seq {number * 5} permit {block}{le}")
            lines.append(f"ip prefix-list SITE-NETS seq {seq} permit {block}{le}")
            seq += 5
    return lines


def discards(name: str) -> list[tuple[str, int]]:
    """The blackhole routes of a site edge or the head office, with their distances.

    An unused address in one of the edge's own blocks, and any private address
    while no tunnel carries it, are dropped here. Without them such traffic
    would follow the default route out of an uplink, unencrypted. Every more
    specific route (a LAN, a block learned through a tunnel, a tunnel's /31)
    still wins.
    """
    if name not in SITE_BLOCKS:
        return []
    return [(block, 250) for block in SITE_BLOCKS[name]] + [(block, 254) for block in PRIVATE]


def private_probes(name: str) -> list[str]:
    """Unused private addresses to ask an edge about: one in each of its own blocks, and one in each private range."""
    probes = []
    for block in SITE_BLOCKS[name]:
        network = ipaddress.ip_network(block)
        probes.append(str(network.network_address + (99 * 256 + 1 if network.prefixlen <= 16 else 250)))
    return probes + ["10.99.0.1", "172.20.0.1", "192.168.99.1"]


def private_exits(name: str) -> dict[str, str]:
    """The probes of private_probes that `name` would send out of an uplink, with the kernel's answer."""
    uplinks = [link.ifname(name) for link in LINKS if name in (link.a, link.b)]
    found = {}
    for probe in private_probes(name):
        proc = run(["ip", "netns", "exec", name, "ip", "route", "get", probe], check=False)
        answer = " ".join((proc.stdout + proc.stderr).split())
        if any(f"dev {ifname} " in answer + " " for ifname in uplinks):
            found[probe] = answer
    return found


def frr_config(router: Router) -> str:
    """The integrated frr.conf of one router."""
    lines: list[str] = [
        "frr defaults traditional",
        f"hostname {router.name}",
        f"log file {FRR_RUN / router.name / 'frr.log'}",
        "ip forwarding",
        "!",
    ]
    asn = router.asn
    if router.originates:
        for seq, prefix in enumerate(router.originates, start=1):
            lines.append(f"ip prefix-list OWN seq {seq * 5} permit {prefix}")
    else:
        lines.append("ip prefix-list OWN seq 5 deny 0.0.0.0/0 le 32")
    lines += [
        "ip prefix-list DEFAULT seq 5 permit 0.0.0.0/0",
        # The overlay carries the sites' private prefixes and nothing else: one
        # list for each site with that site's blocks, and one with all of them.
        *site_prefix_lists(),
        # What an ISP takes from another AS: public space, nothing longer than /24.
        *[f"ip prefix-list PUBLIC seq {seq} deny {block} le 32" for seq, block in enumerate(PRIVATE, start=1)],
        "ip prefix-list PUBLIC seq 10 deny 0.0.0.0/0",
        "ip prefix-list PUBLIC seq 15 permit 0.0.0.0/0 le 24",
        f"bgp community-list standard FROM-CUSTOMERS permit {asn}:{RELATION_TAG['customer']}",
        "!",
    ]
    for relation in ("customer", "peer", "provider"):
        lines += [
            f"route-map FROM-{relation.upper()} permit 10",
            " match ip address prefix-list PUBLIC",
            f" set local-preference {RELATION_LOCAL_PREF[relation]}",
            f" set community {asn}:{RELATION_TAG[relation]}",
            "!",
        ]
    lines += [
        # Valley-free export: a peer or provider hears this AS's own prefixes and
        # its customers' prefixes, never a route learned from another peer or
        # provider.
        "route-map TO-UPSTREAM permit 10",
        " match community FROM-CUSTOMERS",
        "route-map TO-UPSTREAM permit 20",
        " match ip address prefix-list OWN",
        "!",
        "route-map TO-CUSTOMER permit 10",
        "!",
        "route-map DEFAULT-ONLY permit 10",
        " match ip address prefix-list DEFAULT",
        "!",
        "route-map OWN-ONLY permit 10",
        " match ip address prefix-list OWN",
        "!",
    ]
    # A site takes any site's prefix from the head office and gives it only its
    # own. The head office takes from each site only that site's prefixes.
    hub = router.name == TUNNELS[0].hub
    for other in SITE_BLOCKS:
        if other != router.name and not hub:
            continue
        taken = f"SITE-{other.split('-')[0].upper()}" if other != router.name else "SITE-NETS"
        suffix = f"-{other.split('-')[0].upper()}" if other != router.name else ""
        lines += [
            f"route-map OVERLAY-PRIMARY{suffix} permit 10",
            f" match ip address prefix-list {taken}",
            " set local-preference 200",
            "!",
            f"route-map OVERLAY-BACKUP{suffix} permit 10",
            f" match ip address prefix-list {taken}",
            " set local-preference 100",
            "!",
        ]
    own = f"SITE-{router.name.split('-')[0].upper()}" if router.name in SITE_BLOCKS and not hub else "SITE-NETS"
    lines += [
        "route-map OVERLAY-OUT permit 10",
        f" match ip address prefix-list {own}",
        "!",
    ]

    neighbors: list[str] = []
    activate: list[str] = []
    bfd_peers: list[str] = []
    customer_maps: list[str] = []
    static_routes: list[str] = []

    for link in LINKS:
        if router.name not in (link.a, link.b):
            continue
        other, address, _own = peer_of(link, router.name)
        relation = relation_seen_by(link, router.name)
        if relation == "static":
            continue
        remote_as = BY_NAME[other].asn
        neighbors += [
            f" neighbor {address} remote-as {remote_as}",
            f" neighbor {address} description {other}",
            f" neighbor {address} timers {CORE_KEEPALIVE_S} {CORE_HOLD_S}",
            f" neighbor {address} timers connect 5",
        ]
        if link.bfd:
            neighbors.append(f" neighbor {address} bfd")
            bfd_peers.append(address)
        if relation == "ibgp":
            activate += [
                f"  neighbor {address} next-hop-self",
                f"  neighbor {address} send-community",
            ]
            continue
        if not router.public_bgp:
            continue
        if router.name == "fr-fw-edge":
            # A multihomed customer: take a default route from each ISP, give
            # each ISP the head office's own prefix and nothing else.
            activate += [
                f"  neighbor {address} route-map DEFAULT-ONLY in",
                f"  neighbor {address} route-map OWN-ONLY out",
                # Keep what the ISP sent, so that a change of policy is applied to
                # the stored routes and asks the ISP for nothing. FRR leaves the
                # default route out of its answer to a refresh that arrives just
                # after a session comes up (README.md, finding 7).
                f"  neighbor {address} soft-reconfiguration inbound",
            ]
            continue
        inbound = f"FROM-{relation.upper()}"
        if relation == "customer" and link.customer_prefixes:
            # A stub customer: accept its registered prefixes only.
            plist = f"CUST-{other.upper()}"
            for seq, prefix in enumerate(link.customer_prefixes, start=1):
                lines.append(f"ip prefix-list {plist} seq {seq * 5} permit {prefix}")
            inbound = f"FROM-CUST-{other.upper()}"
            customer_maps += [
                f"route-map {inbound} permit 10",
                f" match ip address prefix-list {plist}",
                f" set local-preference {RELATION_LOCAL_PREF['customer']}",
                f" set community {asn}:{RELATION_TAG['customer']}",
                "!",
            ]
            activate.append(f"  neighbor {address} default-originate")
        outbound = "TO-CUSTOMER" if relation == "customer" else "TO-UPSTREAM"
        activate += [
            f"  neighbor {address} route-map {inbound} in",
            f"  neighbor {address} route-map {outbound} out",
            f"  neighbor {address} send-community",
        ]

    for tunnel in TUNNELS:
        if router.name == tunnel.hub:
            address, remote_as = tunnel.site_address(), BY_NAME[tunnel.site].asn
        elif router.name == tunnel.site:
            address, remote_as = tunnel.hub_address(), BY_NAME[tunnel.hub].asn
        else:
            continue
        policy = "OVERLAY-PRIMARY" if tunnel.primary else "OVERLAY-BACKUP"
        if router.name == tunnel.hub:
            policy += f"-{tunnel.site.split('-')[0].upper()}"
        neighbors += [
            f" neighbor {address} remote-as {remote_as}",
            f" neighbor {address} description overlay-{tunnel.name}",
            f" neighbor {address} timers {CORE_KEEPALIVE_S} {CORE_HOLD_S}",
            f" neighbor {address} timers connect 5",
            f" neighbor {address} bfd",
        ]
        activate += [
            f"  neighbor {address} route-map {policy} in",
            f"  neighbor {address} route-map OVERLAY-OUT out",
        ]
        bfd_peers.append(address)
        if router.name == tunnel.site and PIN == PIN_MARK:
            # The tunnel's own routing table: its uplink, and behind it a
            # blackhole. When the uplink's link is down its route is gone, and
            # without the blackhole the lookup would fall through to the main
            # table and take the tunnel out of the other uplink.
            _other, gateway, _own = peer_of(link_of(router.name, tunnel.via), router.name)
            static_routes += [
                f"ip route 0.0.0.0/0 {gateway} table {tunnel.mark()}",
                f"ip route 0.0.0.0/0 blackhole 250 table {tunnel.mark()}",
            ]
        if router.name == tunnel.site and PIN == PIN_DESTINATION:
            # Pin each head-office endpoint to the uplink that carries its
            # tunnel, so the two tunnels of a site never share a path.
            _other, gateway, _own = peer_of(link_of(router.name, tunnel.via), router.name)
            static_routes.append(f"ip route {tunnel.hub_endpoint}/32 {gateway}")

    if not router.public_bgp:
        # ISP-assigned addresses: a default route per uplink, the first one
        # preferred. Nothing here notices a silent uplink failure (a real edge
        # adds a health check); the tunnels do, through BFD.
        distance = 10
        for link in LINKS:
            if router.name in (link.a, link.b) and link.relation == "static":
                _other, gateway, _own = peer_of(link, router.name)
                static_routes.append(f"ip route 0.0.0.0/0 {gateway} {distance}")
                distance += 10

    if router.multipath and PIN == PIN_MARK:
        # Whatever the head office sends on a default route leaves with its
        # own endpoint address, not with the address of the uplink. The match
        # keeps the routes learned through the tunnels out of it: a packet the
        # head office sends to a site must carry a tunnel address.
        lines += [
            "route-map SET-SOURCE permit 10",
            " match ip address prefix-list DEFAULT",
            f" set src {HUB_ENDPOINT}",
            "route-map SET-SOURCE permit 20",
            "!",
            "ip protocol bgp route-map SET-SOURCE",
            "!",
        ]

    networks: list[str] = [f"  network {prefix}" for prefix in router.originates]
    for lan in LANS:
        if lan.router == router.name and not lan.advertise_public:
            networks.append(f"  network {lan.net}")
    for prefix in router.originates:
        # An originated aggregate needs a route to stand on.
        static_routes.append(f"ip route {prefix} blackhole 250")
    static_routes += [f"ip route {block} blackhole {distance}" for block, distance in discards(router.name)]

    lines += customer_maps
    lines += static_routes
    lines += [
        "!",
        f"router bgp {asn}",
        f" bgp router-id {router.router_id}",
        " no bgp default ipv4-unicast",
        " bgp log-neighbor-changes",
        *([" bgp bestpath as-path multipath-relax"] if router.multipath else []),
        *neighbors,
        " address-family ipv4 unicast",
        *networks,
        *(["  maximum-paths 2"] if router.multipath else []),
        *[f"  neighbor {line.split()[1]} activate" for line in neighbors if " remote-as " in line],
        *activate,
        " exit-address-family",
        "!",
    ]
    if bfd_peers:
        lines += [
            "bfd",
            " profile fast",
            f"  transmit-interval {BFD_INTERVAL_MS}",
            f"  receive-interval {BFD_INTERVAL_MS}",
            f"  detect-multiplier {BFD_MULTIPLIER}",
            " !",
        ]
        for address in bfd_peers:
            lines += [f" peer {address}", "  profile fast", " !"]
        lines.append("!")
    lines.append("end")
    return "\n".join(lines) + "\n"


# ------------------------------ Lab lifecycle ------------------------------


def wireguard_mode() -> str:
    """`kernel` when the kernel has WireGuard, else `userspace` (wireguard-go)."""
    probe = run(["ip", "link", "add", "cm-wg-probe", "type", "wireguard"], check=False)
    if probe.returncode == 0:
        run(["ip", "link", "del", "cm-wg-probe"], check=False)
        return "kernel"
    if shutil.which("wireguard-go"):
        return "userspace"
    raise RuntimeError("no WireGuard: the kernel has none and wireguard-go is not installed")


def add_wireguard(mode: str, namespace: str, ifname: str) -> None:
    if mode == "kernel":
        run(["ip", "link", "add", ifname, "type", "wireguard"])
        run(["ip", "link", "set", ifname, "netns", namespace])
        return
    ns(namespace, "wireguard-go", ifname)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if run(["ip", "-n", namespace, "link", "show", ifname], check=False).returncode == 0:
            return
        time.sleep(0.1)
    raise RuntimeError(f"wireguard-go did not create {ifname} in {namespace}")


def all_namespaces() -> list[str]:
    return [r.name for r in ROUTERS] + HOSTS


def down() -> None:
    for name in all_namespaces():
        pids = run(["ip", "netns", "pids", name], check=False).stdout.split()
        if pids:
            run(["kill", "-9", *pids], check=False)
    time.sleep(0.3)
    for name in all_namespaces():
        run(["ip", "netns", "del", name], check=False)
        shutil.rmtree(FRR_ETC / name, ignore_errors=True)
        shutil.rmtree(FRR_RUN / name, ignore_errors=True)
    shutil.rmtree(WG_DIR, ignore_errors=True)
    for sock in Path("/var/run/wireguard").glob("wg[hs]-*.sock"):
        sock.unlink(missing_ok=True)


def up() -> str:
    """Build the namespaces, links, tunnels, and FRR instances."""
    down()
    for name in all_namespaces():
        run(["ip", "netns", "add", name])
        ns(name, "ip", "link", "set", "lo", "up")
        # traceroute needs every hop to answer every probe
        ns(name, "sysctl", "-qw", "net.ipv4.icmp_ratelimit=0")
    for router in ROUTERS:
        ns(router.name, "sysctl", "-qw", "net.ipv4.ip_forward=1")
        # Strict reverse-path filtering would drop the asymmetric paths that a
        # multihomed edge sees. The default value can be 2 here, so set it too.
        ns(router.name, "sysctl", "-qw", "net.ipv4.conf.all.rp_filter=0")
        ns(router.name, "sysctl", "-qw", "net.ipv4.conf.default.rp_filter=0")
        for address in router.loopbacks:
            ns(router.name, "ip", "addr", "add", address, "dev", "lo")

    for link in LINKS:
        first, second = link.addresses()
        prefix = ipaddress.ip_network(link.net).prefixlen
        if_a, if_b = link.ifname(link.a), link.ifname(link.b)
        run(["ip", "link", "add", if_a, "netns", link.a, "type", "veth", "peer", "name", if_b, "netns", link.b])
        for name, ifname, address in ((link.a, if_a, first), (link.b, if_b, second)):
            ns(name, "ip", "addr", "add", f"{address}/{prefix}", "dev", ifname)
            ns(name, "ip", "link", "set", ifname, "up")

    for lan in LANS:
        prefix = ipaddress.ip_network(lan.net).prefixlen
        run(
            [
                "ip",
                "link",
                "add",
                "lan0",
                "netns",
                lan.router,
                "type",
                "veth",
                "peer",
                "name",
                "eth0",
                "netns",
                lan.host,
            ]
        )
        ns(lan.router, "ip", "addr", "add", f"{lan.gateway()}/{prefix}", "dev", "lan0")
        ns(lan.router, "ip", "link", "set", "lan0", "up")
        ns(lan.host, "ip", "addr", "add", f"{lan.host_address()}/{prefix}", "dev", "eth0")
        ns(lan.host, "ip", "link", "set", "eth0", "up")
        ns(lan.host, "ip", "route", "add", "default", "via", lan.gateway())

    mode = wireguard_mode()
    WG_DIR.mkdir(parents=True, exist_ok=True)
    WG_DIR.chmod(0o700)
    for tunnel in TUNNELS:
        keys: dict[str, tuple[Path, str]] = {}
        for side in ("hub", "site"):
            private = run(["wg", "genkey"]).stdout.strip()
            public = subprocess.run(
                ["wg", "pubkey"], input=private, capture_output=True, text=True, check=True
            ).stdout.strip()
            path = WG_DIR / f"{tunnel.name}-{side}.key"
            path.write_text(private + "\n")
            path.chmod(0o600)
            keys[side] = (path, public)
        add_wireguard(mode, tunnel.hub, tunnel.hub_if())
        add_wireguard(mode, tunnel.site, tunnel.site_if())
        # Routing protocols run inside the tunnel, so WireGuard's own
        # cryptokey routing must let everything through: one interface per
        # tunnel, AllowedIPs 0.0.0.0/0, and no route installed for it.
        ns(
            tunnel.hub, "wg", "set", tunnel.hub_if(),
            "private-key", str(keys["hub"][0]), "listen-port", str(tunnel.port),
            "peer", keys["site"][1], "allowed-ips", "0.0.0.0/0",
        )  # fmt: skip
        ns(
            tunnel.site, "wg", "set", tunnel.site_if(),
            "private-key", str(keys["site"][0]),
            "peer", keys["hub"][1], "allowed-ips", "0.0.0.0/0",
            "endpoint", f"{tunnel.endpoint()}:{tunnel.port}", "persistent-keepalive", "5",
        )  # fmt: skip
        if PIN == PIN_MARK:
            # FRR installs the routes of the mark's table (frr_config).
            mark = str(tunnel.mark())
            ns(tunnel.site, "wg", "set", tunnel.site_if(), "fwmark", mark)
            ns(tunnel.site, "ip", "rule", "add", "fwmark", mark, "lookup", mark)
        for name, ifname, address in (
            (tunnel.hub, tunnel.hub_if(), tunnel.hub_address()),
            (tunnel.site, tunnel.site_if(), tunnel.site_address()),
        ):
            ns(name, "ip", "addr", "add", f"{address}/31", "dev", ifname)
            ns(name, "ip", "link", "set", ifname, "mtu", "1420", "up")

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for router in ROUTERS:
        etc, rundir = FRR_ETC / router.name, FRR_RUN / router.name
        for path in (etc, rundir):
            path.mkdir(parents=True, exist_ok=True)
            shutil.chown(path, "frr", "frr")
        config = frr_config(router)
        (etc / "frr.conf").write_text(config)
        (etc / "vtysh.conf").write_text("service integrated-vtysh-config\n")
        shutil.chown(etc / "frr.conf", "frr", "frr")
        (EVIDENCE / f"frr-{router.name}.conf").write_text(config)
        for daemon in ("zebra", "staticd", "bfdd", "bgpd"):
            ns(router.name, str(FRR_BIN / daemon), "-N", router.name, "-d", "-A", "127.0.0.1")
        loaded = run(["ip", "netns", "exec", router.name, "vtysh", "-N", router.name, "-b"], check=False)
        if loaded.returncode != 0 or "rror" in loaded.stdout + loaded.stderr:
            raise RuntimeError(f"frr.conf of {router.name} did not load: {loaded.stdout}{loaded.stderr}")
    return mode


# ------------------------------ Measurements -------------------------------


def established(router: str) -> dict[str, str]:
    """BGP neighbor description -> state, for one router."""
    peers = vtysh_json(router, "show bgp ipv4 unicast summary json").get("peers", {})
    return {f"{address} ({peer.get('desc', '')})": peer.get("state", "?") for address, peer in peers.items()}


def expected_sessions(router: Router) -> int:
    count = 0
    for link in LINKS:
        if router.name in (link.a, link.b) and link.relation != "static":
            count += 1
    return count + sum(1 for t in TUNNELS if router.name in (t.hub, t.site))


def wait_converged(timeout_s: float = 90.0) -> float:
    """Seconds until every configured BGP session is Established."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        if all(
            len(states := established(r.name)) == expected_sessions(r) and set(states.values()) == {"Established"}
            for r in ROUTERS
        ):
            return time.monotonic() - start
        time.sleep(1)
    return -1.0


def not_established() -> dict[str, dict[str, str]]:
    """Router -> its BGP sessions that are not Established."""
    stuck: dict[str, dict[str, str]] = {}
    for router in ROUTERS:
        bad = {peer: state for peer, state in established(router.name).items() if state != "Established"}
        if bad:
            stuck[router.name] = bad
    return stuck


def routes(router: str) -> dict[str, list[dict]]:
    return vtysh_json(router, "show ip route json")


def is_private(prefix: str) -> bool:
    network = ipaddress.ip_network(prefix)
    return any(network.subnet_of(ipaddress.ip_network(block)) for block in PRIVATE)


def in_blocks(prefix: str, site: str = "") -> bool:
    """`prefix` is inside a block of `site`, or of any site."""
    network = ipaddress.ip_network(prefix)
    blocks = SITE_BLOCKS[site] if site else [b for blocks in SITE_BLOCKS.values() for b in blocks]
    return any(network.subnet_of(ipaddress.ip_network(block)) for block in blocks)


def bgp_paths(router: str, prefix: str) -> list[str]:
    """The neighbors `router` holds a path to `prefix` from."""
    data = vtysh_json(router, f"show bgp ipv4 unicast {prefix} json")
    return sorted(path.get("peer", {}).get("peerId", "?") for path in data.get("paths", []))


def best_local_pref(router: str, prefix: str) -> int:
    """The local preference of `router`'s best path to `prefix`, or -1."""
    data = vtysh_json(router, f"show bgp ipv4 unicast {prefix} json")
    for path in data.get("paths", []):
        if path.get("bestpath", {}).get("overall"):
            return int(path.get("locPrf", path.get("localpref", -1)))
    return -1


def neighbor_address(router: str, other: str) -> str:
    """The address of `other` on its link with `router`."""
    return peer_of(link_of(router, other), router)[1]


def bgp_path(router: str, prefix: str) -> str:
    """The AS path of the best route to `prefix`, as text."""
    data = vtysh_json(router, f"show bgp ipv4 unicast {prefix} json")
    for path in data.get("paths", []):
        if path.get("bestpath", {}).get("overall"):
            return path.get("aspath", {}).get("string", "") or "(local)"
    return "(none)"


def next_hop_if(router: str, prefix: str) -> str:
    entries = routes(router).get(prefix, [])
    for entry in entries:
        if entry.get("selected"):
            hops = [hop.get("interfaceName", "?") for hop in entry.get("nexthops", []) if hop.get("active")]
            return ",".join(hops)
    return "(none)"


def reachable(src: str, dst: str, count: int = 3) -> bool:
    return (
        run(["ip", "netns", "exec", src, "ping", "-c", str(count), "-i", "0.2", "-W", "1", dst], check=False).returncode
        == 0
    )


def traceroute(src: str, dst: str, mark: int = 0) -> list[str]:
    extra = [f"--fwmark={mark}"] if mark else []
    out = ns(src, "traceroute", "-n", "-q", "1", "-w", "1", "-m", "12", *extra, dst, check=False)
    return [parts[1] for line in out.splitlines()[1:] if len(parts := line.split()) >= 2]


class Outage(BaseModel):
    """What a stream of pings saw while a fault was injected."""

    name: str
    stream: str
    sent: int
    lost: int
    longest_gap_s: float
    # The hops inside the tunnels, from the site's host to the head office's.
    path_before: str
    path_after: str
    # The internet hops under the site's primary tunnel.
    underlay_before: str = ""
    underlay_after: str = ""
    # Packets the fault's rules had dropped when the measurement ended.
    fault_drops: int = 0


class Pinger:
    """A background `ping` at a fixed interval, to time an outage."""

    def __init__(self, src: str, dst: str, seconds: float) -> None:
        self.src, self.dst = src, dst
        count = int(seconds / PING_INTERVAL_S)
        self.proc: subprocess.Popen[str] = subprocess.Popen(
            ["ip", "netns", "exec", src, "ping", "-D", "-i", str(PING_INTERVAL_S), "-W", "1", "-c", str(count), dst],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.count: int = count

    def result(self) -> tuple[int, int, float]:
        """(sent, lost, longest gap in seconds)."""
        out, _ = self.proc.communicate(timeout=self.count * PING_INTERVAL_S + 30)
        seen = sorted({int(m.group(1)) for m in re.finditer(r"icmp_seq=(\d+) ttl=", out)})
        lost = self.count - len(seen)
        longest, previous = 0, 0
        for seq in [*seen, self.count + 1]:
            longest = max(longest, seq - previous - 1)
            previous = seq
        return self.count, lost, round(longest * PING_INTERVAL_S, 1)


def blackhole(router: str, ifnames: list[str] | None) -> None:
    """Silently drop traffic on some interfaces of `router`, or on all of them.

    The interfaces stay up, so no neighbor sees a carrier loss: only BFD or a
    BGP hold timer can notice.
    """
    ns(router, "nft", "add", "table", "inet", "cmfault")
    for chain, hook in (("cm_forward", "forward"), ("cm_input", "input"), ("cm_output", "output")):
        ns(router, "nft", "add", "chain", "inet", "cmfault", chain, f"{{ type filter hook {hook} priority -300; }}")
    if ifnames is None:
        for chain in ("cm_forward", "cm_input", "cm_output"):
            ns(router, "nft", "add", "rule", "inet", "cmfault", chain, "meta", "iifname", "!=", "lo", "counter", "drop")
            ns(router, "nft", "add", "rule", "inet", "cmfault", chain, "meta", "oifname", "!=", "lo", "counter", "drop")
        return
    for ifname in ifnames:
        ns(router, "nft", "add", "rule", "inet", "cmfault", "cm_forward", "iifname", ifname, "counter", "drop")
        ns(router, "nft", "add", "rule", "inet", "cmfault", "cm_forward", "oifname", ifname, "counter", "drop")
        ns(router, "nft", "add", "rule", "inet", "cmfault", "cm_input", "iifname", ifname, "counter", "drop")
        ns(router, "nft", "add", "rule", "inet", "cmfault", "cm_output", "oifname", ifname, "counter", "drop")


def dropped(router: str) -> int:
    """Packets the fault rules of `router` have dropped so far."""
    listing = ns(router, "nft", "list", "table", "inet", "cmfault", check=False)
    return sum(int(count) for count in re.findall(r"counter packets (\d+)", listing))


def heal(router: str) -> None:
    ns(router, "nft", "delete", "table", "inet", "cmfault", check=False)


def site_path(site_host: str) -> str:
    """The hops from a site's host to the head office's host."""
    return " > ".join(traceroute(site_host, HQ_HOST_ADDRESS))


HQ_HOST_ADDRESS: str = LANS[0].host_address()
SITE_HOSTS: dict[str, str] = {lan.host: lan.host_address() for lan in LANS[1:4]}


def underlay_path(site_host: str) -> str:
    """The internet hops under a site's primary tunnel, from its edge to the head office's endpoint."""
    site = next(lan.router for lan in LANS if lan.host == site_host)
    tunnel = next(t for t in TUNNELS if t.site == site and t.primary)
    # The endpoint WireGuard sends to now, which may have roamed (finding 2).
    live = endpoints().get(tunnel.name, "(none)").rsplit(":", 1)[0]
    target = live if live not in ("(none)", "") else tunnel.endpoint()
    return " > ".join(traceroute(site, target, mark=tunnel.mark() if PIN == PIN_MARK else 0))


def timed_event(
    name: str, router: str, action: str, ifnames: list[str] | None = None, seconds: float = 25.0
) -> list[Outage]:
    """Inject a silent fault on `router` (or heal it) and time what each site sees.

    Every site pings the head office's host ten times a second through the
    event. `action` is `blackhole` or `heal`.
    """
    before = {host: site_path(host) for host in SITE_HOSTS}
    under_before = {host: underlay_path(host) for host in SITE_HOSTS}
    pingers = {host: Pinger(host, HQ_HOST_ADDRESS, seconds) for host in SITE_HOSTS}
    time.sleep(3)
    if action == "blackhole":
        log(f"{name}: blackhole on {router} {ifnames or 'all interfaces'}")
        blackhole(router, ifnames)
    else:
        drops = dropped(router)
        log(f"{name}: heal {router} (its fault rules dropped {drops} packets)")
        heal(router)
    time.sleep(seconds - 10)
    after = {host: site_path(host) for host in SITE_HOSTS}
    under_after = {host: underlay_path(host) for host in SITE_HOSTS}
    drops = dropped(router) if action == "blackhole" else 0
    outages: list[Outage] = []
    for host, pinger in pingers.items():
        sent, lost, gap = pinger.result()
        outages.append(
            Outage(
                name=name,
                stream=f"{host} to fr-historian",
                sent=sent,
                lost=lost,
                longest_gap_s=gap,
                path_before=before[host],
                path_after=after[host],
                underlay_before=under_before[host],
                underlay_after=under_after[host],
                fault_drops=drops,
            )
        )
        log(f"  {host}: {lost}/{sent} lost, longest gap {gap} s; tunnel {before[host]} -> {after[host]}")
    return outages


def fault(name: str, router: str, ifnames: list[str] | None, seconds: float = 25.0) -> list[Outage]:
    """Inject one silent fault and time the outage each site sees to the head office."""
    return timed_event(name, router, "blackhole", ifnames, seconds)


def recover(name: str, router: str, seconds: float = 25.0) -> list[Outage]:
    """Remove a fault and time what the return to the primary path costs."""
    return timed_event(name, router, "heal", None, seconds)


def endpoints() -> dict[str, str]:
    """Tunnel -> the head-office endpoint its site sends to right now."""
    seen: dict[str, str] = {}
    for tunnel in TUNNELS:
        fields = ns(tunnel.site, "wg", "show", tunnel.site_if(), "endpoints", check=False).split()
        seen[tunnel.name] = fields[-1] if fields else "(none)"
    return seen


def drifted() -> dict[str, str]:
    """Tunnels whose site no longer sends to the configured endpoint."""
    return {
        tunnel.name: f"{now} (configured {tunnel.endpoint()}:{tunnel.port})"
        for tunnel in TUNNELS
        if (now := endpoints()[tunnel.name]) != f"{tunnel.endpoint()}:{tunnel.port}"
    }


def uplink_of(tunnel: Tunnel) -> str:
    """The interface a site's tunnel packets leave through right now."""
    address = endpoints()[tunnel.name].rsplit(":", 1)[0]
    argv = ["ip", "route", "get", address]
    if PIN == PIN_MARK:
        argv += ["mark", str(tunnel.mark())]
    match = re.search(r" dev (\S+)", ns(tunnel.site, *argv, check=False))
    return match.group(1) if match else "(none)"


def footprint() -> dict[str, float]:
    """Proportional memory (PSS) in MiB of the routing daemons, per kind of process.

    PSS splits each shared page among the processes that map it, so the sum is
    what the daemons cost together; RSS would count the shared libraries once
    for every process.
    """
    totals: dict[str, float] = {}
    for router in ROUTERS:
        for pid in run(["ip", "netns", "pids", router.name], check=False).stdout.split():
            try:
                comm = Path(f"/proc/{pid}/comm").read_text().strip()
                status = Path(f"/proc/{pid}/smaps_rollup").read_text()
            except OSError:
                continue
            match = re.search(r"^Pss:\s+(\d+) kB", status, flags=re.M)
            if match:
                totals[comm] = totals.get(comm, 0.0) + int(match.group(1)) / 1024
    return {comm: round(mib, 1) for comm, mib in sorted(totals.items())}


# --------------------------------- Checks ---------------------------------


def run_checks(mode: str) -> Evidence:
    checks: list[Check] = []
    facts: dict[str, str] = {"wireguard": mode}
    outages: list[Outage] = []

    def check(name: str, passed: bool, detail: str = "") -> bool:
        checks.append(Check(name=name, passed=passed, detail=detail))
        log(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
        return passed

    seconds = wait_converged()
    sessions = {r.name: established(r.name) for r in ROUTERS}
    check(
        "every BGP session is Established",
        seconds >= 0,
        f"{sum(len(s) for s in sessions.values()) // 2} sessions in {seconds:.0f} s"
        if seconds >= 0
        else json.dumps(sessions),
    )
    time.sleep(8)  # let the last UPDATEs and the tunnels' BFD sessions settle

    # The internet: every AS reaches every other AS's prefix, on a valley-free path.
    paths = {
        "cb-south to the head office's prefix": bgp_path("cb-south", HQ_PREFIX),
        "bristlecone to the head office's prefix": bgp_path("bristlecone", HQ_PREFIX),
        "marmot to ShadeCast's prefix": bgp_path("marmot", "203.0.113.0/24"),
        "shadecast to Tumbleweed's prefix": bgp_path("shadecast", "198.18.101.0/24"),
        "shadecast to Marmot's prefix": bgp_path("shadecast", "198.18.102.0/24"),
        "tumbleweed to Bristlecone's prefix": bgp_path("tumbleweed", "198.18.103.0/24"),
    }
    for what, path in paths.items():
        facts[f"AS path, {what}"] = path
    # By preference, not by length: in every case here the path the policy
    # prefers is also the shorter one, so the AS path alone would not show it.
    peer_pref = best_local_pref("shadecast", "198.18.101.0/24")
    check(
        "ShadeCast reaches Tumbleweed and its customer Marmot over the peering link, not through transit",
        paths["shadecast to Tumbleweed's prefix"] == "64501"
        and paths["shadecast to Marmot's prefix"] == "64501 64502"
        and peer_pref == RELATION_LOCAL_PREF["peer"],
        f"{paths["shadecast to Tumbleweed's prefix"]!r} at local preference {peer_pref}, "
        f"{paths["shadecast to Marmot's prefix"]!r}",
    )
    marmot_from = bgp_paths("cb-west", "198.18.102.0/24")
    tumbleweed = neighbor_address("cb-west", "tumbleweed")
    check(
        "a route learned from a peer is not given to the provider (no valley)",
        marmot_from == [tumbleweed],
        f"cb-west holds Marmot's prefix from {', '.join(marmot_from) or 'nobody'} (Tumbleweed is {tumbleweed}; "
        f"a path from ShadeCast, {neighbor_address('cb-west', 'shadecast')}, would be a leak)",
    )
    # No ISP carries a private route of its own accord (looked at before the
    # test below gives Marmot one to offer).
    public_routers = [r.name for r in ROUTERS if r.public_bgp and r.name != "fr-fw-edge"]
    leaked = {name: [p for p in routes(name) if is_private(p)] for name in public_routers}
    # An ISP's filters, tried: Marmot offers its provider a private prefix and
    # a /25, and the provider must take neither.
    offered = ("10.99.0.0/24", "198.18.102.0/25")
    provider = neighbor_address("marmot", "tumbleweed")
    ns("marmot", "vtysh", "-N", "marmot", "-c", "configure terminal",
       *[arg for seq, prefix in enumerate(offered, start=60)
         for arg in ("-c", f"ip route {prefix} blackhole", "-c", f"ip prefix-list OWN seq {seq} permit {prefix}")],
       "-c", "router bgp 64502", "-c", "address-family ipv4 unicast",
       *[arg for prefix in offered for arg in ("-c", f"network {prefix}")])  # fmt: skip
    time.sleep(8)
    sent = vtysh_json("marmot", f"show bgp ipv4 unicast neighbors {provider} advertised-routes json")
    sent_routes = sent.get("advertisedRoutes", {})
    taken = {prefix: bgp_paths("tumbleweed", prefix) for prefix in offered}
    check(
        "an ISP refuses a private prefix and one longer than a /24 from a customer, and carries no private route",
        all(prefix in sent_routes for prefix in offered) and not any(taken.values()) and not any(leaked.values()),
        f"Marmot offered {', '.join(p for p in offered if p in sent_routes) or 'nothing'}; Tumbleweed holds "
        f"{json.dumps(taken)}; private routes at the ISPs: {json.dumps({k: v for k, v in leaked.items() if v}) or 'none'}",
    )
    ns("marmot", "vtysh", "-N", "marmot", "-c", "configure terminal",
       "-c", "router bgp 64502", "-c", "address-family ipv4 unicast",
       *[arg for prefix in offered for arg in ("-c", f"no network {prefix}")],
       "-c", "exit", "-c", "exit",
       *[arg for seq, prefix in enumerate(offered, start=60)
         for arg in ("-c", f"no ip route {prefix} blackhole", "-c", f"no ip prefix-list OWN seq {seq}")])  # fmt: skip
    time.sleep(3)
    hq_default = sorted(next_hop_if("fr-fw-edge", "0.0.0.0/0").split(","))
    check(
        "the head office installs a default route through each of its two ISPs",
        hq_default == ["fr-sc", "fr-tw"],
        f"default via {', '.join(hq_default)}",
    )
    # A route that an ISP did not send again in a route refresh stays marked
    # stale, and a newer FRR removes it when the refresh ends.
    paths = vtysh_json("fr-fw-edge", "show bgp ipv4 unicast 0.0.0.0/0 json").get("paths", [])
    stale = [path.get("peer", {}).get("peerId", "?") for path in paths if path.get("stale")]
    check(
        "neither default route of the head office is marked stale",
        len(paths) == 2 and not stale,
        f"{len(paths)} paths; stale from {', '.join(stale) or 'no ISP'}",
    )
    site_routes = {
        name: sorted(p for p, entries in routes(name).items() if entries[0].get("protocol") == "bgp")
        for name in ("fc-fw-edge", "ep-fw-edge", "cs-fw-edge")
    }
    check(
        "a site learns only site prefixes over the tunnels (no default, no public route)",
        all(all(in_blocks(p) for p in prefixes) and len(prefixes) == 3 for prefixes in site_routes.values()),
        json.dumps(site_routes),
    )

    # The overlay: every site reaches the head office and every other site.
    reach: dict[str, bool] = {}
    for host in SITE_HOSTS:
        reach[f"{host} to fr-historian"] = reachable(host, HQ_HOST_ADDRESS)
        for other, address in SITE_HOSTS.items():
            if other != host:
                reach[f"{host} to {other}"] = reachable(host, address)
    check("every site reaches the head office and every other site", all(reach.values()), json.dumps(reach))
    for host in SITE_HOSTS:
        facts[f"path, {host} to fr-historian"] = site_path(host)
    facts["path, fc-historian to cs-historian"] = " > ".join(traceroute("fc-historian", SITE_HOSTS["cs-historian"]))
    check(
        "site traffic rides the primary tunnel",
        next_hop_if("fc-fw-edge", HQ_LAN) == "wgs-fc-a" and next_hop_if("fr-fw-edge", FC_LAN) == "wgh-fc-a",
        f"fc-fw-edge via {next_hop_if('fc-fw-edge', HQ_LAN)}, fr-fw-edge back via {next_hop_if('fr-fw-edge', FC_LAN)}",
    )
    exits = {name: private_exits(name) for name in SITE_BLOCKS}
    blackholed = {name: "blackhole" in ns(name, "ip", "route", "show", "10.0.0.0/8") for name in SITE_BLOCKS}
    check(
        "no edge would send an unused private address out of an uplink",
        not any(exits.values()) and all(blackholed.values()),
        json.dumps({name: found for name, found in exits.items() if found})
        if any(exits.values())
        else f"{sum(len(private_probes(n)) for n in SITE_BLOCKS)} probes at {len(SITE_BLOCKS)} edges; "
        f"10.0.0.0/8 is a blackhole at {sum(blackholed.values())} of them",
    )
    check(
        "a private address is not reachable from the internet side",
        not reachable("inet", HQ_HOST_ADDRESS, count=2),
        "inet cannot ping fr-historian",
    )
    check(
        "the head office's public endpoint answers from the internet",
        reachable("inet", "198.18.105.1"),
        "inet pings 198.18.105.1",
    )
    facts["pin"] = PIN
    facts["tunnel endpoints at the start"] = json.dumps(endpoints())
    facts["routing daemons, proportional (PSS) MiB"] = json.dumps(footprint())
    facts["routing daemons, total MiB"] = str(round(sum(footprint().values()), 1))

    # Fault 1: the Fort Collins site's primary uplink dies without a link-down.
    result = fault("Fort Collins uplink to ShadeCast, silent", "shadecast", ["sc-fc"])
    outages += result
    fc = next(o for o in result if o.stream.startswith("fc-historian"))
    check(
        "the Fort Collins site fails over to its second ISP when its ShadeCast uplink goes silent",
        0 < fc.longest_gap_s <= 3.0 and next_hop_if("fc-fw-edge", HQ_LAN) == "wgs-fc-b",
        f"outage {fc.longest_gap_s} s, now via {next_hop_if('fc-fw-edge', HQ_LAN)}",
    )
    check(
        "the other sites do not notice",
        all(o.lost == 0 for o in result if not o.stream.startswith("fc-historian")),
        ", ".join(f"{o.stream}: {o.lost} lost" for o in result if not o.stream.startswith("fc-historian")),
    )
    result = recover("Fort Collins uplink to ShadeCast, restored", "shadecast")
    outages += result
    fc = next(o for o in result if o.stream.startswith("fc-historian"))
    time.sleep(5)
    check(
        "the Fort Collins site returns to the primary tunnel after the repair",
        next_hop_if("fc-fw-edge", HQ_LAN) == "wgs-fc-a" and fc.longest_gap_s <= 1.0,
        f"via {next_hop_if('fc-fw-edge', HQ_LAN)}, outage on the way back {fc.longest_gap_s} s",
    )

    # Fault 2: the head office loses its ShadeCast uplink, silently. It starts
    # with the lab converged and Fort Collins on its primary tunnel, whose
    # underlay crosses the failed link, so Fort Collins must notice.
    ready = wait_converged(30) >= 0 and next_hop_if("fc-fw-edge", HQ_LAN) == "wgs-fc-a"
    result = fault("head office uplink to ShadeCast, silent", "shadecast", ["sc-fr"])
    outages += result
    worst = max(o.longest_gap_s for o in result)
    fc = next(o for o in result if o.stream.startswith("fc-historian"))
    crosses = fc.underlay_before.split(" > ") == [neighbor_address("fc-fw-edge", "shadecast"), HUB_ENDPOINT]
    check(
        "every site keeps the head office when it loses one of its two ISPs",
        ready
        and 0 < fc.longest_gap_s
        and all(o.longest_gap_s <= 5.0 for o in result)
        and all(reachable(h, HQ_HOST_ADDRESS, 2) for h in SITE_HOSTS),
        f"started converged on fc-a: {ready}; Fort Collins lost {fc.longest_gap_s} s, the longest outage was "
        f"{worst} s; ShadeCast now reaches the head office over {bgp_path('shadecast', HQ_PREFIX)!r}",
    )
    check(
        "the fault was real: Fort Collins's tunnel crossed the failed link, and ShadeCast rerouted through its peer",
        crosses and fc.fault_drops > 0 and bgp_path("shadecast", HQ_PREFIX) == "64501 64505",
        f"Fort Collins underlay {fc.underlay_before} -> {fc.underlay_after}; {fc.fault_drops} packets dropped",
    )
    facts["path, fc-historian to fr-historian, head office on one ISP"] = site_path("fc-historian")
    outages += recover("head office uplink to ShadeCast, restored", "shadecast")
    time.sleep(5)

    # Fault 3: all of ShadeCast goes dark.
    ready = wait_converged(30) >= 0 and next_hop_if("fc-fw-edge", HQ_LAN) == "wgs-fc-a"
    result = fault("ShadeCast outage, silent", "shadecast", None, seconds=35.0)
    outages += result
    worst = max(o.longest_gap_s for o in result)
    fc = next(o for o in result if o.stream.startswith("fc-historian"))
    check(
        "every site keeps the head office through the loss of a whole ISP",
        ready and 0 < fc.longest_gap_s and all(reachable(h, HQ_HOST_ADDRESS, 2) for h in SITE_HOSTS),
        f"longest outage {worst} s; Fort Collins via {next_hop_if('fc-fw-edge', HQ_LAN)}, "
        f"Eastern Plains via {next_hop_if('ep-fw-edge', HQ_LAN)}",
    )
    check(
        "the outage is bounded by the BGP hold time of the ISP core",
        worst <= CORE_HOLD_S + 3,
        f"{worst} s against a {CORE_HOLD_S} s hold time",
    )
    outages += recover("ShadeCast outage, restored", "shadecast", seconds=35.0)
    time.sleep(5)

    # Fault 4: the same outage, with the transit carrier's route to the head
    # office going through ShadeCast. Until now it went through Tumbleweed (the
    # two customer routes tie), so the Colorado Springs site was never on the path.
    wait_converged(60)
    _name, shadecast_address, _own = peer_of(link_of("cb-west", "shadecast"), "cb-west")
    ns("cb-west", "vtysh", "-N", "cb-west", "-c", "configure terminal",
       "-c", "route-map FROM-CUSTOMER-PREFERRED permit 10",
       "-c", "match ip address prefix-list PUBLIC",
       "-c", "set local-preference 210",
       "-c", f"set community 64496:{RELATION_TAG['customer']}",
       "-c", "exit",
       "-c", "router bgp 64496", "-c", "address-family ipv4 unicast",
       "-c", f"neighbor {shadecast_address} route-map FROM-CUSTOMER-PREFERRED in")  # fmt: skip
    time.sleep(8)
    through = bgp_path("cb-west", HQ_PREFIX)
    result = fault("ShadeCast outage, the transit carrier routing through it", "shadecast", None, seconds=45.0)
    outages += result
    cs = next(o for o in result if o.stream.startswith("cs-historian"))
    check(
        "a site whose path crosses the dead ISP in the core waits for the BGP hold timer",
        through == "64500 64505" and CORE_HOLD_S - CORE_KEEPALIVE_S <= cs.longest_gap_s <= CORE_HOLD_S + 3,
        f"cb-west reached the head office over {through!r}; Colorado Springs lost {cs.longest_gap_s} s "
        f"against a {CORE_HOLD_S} s hold time; underlay {cs.underlay_before} -> {cs.underlay_after}",
    )
    outages += recover("ShadeCast outage, restored again", "shadecast", seconds=35.0)
    ns("cb-west", "vtysh", "-N", "cb-west", "-c", "configure terminal",
       "-c", "router bgp 64496", "-c", "address-family ipv4 unicast",
       "-c", f"neighbor {shadecast_address} route-map FROM-CUSTOMER in",
       "-c", "exit", "-c", "exit",
       "-c", "no route-map FROM-CUSTOMER-PREFERRED")  # fmt: skip
    time.sleep(8)

    if PIN == PIN_MARK:
        # A loud fault: the link of Fort Collins's ShadeCast uplink goes down,
        # and the route of the tunnel's table goes with it.
        wait_converged(60)
        uplink = link_of("fc-fw-edge", "shadecast").ifname("fc-fw-edge")
        tunnel = next(t for t in TUNNELS if t.name == "fc-a")
        ns("fc-fw-edge", "ip", "link", "set", uplink, "down")
        time.sleep(6)
        proc = run(
            ["ip", "netns", "exec", "fc-fw-edge", "ip", "route", "get", HUB_ENDPOINT, "mark", str(tunnel.mark())],
            check=False,
        )
        lookup = (proc.stdout + proc.stderr).strip()
        table = ns("fc-fw-edge", "ip", "route", "show", "table", str(tunnel.mark()), check=False)
        # The kernel answers a lookup that ends in a blackhole with "Invalid
        # argument"; the table must hold that blackhole and no route out.
        stayed = "Invalid argument" in lookup and "blackhole" in table and " dev " not in table
        check(
            "with its uplink's link down, a tunnel does not move to the other uplink",
            stayed and next_hop_if("fc-fw-edge", HQ_LAN) == "wgs-fc-b" and reachable("fc-historian", HQ_HOST_ADDRESS),
            f"lookup for fc-a: {' '.join(lookup.split())}; table {tunnel.mark()}: {' '.join(table.split())}; "
            f"traffic via {next_hop_if('fc-fw-edge', HQ_LAN)}",
        )
        ns("fc-fw-edge", "ip", "link", "set", uplink, "up")
        started = time.monotonic()
        while time.monotonic() - started < 60 and next_hop_if("fc-fw-edge", HQ_LAN) != "wgs-fc-a":
            time.sleep(1)
        check(
            "the tunnel returns when the link does",
            next_hop_if("fc-fw-edge", HQ_LAN) == "wgs-fc-a",
            f"after {time.monotonic() - started:.0f} s",
        )

    facts["tunnel endpoints after the faults and repairs"] = json.dumps(endpoints())
    moved = drifted()
    check(
        "no tunnel endpoint has moved off the address its site was given",
        not moved,
        json.dumps(moved) if moved else f"all {len(TUNNELS)} as configured",
    )
    uplinks = {t.name: uplink_of(t) for t in TUNNELS}
    wanted = {t.name: link_of(t.site, t.via).ifname(t.site) for t in TUNNELS}
    check(
        "every tunnel still leaves through its own uplink",
        uplinks == wanted,
        json.dumps(uplinks) if uplinks == wanted else f"is {json.dumps(uplinks)}, should be {json.dumps(wanted)}",
    )
    back = wait_converged(120)
    check(
        "the lab converges again after the repairs",
        back >= 0,
        f"{back:.0f} s" if back >= 0 else f"not Established: {json.dumps(not_established())}",
    )
    if back < 0:
        for name, sessions in not_established().items():
            for session in sessions:
                address = session.split()[0]
                (EVIDENCE / f"stuck-{name}-{address}.txt").write_text(
                    vtysh(name, f"show bgp neighbors {address}")
                    + vtysh(name, "show bfd peers")
                    + ns(name, "wg", "show")
                )

    # Scenario: the brewpub's edge announces the plant network of the Colorado Springs
    # site, as an edge that somebody else controls could. Its own export filter
    # is opened first, so that what stops the route is the head office's filter.
    # It also announces a /24 inside the head office's own block, which only a
    # filter for each site refuses: a filter for all the sites' blocks takes it.
    cs_tunnel = next(tunnel for tunnel in TUNNELS if tunnel.site == "cs-fw-edge")
    fc_peers = sorted(tunnel.site_address() for tunnel in TUNNELS if tunnel.site == "fc-fw-edge")
    foreign = (CS_LAN, HQ_MORE_SPECIFIC)
    before_paths = bgp_paths("fr-fw-edge", CS_LAN)
    opened = [
        command
        for seq, prefix in enumerate(foreign, start=90)
        for command in ("-c", f"ip route {prefix} blackhole", "-c", f"ip prefix-list SITE-FC seq {seq} permit {prefix}")
    ]
    ns("fc-fw-edge", "vtysh", "-N", "fc-fw-edge", "-c", "configure terminal", *opened,
       "-c", "router bgp 65020", "-c", "address-family ipv4 unicast",
       *[arg for prefix in foreign for arg in ("-c", f"network {prefix}")])  # fmt: skip
    time.sleep(10)
    sent = vtysh_json(
        "fc-fw-edge", f"show bgp ipv4 unicast neighbors {TUNNELS[0].hub_address()} advertised-routes json"
    )
    offered = [prefix for prefix in foreign if prefix in sent.get("advertisedRoutes", {})]
    after_paths = bgp_paths("fr-fw-edge", CS_LAN)
    more_paths = bgp_paths("fr-fw-edge", HQ_MORE_SPECIFIC)
    facts["foreign prefix, the head office's paths before"] = json.dumps(before_paths)
    facts["foreign prefix, the head office's paths after"] = json.dumps(after_paths)
    check(
        "the head office refuses another site's network when a site announces it",
        offered == list(foreign)
        and after_paths == [cs_tunnel.site_address()]
        and not set(after_paths) & set(fc_peers)
        and next_hop_if("fr-fw-edge", CS_LAN) == cs_tunnel.hub_if()
        and reachable("ep-historian", SITE_HOSTS["cs-historian"], 2),
        f"fc-fw-edge offered {', '.join(offered) or 'nothing'}; the head office holds {CS_LAN} from "
        f"{', '.join(after_paths)} (the Colorado Springs tunnel is {cs_tunnel.site_address()}), next hop "
        f"{next_hop_if('fr-fw-edge', CS_LAN)}; ep-historian still reaches cs-historian",
    )
    check(
        "the head office refuses a site's more-specific of the head office's own block",
        HQ_MORE_SPECIFIC in offered and not more_paths and next_hop_if("fr-fw-edge", HQ_MORE_SPECIFIC) == "(none)",
        f"fc-fw-edge offered {HQ_MORE_SPECIFIC}: {HQ_MORE_SPECIFIC in offered}; the head office holds it from "
        f"{', '.join(more_paths) or 'no neighbor'}",
    )
    closed = [
        command
        for seq, prefix in enumerate(foreign, start=90)
        for command in ("-c", f"no ip route {prefix} blackhole", "-c", f"no ip prefix-list SITE-FC seq {seq}")
    ]
    ns("fc-fw-edge", "vtysh", "-N", "fc-fw-edge", "-c", "configure terminal",
       "-c", "router bgp 65020", "-c", "address-family ipv4 unicast",
       *[arg for prefix in foreign for arg in ("-c", f"no network {prefix}")],
       "-c", "exit", "-c", "exit", *closed)  # fmt: skip
    time.sleep(5)

    # Scenario: Bristlecone announces the head office's prefix (an origin hijack).
    before = {name: bgp_path(name, HQ_PREFIX) for name in ("cb-west", "cb-south", "shadecast", "tumbleweed")}
    ns("bristlecone", "vtysh", "-N", "bristlecone", "-c", "configure terminal",
       "-c", f"ip route {HQ_PREFIX} blackhole",
       "-c", f"ip prefix-list OWN seq 50 permit {HQ_PREFIX}",
       "-c", "router bgp 64503", "-c", "address-family ipv4 unicast", "-c", f"network {HQ_PREFIX}")  # fmt: skip
    time.sleep(10)
    after = {name: bgp_path(name, HQ_PREFIX) for name in before}
    facts["hijack, AS path to the head office's prefix before"] = json.dumps(before)
    facts["hijack, AS path to the head office's prefix after"] = json.dumps(after)
    hijack_reach = {host: reachable(host, HQ_HOST_ADDRESS, 2) for host in SITE_HOSTS}
    check(
        "an origin hijack by Bristlecone takes the transit carrier's traffic and cuts off the site behind Bristlecone",
        after["cb-south"] == "64503" and not hijack_reach["cs-historian"],
        f"paths {json.dumps(after)}; reach {json.dumps(hijack_reach)}",
    )
    check(
        "the head office's own ISPs still prefer their customer route, so their sites stay up",
        after["shadecast"] == "64505" and hijack_reach["fc-historian"] and hijack_reach["ep-historian"],
        f"shadecast {after['shadecast']!r}, tumbleweed {after['tumbleweed']!r}",
    )
    ns("bristlecone", "vtysh", "-N", "bristlecone", "-c", "configure terminal",
       "-c", "router bgp 64503", "-c", "address-family ipv4 unicast", "-c", f"no network {HQ_PREFIX}",
       "-c", "exit", "-c", "exit",
       "-c", f"no ip route {HQ_PREFIX} blackhole",
       "-c", "no ip prefix-list OWN seq 50")  # fmt: skip
    started = time.monotonic()
    while time.monotonic() - started < 90 and not reachable("cs-historian", HQ_HOST_ADDRESS, 2):
        time.sleep(1)
    took = time.monotonic() - started
    check(
        "the site behind Bristlecone comes back when the announcement is withdrawn",
        reachable("cs-historian", HQ_HOST_ADDRESS),
        f"after {took:.0f} s; cb-south path {bgp_path('cb-south', HQ_PREFIX)!r}",
    )

    facts["outages"] = json.dumps([o.model_dump() for o in outages])
    return Evidence(spike=EVIDENCE.name, checks=checks, facts=facts)


def write_evidence(evidence: Evidence) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "result.json").write_text(evidence.model_dump_json(indent=2) + "\n")
    lines = [f"# {evidence.spike}", "", "| Check | Result | Detail |", "|---|---|---|"]
    for item in evidence.checks:
        detail = item.detail.replace("|", "\\|")
        lines.append(f"| {item.name} | {'pass' if item.passed else '**fail**'} | {detail} |")
    outages = [Outage(**o) for o in json.loads(evidence.facts.get("outages", "[]"))]
    if outages:
        lines += [
            "",
            "Each site pings the head office's host ten times a second through the event. The tunnel",
            "path is the hops inside the VPN; the underlay path is the internet hops under the site's",
            "primary tunnel.",
            "",
            "| Event | Stream | Sent | Lost | Longest gap (s) | Tunnel path before | Tunnel path after "
            "| Underlay before | Underlay after | Fault drops |",
            "|---|---|---:|---:|---:|---|---|---|---|---:|",
        ]
        for o in outages:
            lines.append(
                f"| {o.name} | {o.stream} | {o.sent} | {o.lost} | {o.longest_gap_s} | {o.path_before} "
                f"| {o.path_after} | {o.underlay_before} | {o.underlay_after} | {o.fault_drops} |"
            )
    lines += ["", "| Fact | Value |", "|---|---|"]
    for key, value in evidence.facts.items():
        if key != "outages":
            lines.append(f"| {key} | {value.replace('|', chr(92) + '|')} |")
    (EVIDENCE / "summary.md").write_text("\n".join(lines) + "\n")
    for router in ROUTERS:
        text = "\n".join(
            vtysh(router.name, command)
            for command in (
                "show bgp ipv4 unicast summary",
                "show bgp ipv4 unicast",
                "show ip route",
                "show bfd peers brief",
            )
        )
        (EVIDENCE / f"state-{router.name}.txt").write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="leave the lab up after the checks")
    parser.add_argument("--down", action="store_true", help="tear a kept lab down and exit")
    parser.add_argument("--up-only", action="store_true", help="build the lab and exit without the checks")
    parser.add_argument(
        "--pin",
        choices=[PIN_MARK, PIN_DESTINATION],
        default=PIN_MARK,
        help="how a site ties a tunnel to an uplink (default: mark)",
    )
    args = parser.parse_args()
    global PIN, EVIDENCE  # noqa: PLW0603
    PIN = args.pin
    if PIN == PIN_DESTINATION:
        EVIDENCE = EVIDENCE.with_name(f"{EVIDENCE.name}-destination")
    if os.geteuid() != 0:
        print("run as root", file=sys.stderr)
        return 2
    if args.down:
        down()
        return 0
    for stale in ("summary.md", "result.json", *[p.name for p in EVIDENCE.glob("stuck-*.txt")]):
        (EVIDENCE / stale).unlink(missing_ok=True)
    try:
        mode = up()
        log(f"lab is up (WireGuard: {mode})")
        if args.up_only:
            return 0
        evidence = run_checks(mode)
        write_evidence(evidence)
        log(f"evidence in {EVIDENCE}")
        return 0 if evidence.passed else 1
    finally:
        if not (args.keep or args.up_only):
            down()


if __name__ == "__main__":
    raise SystemExit(main())
