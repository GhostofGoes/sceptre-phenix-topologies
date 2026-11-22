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
    Router(name="cb-south", short="cbs", asn=64496, router_id="198.18.0.252", title="Continental Backbone, Houston"),
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
        name="pelican",
        short="pc",
        asn=64503,
        router_id="198.18.103.254",
        title="Pelican Coast Broadband",
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
        name="hc-fw-edge",
        short="hc",
        asn=65020,
        router_id="10.255.2.1",
        title="High Country brewpub edge",
        public_bgp=False,
    ),
    Router(
        name="ws-fw-edge",
        short="ws",
        asn=65030,
        router_id="10.255.3.1",
        title="Western Slope brewery edge",
        public_bgp=False,
    ),
    Router(
        name="gc-fw-edge",
        short="gc",
        asn=65040,
        router_id="10.255.4.1",
        title="Gulf Coast brewery edge",
        public_bgp=False,
    ),
]

HQ_PREFIX: str = "198.18.105.0/24"

LINKS: list[Link] = [
    Link(a="cb-west", b="cb-south", net="198.18.0.0/30", relation="ibgp"),
    Link(a="cb-west", b="shadecast", net="198.18.0.4/30", relation="customer"),
    Link(a="cb-west", b="tumbleweed", net="198.18.0.8/30", relation="customer"),
    Link(a="cb-south", b="pelican", net="198.18.0.12/30", relation="customer"),
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
    Link(a="shadecast", b="hc-fw-edge", net="203.0.113.8/30", relation="static"),
    Link(a="marmot", b="hc-fw-edge", net="198.18.102.0/30", relation="static"),
    Link(a="tumbleweed", b="ws-fw-edge", net="198.18.101.8/30", relation="static"),
    Link(a="shadecast", b="ws-fw-edge", net="203.0.113.16/30", relation="static"),
    Link(a="pelican", b="gc-fw-edge", net="198.18.103.0/30", relation="static"),
]

LANS: list[Lan] = [
    Lan(router="fr-fw-edge", host="fr-historian", net="10.20.30.0/24"),
    Lan(router="hc-fw-edge", host="hc-historian", net="10.21.30.0/24"),
    Lan(router="ws-fw-edge", host="ws-historian", net="10.22.30.0/24"),
    Lan(router="gc-fw-edge", host="gc-historian", net="10.23.30.0/24"),
    Lan(router="cb-west", host="inet", net="198.18.110.0/24", advertise_public=True),
]

TUNNELS: list[Tunnel] = [
    Tunnel(
        name="hc-a",
        site="hc-fw-edge",
        hub="fr-fw-edge",
        via="shadecast",
        hub_endpoint="198.18.105.1",
        port=51821,
        net="10.255.0.0/31",
        primary=True,
    ),
    Tunnel(
        name="hc-b",
        site="hc-fw-edge",
        hub="fr-fw-edge",
        via="marmot",
        hub_endpoint="198.18.105.2",
        port=51822,
        net="10.255.0.2/31",
        primary=False,
    ),
    Tunnel(
        name="ws-a",
        site="ws-fw-edge",
        hub="fr-fw-edge",
        via="tumbleweed",
        hub_endpoint="198.18.105.1",
        port=51823,
        net="10.255.0.4/31",
        primary=True,
    ),
    Tunnel(
        name="ws-b",
        site="ws-fw-edge",
        hub="fr-fw-edge",
        via="shadecast",
        hub_endpoint="198.18.105.2",
        port=51824,
        net="10.255.0.6/31",
        primary=False,
    ),
    Tunnel(
        name="gc-a",
        site="gc-fw-edge",
        hub="fr-fw-edge",
        via="pelican",
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
        # The overlay carries the sites' private prefixes and nothing else.
        "ip prefix-list SITE-NETS seq 5 permit 10.0.0.0/8 ge 16 le 24",
        # What an ISP takes from another AS: public space, nothing longer than /24.
        "ip prefix-list PUBLIC seq 5 deny 10.0.0.0/8 le 32",
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
        "route-map OVERLAY-PRIMARY permit 10",
        " match ip address prefix-list SITE-NETS",
        " set local-preference 200",
        "!",
        "route-map OVERLAY-BACKUP permit 10",
        " match ip address prefix-list SITE-NETS",
        " set local-preference 100",
        "!",
        "route-map OVERLAY-OUT permit 10",
        " match ip address prefix-list SITE-NETS",
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
    return " > ".join(traceroute(site, tunnel.endpoint(), mark=tunnel.mark() if PIN == PIN_MARK else 0))


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
    """Resident memory in MiB of the routing daemons, per kind of process."""
    totals: dict[str, float] = {}
    for router in ROUTERS:
        for pid in run(["ip", "netns", "pids", router.name], check=False).stdout.split():
            try:
                comm = Path(f"/proc/{pid}/comm").read_text().strip()
                status = Path(f"/proc/{pid}/status").read_text()
            except OSError:
                continue
            match = re.search(r"VmRSS:\s+(\d+) kB", status)
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
        "pelican to the head office's prefix": bgp_path("pelican", HQ_PREFIX),
        "marmot to ShadeCast's prefix": bgp_path("marmot", "203.0.113.0/24"),
        "shadecast to Tumbleweed's prefix": bgp_path("shadecast", "198.18.101.0/24"),
        "shadecast to Marmot's prefix": bgp_path("shadecast", "198.18.102.0/24"),
        "tumbleweed to Pelican's prefix": bgp_path("tumbleweed", "198.18.103.0/24"),
    }
    for what, path in paths.items():
        facts[f"AS path, {what}"] = path
    check(
        "ShadeCast reaches Tumbleweed and its customer Marmot over the peering link, not through transit",
        paths["shadecast to Tumbleweed's prefix"] == "64501" and paths["shadecast to Marmot's prefix"] == "64501 64502",
        f"{paths["shadecast to Tumbleweed's prefix"]!r}, {paths["shadecast to Marmot's prefix"]!r}",
    )
    check(
        "a route learned from a peer is not given to the provider (no valley)",
        "64500 64501" not in bgp_path("cb-west", "198.18.102.0/24")
        and bgp_path("cb-west", "198.18.102.0/24") == "64501 64502",
        f"cb-west to Marmot's prefix: {bgp_path('cb-west', '198.18.102.0/24')!r}",
    )
    public_routers = [r.name for r in ROUTERS if r.public_bgp and r.name != "fr-fw-edge"]
    leaked = {name: [p for p in routes(name) if p.startswith("10.")] for name in public_routers}
    check(
        "no ISP carries a private (10.0.0.0/8) route",
        not any(leaked.values()),
        json.dumps({k: v for k, v in leaked.items() if v}) or "none",
    )
    hq_default = sorted(next_hop_if("fr-fw-edge", "0.0.0.0/0").split(","))
    check(
        "the head office installs a default route through each of its two ISPs",
        hq_default == ["fr-sc", "fr-tw"],
        f"default via {', '.join(hq_default)}",
    )
    site_routes = {
        name: sorted(p for p, entries in routes(name).items() if entries[0].get("protocol") == "bgp")
        for name in ("hc-fw-edge", "ws-fw-edge", "gc-fw-edge")
    }
    check(
        "a site learns only site prefixes over the tunnels (no default, no public route)",
        all(all(p.startswith("10.") for p in prefixes) and len(prefixes) == 3 for prefixes in site_routes.values()),
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
    facts["path, hc-historian to gc-historian"] = " > ".join(traceroute("hc-historian", SITE_HOSTS["gc-historian"]))
    check(
        "site traffic rides the primary tunnel",
        next_hop_if("hc-fw-edge", "10.20.30.0/24") == "wgs-hc-a"
        and next_hop_if("fr-fw-edge", "10.21.30.0/24") == "wgh-hc-a",
        f"hc-fw-edge via {next_hop_if('hc-fw-edge', '10.20.30.0/24')}, fr-fw-edge back via {next_hop_if('fr-fw-edge', '10.21.30.0/24')}",
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
    facts["routing daemons, resident MiB"] = json.dumps(footprint())
    facts["routing daemons, total MiB"] = str(round(sum(footprint().values()), 1))

    # Fault 1: the High Country site's primary uplink dies without a link-down.
    result = fault("High Country uplink to ShadeCast, silent", "shadecast", ["sc-hc"])
    outages += result
    hc = next(o for o in result if o.stream.startswith("hc-historian"))
    check(
        "the High Country site fails over to its second ISP when its ShadeCast uplink goes silent",
        0 < hc.longest_gap_s <= 3.0 and next_hop_if("hc-fw-edge", "10.20.30.0/24") == "wgs-hc-b",
        f"outage {hc.longest_gap_s} s, now via {next_hop_if('hc-fw-edge', '10.20.30.0/24')}",
    )
    check(
        "the other sites do not notice",
        all(o.lost == 0 for o in result if not o.stream.startswith("hc-historian")),
        ", ".join(f"{o.stream}: {o.lost} lost" for o in result if not o.stream.startswith("hc-historian")),
    )
    result = recover("High Country uplink to ShadeCast, restored", "shadecast")
    outages += result
    hc = next(o for o in result if o.stream.startswith("hc-historian"))
    time.sleep(5)
    check(
        "the High Country site returns to the primary tunnel after the repair",
        next_hop_if("hc-fw-edge", "10.20.30.0/24") == "wgs-hc-a" and hc.longest_gap_s <= 1.0,
        f"via {next_hop_if('hc-fw-edge', '10.20.30.0/24')}, outage on the way back {hc.longest_gap_s} s",
    )

    # Fault 2: the head office loses its ShadeCast uplink, silently.
    wait_converged(30)
    result = fault("head office uplink to ShadeCast, silent", "shadecast", ["sc-fr"])
    outages += result
    worst = max(o.longest_gap_s for o in result)
    check(
        "every site keeps the head office when it loses one of its two ISPs",
        all(o.longest_gap_s <= 5.0 for o in result) and all(reachable(h, HQ_HOST_ADDRESS, 2) for h in SITE_HOSTS),
        f"longest outage {worst} s; ShadeCast now reaches the head office over {bgp_path('shadecast', HQ_PREFIX)!r}",
    )
    check(
        "the fault was real: ShadeCast dropped packets on the head office's link and rerouted through its peer",
        result[0].fault_drops > 0 and bgp_path("shadecast", HQ_PREFIX) == "64501 64505",
        f"{result[0].fault_drops} packets dropped; High Country underlay {result[0].underlay_before} -> {result[0].underlay_after}",
    )
    facts["path, hc-historian to fr-historian, head office on one ISP"] = site_path("hc-historian")
    outages += recover("head office uplink to ShadeCast, restored", "shadecast")
    time.sleep(5)

    # Fault 3: all of ShadeCast goes dark.
    wait_converged(30)
    result = fault("ShadeCast outage, silent", "shadecast", None, seconds=35.0)
    outages += result
    worst = max(o.longest_gap_s for o in result)
    check(
        "every site keeps the head office through the loss of a whole ISP",
        all(reachable(h, HQ_HOST_ADDRESS, 2) for h in SITE_HOSTS),
        f"longest outage {worst} s; High Country via {next_hop_if('hc-fw-edge', '10.20.30.0/24')}, "
        f"Western Slope via {next_hop_if('ws-fw-edge', '10.20.30.0/24')}",
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
    # two customer routes tie), so the Gulf Coast site was never on the path.
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
    gc = next(o for o in result if o.stream.startswith("gc-historian"))
    check(
        "a site whose path crosses the dead ISP in the core waits for the BGP hold timer",
        through == "64500 64505" and BFD_INTERVAL_MS * BFD_MULTIPLIER / 1000 < gc.longest_gap_s <= CORE_HOLD_S + 3,
        f"cb-west reached the head office over {through!r}; Gulf Coast lost {gc.longest_gap_s} s "
        f"against a {CORE_HOLD_S} s hold time; underlay {gc.underlay_before} -> {gc.underlay_after}",
    )
    outages += recover("ShadeCast outage, restored again", "shadecast", seconds=35.0)
    ns("cb-west", "vtysh", "-N", "cb-west", "-c", "configure terminal",
       "-c", "router bgp 64496", "-c", "address-family ipv4 unicast",
       "-c", f"neighbor {shadecast_address} route-map FROM-CUSTOMER in",
       "-c", "exit", "-c", "exit",
       "-c", "no route-map FROM-CUSTOMER-PREFERRED")  # fmt: skip
    time.sleep(8)

    if PIN == PIN_MARK:
        # A loud fault: the link of High Country's ShadeCast uplink goes down,
        # and the route of the tunnel's table goes with it.
        wait_converged(60)
        uplink = link_of("hc-fw-edge", "shadecast").ifname("hc-fw-edge")
        tunnel = next(t for t in TUNNELS if t.name == "hc-a")
        ns("hc-fw-edge", "ip", "link", "set", uplink, "down")
        time.sleep(6)
        lookup = ns("hc-fw-edge", "ip", "route", "get", HUB_ENDPOINT, "mark", str(tunnel.mark()), check=False)
        stayed = "dev" not in lookup or f"dev {uplink}" in lookup
        check(
            "with its uplink's link down, a tunnel does not move to the other uplink",
            stayed
            and next_hop_if("hc-fw-edge", "10.20.30.0/24") == "wgs-hc-b"
            and reachable("hc-historian", HQ_HOST_ADDRESS),
            f"lookup for hc-a: {' '.join(lookup.split()) or 'no route (blackhole)'}; "
            f"traffic via {next_hop_if('hc-fw-edge', '10.20.30.0/24')}",
        )
        ns("hc-fw-edge", "ip", "link", "set", uplink, "up")
        started = time.monotonic()
        while time.monotonic() - started < 60 and next_hop_if("hc-fw-edge", "10.20.30.0/24") != "wgs-hc-a":
            time.sleep(1)
        check(
            "the tunnel returns when the link does",
            next_hop_if("hc-fw-edge", "10.20.30.0/24") == "wgs-hc-a",
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

    # Scenario: Pelican announces the head office's prefix (an origin hijack).
    before = {name: bgp_path(name, HQ_PREFIX) for name in ("cb-west", "cb-south", "shadecast", "tumbleweed")}
    ns("pelican", "vtysh", "-N", "pelican", "-c", "configure terminal",
       "-c", f"ip route {HQ_PREFIX} blackhole",
       "-c", f"ip prefix-list OWN seq 50 permit {HQ_PREFIX}",
       "-c", "router bgp 64503", "-c", "address-family ipv4 unicast", "-c", f"network {HQ_PREFIX}")  # fmt: skip
    time.sleep(10)
    after = {name: bgp_path(name, HQ_PREFIX) for name in before}
    facts["hijack, AS path to the head office's prefix before"] = json.dumps(before)
    facts["hijack, AS path to the head office's prefix after"] = json.dumps(after)
    hijack_reach = {host: reachable(host, HQ_HOST_ADDRESS, 2) for host in SITE_HOSTS}
    check(
        "an origin hijack by Pelican takes the transit carrier's traffic and cuts off the site behind Pelican",
        after["cb-south"] == "64503" and not hijack_reach["gc-historian"],
        f"paths {json.dumps(after)}; reach {json.dumps(hijack_reach)}",
    )
    check(
        "the head office's own ISPs still prefer their customer route, so their sites stay up",
        after["shadecast"] == "64505" and hijack_reach["hc-historian"] and hijack_reach["ws-historian"],
        f"shadecast {after['shadecast']!r}, tumbleweed {after['tumbleweed']!r}",
    )
    ns("pelican", "vtysh", "-N", "pelican", "-c", "configure terminal",
       "-c", "router bgp 64503", "-c", "address-family ipv4 unicast", "-c", f"no network {HQ_PREFIX}",
       "-c", "exit", "-c", "exit",
       "-c", f"no ip route {HQ_PREFIX} blackhole",
       "-c", "no ip prefix-list OWN seq 50")  # fmt: skip
    started = time.monotonic()
    while time.monotonic() - started < 90 and not reachable("gc-historian", HQ_HOST_ADDRESS, 2):
        time.sleep(1)
    took = time.monotonic() - started
    check(
        "the site behind Pelican comes back when the announcement is withdrawn",
        reachable("gc-historian", HQ_HOST_ADDRESS),
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
