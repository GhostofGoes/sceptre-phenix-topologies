"""The WAN model: what wan/wan.yaml holds, and the lookups that the generators use.

The module keeps one loaded model in module-level names (ROUTERS, LINKS, and the
others), as the wan-bgp spike does. `load()` fills them. The spike's own model
(spikes/wan-bgp/netns_lab.py) holds the same values, and a test compares the two.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any, ClassVar, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field

#: cherrymountain-brewery/wan/wan.yaml
DEFAULT_PATH: Final[Path] = Path(__file__).resolve().parents[3] / "wan" / "wan.yaml"

# How a site ties each tunnel to one uplink. The generators support the mark
# only: a rule sends the tunnel's own packets to a routing table whose default
# route is the uplink (WireGuard by a firewall mark, IPsec by the source address).
PIN_MARK: Final[str] = "mark"
PIN: str = PIN_MARK

WIREGUARD: Final[str] = "wireguard"
VTI: Final[str] = "vti"

RELATION_LOCAL_PREF: Final[dict[str, int]] = {"customer": 200, "peer": 150, "provider": 100}
RELATION_TAG: Final[dict[str, int]] = {"customer": 1, "peer": 2, "provider": 3}
INVERSE_RELATION: Final[dict[str, str]] = {"customer": "provider", "provider": "customer", "peer": "peer"}
RELATIONS: Final[frozenset[str]] = frozenset({"customer", "peer", "provider", "ibgp", "static"})


class WanError(ValueError):
    """wan.yaml is wrong. The message names the entry."""


class _Entry(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class Router(_Entry):
    """One router: an internet router, the head office's edge, or a site's edge."""

    name: str
    #: Short form for interface names.
    short: str
    asn: int
    router_id: str
    title: str
    #: Prefixes this AS originates into the internet.
    originates: list[str] = Field(default_factory=list)
    #: Extra addresses on the loopback (the head office's VPN endpoint, and its NAT address).
    loopbacks: list[str] = Field(default_factory=list)
    #: False for a site edge that has ISP-assigned addresses and no BGP session with its ISPs.
    public_bgp: bool = True
    #: Install the default route of every ISP, not only the best one.
    multipath: bool = False
    #: An ISP sends its customer a default route only while its BGP table holds
    #: one of these prefixes. Empty: the prefixes of its providers.
    default_requires: list[str] = Field(default_factory=list)


class Link(_Entry):
    """A point-to-point underlay link. `a` gets the first host address."""

    a: str
    b: str
    net: str
    #: How `a` sees `b`: customer, peer, provider, ibgp, or static (no BGP).
    relation: str
    #: With relation customer: the prefixes `a` accepts from `b`.
    customer_prefixes: list[str] = Field(default_factory=list)
    bfd: bool = False

    def addresses(self) -> tuple[str, str]:
        hosts = list(ipaddress.ip_network(self.net).hosts())
        return str(hosts[0]), str(hosts[1])

    def ifname(self, end: str) -> str:
        other = self.b if end == self.a else self.a
        return f"{BY_NAME[end].short}-{BY_NAME[other].short}"


class Lan(_Entry):
    """A site LAN with one host on it."""

    router: str
    host: str
    net: str
    advertise_public: bool = False
    #: The VLAN alias and ID of the LAN in the phenix topology: the site's own zone.
    vlan: str = ""
    vlan_id: int = 0

    def gateway(self) -> str:
        return str(next(ipaddress.ip_network(self.net).hosts()))

    def host_address(self) -> str:
        return str(list(ipaddress.ip_network(self.net).hosts())[10])


class Tunnel(_Entry):
    """One tunnel from a site uplink to the head office."""

    name: str
    site: str
    hub: str
    #: The site's uplink that carries the tunnel, as the name of its ISP.
    via: str
    #: The endpoint of the destination pin, which the generators do not use.
    hub_endpoint: str
    port: int
    net: str
    primary: bool

    def endpoint(self) -> str:
        """The head-office address the site is configured to send to."""
        return HUB_ENDPOINT

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


class Timers(_Entry):
    core_keepalive_s: int
    core_hold_s: int
    bfd_interval_ms: int
    bfd_multiplier: int


class Retransmit(_Entry):
    """How long IKE goes on sending a request that gets no answer.

    strongSwan sends it again after `timeout_s`, then after `timeout_s` times
    `base`, and so on, `tries` times, and then gives the peer up. Its own values
    (5 tries, 4 s, 1.8) take 165 s. A site that alone opens the tunnels needs
    that long to notice a head office that came back from a crash.
    """

    tries: int = Field(ge=1, le=10)
    timeout_s: int = Field(ge=1, le=30)
    base: float = Field(ge=1.0, le=5.0)

    def give_up_s(self) -> float:
        """Seconds from the first send until IKE gives the peer up."""
        return sum(self.timeout_s * self.base**n for n in range(self.tries + 1))


class Vpn(_Entry):
    #: `vti` (route-based IPsec) or `wireguard`.
    kind: str
    #: None: strongSwan's own retry schedule.
    ike_retransmit: Retransmit | None = None
    #: Whether the head office opens the IPsec tunnels too (PLANS.md, D21).
    hub_opens: bool
    hub_endpoint: str
    vti_mtu: int


class HeadOffice(_Entry):
    router: str
    prefix: str
    #: What the head office's private addresses become on the internet. An
    #: address of `prefix` that is not the VPN endpoint.
    nat_address: str


class Wan(_Entry):
    """wan.yaml."""

    timers: Timers
    vpn: Vpn
    head_office: HeadOffice
    private: list[str]
    routers: list[Router]
    links: list[Link]
    lans: list[Lan]
    tunnels: list[Tunnel]
    site_blocks: dict[str, list[str]]


# The loaded model. `load()` fills these.
WAN: Wan | None = None
ROUTERS: list[Router] = []
LINKS: list[Link] = []
LANS: list[Lan] = []
TUNNELS: list[Tunnel] = []
BY_NAME: dict[str, Router] = {}
SITE_BLOCKS: dict[str, list[str]] = {}
PRIVATE: tuple[str, ...] = ()
HUB: str = ""
HUB_ENDPOINT: str = ""
HQ_PREFIX: str = ""
NAT_ADDRESS: str = ""
CORE_KEEPALIVE_S: int = 0
CORE_HOLD_S: int = 0
BFD_INTERVAL_MS: int = 0
BFD_MULTIPLIER: int = 0
VTI_MTU: int = 0
KIND: str = VTI
HUB_OPENS: bool = False
IKE_RETRANSMIT: Retransmit | None = None


def _network(value: str, where: str) -> None:
    try:
        ipaddress.ip_network(value)
    except ValueError as ex:
        raise WanError(f"{where}: {value!r} is not a network") from ex


def check(wan: Wan) -> None:
    """Raise WanError for the first entry that does not fit the others."""
    names = [router.name for router in wan.routers]
    for label, values in (("router name", names), ("router short name", [router.short for router in wan.routers])):
        if len(set(values)) != len(values):
            raise WanError(f"a {label} is used twice: {sorted(v for v in set(values) if values.count(v) > 1)}")
    known = set(names)
    pairs: set[frozenset[str]] = set()
    for link in wan.links:
        where = f"link {link.a} to {link.b}"
        if not {link.a, link.b} <= known:
            raise WanError(f"{where}: an end is not a router")
        if link.relation not in RELATIONS:
            raise WanError(f"{where}: relation {link.relation!r} is not one of {sorted(RELATIONS)}")
        _network(link.net, where)
        if frozenset({link.a, link.b}) in pairs:
            raise WanError(f"{where}: a second link between the same routers")
        pairs.add(frozenset({link.a, link.b}))
    for lan in wan.lans:
        if lan.router not in known:
            raise WanError(f"lan of {lan.host}: {lan.router!r} is not a router")
        _network(lan.net, f"lan of {lan.host}")
        if not lan.vlan or not lan.vlan_id:
            raise WanError(f"lan of {lan.host}: it needs a vlan and a vlan_id")
    if wan.head_office.router not in known:
        raise WanError(f"head_office: {wan.head_office.router!r} is not a router")
    office = ipaddress.ip_network(wan.head_office.prefix)
    nat = ipaddress.ip_address(wan.head_office.nat_address)
    if nat not in office or str(nat) == wan.vpn.hub_endpoint:
        raise WanError(
            f"head_office: nat_address {nat} must be in {office} and must not be the VPN endpoint, "
            "which takes UDP 500 and 4500"
        )
    for router in wan.routers:
        for prefix in router.default_requires:
            _network(prefix, f"default_requires of {router.name}")
    if wan.vpn.kind not in {VTI, WIREGUARD}:
        raise WanError(f"vpn: kind {wan.vpn.kind!r} is not {VTI!r} or {WIREGUARD!r}")
    ports: set[int] = set()
    for tunnel in wan.tunnels:
        where = f"tunnel {tunnel.name}"
        if tunnel.hub != wan.head_office.router:
            raise WanError(f"{where}: its hub is not the head office's router")
        if tunnel.site not in known or tunnel.via not in known:
            raise WanError(f"{where}: its site or its ISP is not a router")
        if frozenset({tunnel.site, tunnel.via}) not in pairs:
            raise WanError(f"{where}: the site has no link to {tunnel.via}")
        if ipaddress.ip_network(tunnel.net).prefixlen != 31:
            raise WanError(f"{where}: {tunnel.net} is not a /31")
        if tunnel.port in ports:
            raise WanError(f"{where}: port {tunnel.port} is used twice")
        ports.add(tunnel.port)
    for name, blocks in wan.site_blocks.items():
        if name not in known:
            raise WanError(f"site_blocks: {name!r} is not a router")
        for block in blocks:
            _network(block, f"site_blocks of {name}")


def load(path: Path = DEFAULT_PATH) -> Wan:
    """Read wan.yaml, check it, and make it the loaded model."""
    global WAN, PRIVATE, HUB, HUB_ENDPOINT, HQ_PREFIX, NAT_ADDRESS, KIND, HUB_OPENS, IKE_RETRANSMIT  # noqa: PLW0603
    global CORE_KEEPALIVE_S, CORE_HOLD_S, BFD_INTERVAL_MS, BFD_MULTIPLIER, VTI_MTU  # noqa: PLW0603
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    wan = Wan.model_validate(raw)
    check(wan)
    WAN = wan
    ROUTERS[:] = wan.routers
    LINKS[:] = wan.links
    LANS[:] = wan.lans
    TUNNELS[:] = wan.tunnels
    BY_NAME.clear()
    BY_NAME.update({router.name: router for router in wan.routers})
    SITE_BLOCKS.clear()
    SITE_BLOCKS.update(wan.site_blocks)
    PRIVATE = tuple(wan.private)
    HUB = wan.head_office.router
    HUB_ENDPOINT = wan.vpn.hub_endpoint
    HQ_PREFIX = wan.head_office.prefix
    NAT_ADDRESS = wan.head_office.nat_address
    KIND = wan.vpn.kind
    HUB_OPENS = wan.vpn.hub_opens
    IKE_RETRANSMIT = wan.vpn.ike_retransmit
    CORE_KEEPALIVE_S = wan.timers.core_keepalive_s
    CORE_HOLD_S = wan.timers.core_hold_s
    BFD_INTERVAL_MS = wan.timers.bfd_interval_ms
    BFD_MULTIPLIER = wan.timers.bfd_multiplier
    VTI_MTU = wan.vpn.vti_mtu
    return wan


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


def transit_prefixes(router: Router) -> list[str]:
    """The prefixes that show that an ISP still reaches the rest of the internet.

    `default_requires` if the router has it. Otherwise what the networks of its
    providers originate. An ISP that has no provider gets no list, and its
    default route has no condition.
    """
    if router.default_requires:
        return list(router.default_requires)
    providers = {
        BY_NAME[peer_of(link, router.name)[0]].asn
        for link in LINKS
        if router.name in (link.a, link.b) and relation_seen_by(link, router.name) == "provider"
    }
    return [prefix for other in ROUTERS if other.asn in providers for prefix in other.originates]


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
