"""The sites of the fleet: what plant/sites.yaml holds, and its checks.

A site is the plant of plant.yaml with parts left out and with its networks
moved (docs/multisite/PLANS.md, sections 2 and 4.1). This module reads the
description of each site and makes sure that it fits plant.yaml and
wan/wan.yaml. It writes nothing: plantgen.fleet builds the plant description
of a site from these values, and the command line writes the site's files. The
site-profiles spike (spikes/site-profiles) ran these values, and a test
compares the two.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field

from plantgen.model import PlantError, PlantFile
from plantgen.model import load_file as load_plant

#: cherrymountain-brewery/plant/sites.yaml and cherrymountain-brewery/wan/wan.yaml
_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
DEFAULT_PATH: Final[Path] = _ROOT / "plant" / "sites.yaml"
WAN_PATH: Final[Path] = _ROOT / "wan" / "wan.yaml"

#: The second octets of the `10.` blocks that move by a site's index, and the
#: second octet of MGMT in 172.16.0.0/12.
PRIVATE_BLOCKS: Final[tuple[int, ...]] = (10, 20, 30, 40)
MGMT_SECOND_OCTET: Final[int] = 16
#: Hosts and VLANs that the fleet has once, with no site prefix.
SHARED_HOSTS: Final[frozenset[str]] = frozenset(
    {"isp-rtr", "inet-traffic", "util-rtr", "utility-derms", "vendor-ws"}
)
SHARED_VLANS: Final[frozenset[str]] = frozenset({"INET", "VENDOR", "UTIL", "MIRROR"})
#: The hosts of each module of the single-site range that only the head office has.
MODULES: Final[dict[str, tuple[str, ...]]] = {
    "soc": ("malcolm", "wazuh", "soc-analyst", "soc-purple"),
    "remote": ("dmz-vpn", "dmz-jump"),
}
#: The name of a site's edge router without the site's prefix.
EDGE: Final[str] = "fw-edge"
#: The routers of plant.yaml. A network that only routers are on is not a network
#: of the site.
ROUTERS: Final[frozenset[str]] = frozenset({"fw-edge", "fw-ot", "isp-rtr", "util-rtr"})


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Move(_Entry):
    """Where the hosts of one VLAN of plant.yaml go at a site."""

    vlan: str
    #: The new network, numbered as the Front Range plan numbers it.
    subnet: str
    #: Added to a host's last octet, so that two VLANs can merge.
    shift: int = 0
    #: The router of the new network. None: the network is not routed.
    gateway: str | None = None

    def address(self, old: str) -> str:
        """The address of a host in the new network.

        A router (last octet 1) does not shift."""
        last = int(old.split("/", maxsplit=1)[0].rsplit(".", maxsplit=1)[-1])
        base = ipaddress.ip_network(self.subnet).network_address
        return str(base + last + (self.shift if last != 1 else 0))


class Uplink(_Entry):
    """One access link of a site: the ISP, and the edge's address on the link."""

    isp: str
    address: str


class SiteSpec(_Entry):
    """One brewery site: what it runs, and how its network is cut."""

    code: str = Field(pattern=r"^[a-z]{2}$")
    index: int = Field(ge=0, le=9)
    title: str
    origin: str
    network: str
    modules: list[str] = Field(default_factory=list)
    extra_hosts: list[str] = Field(default_factory=list)
    drop_areas: list[str] = Field(default_factory=list)
    drop_hosts: list[str] = Field(default_factory=list)
    moves: dict[str, Move] = Field(default_factory=dict)
    cells: dict[str, Move] = Field(default_factory=dict)
    time_server: str = "ot-svc"
    uplinks: list[Uplink] = Field(min_length=1)
    sensor: bool = True

    @property
    def one_firewall(self) -> bool:
        return "fw-ot" in self.drop_hosts

    def host(self, name: str) -> str:
        """The name of a host at this site."""
        return name if name in SHARED_HOSTS else f"{self.code}-{name}"

    def vlan(self, name: str) -> str:
        """The alias of a VLAN at this site."""
        return name if name in SHARED_VLANS else f"{self.code.upper()}-{name}"

    def address(self, text: str) -> str:
        """Move every private address in `text` to this site's blocks."""

        def move(match: re.Match[str]) -> str:
            first, second = int(match.group(1)), int(match.group(2))
            if (first == 10 and second in PRIVATE_BLOCKS) or (
                first == 172 and second == MGMT_SECOND_OCTET
            ):
                second += self.index
            return f"{first}.{second}."

        return re.sub(r"\b(10|172)\.(\d{1,3})\.(?=\d{1,3}\.\d{1,3}\b)", move, text)

    def hosts(self, plant: PlantFile) -> list[str]:
        """The hosts of plant.yaml that this site has, without the site's prefix."""
        elsewhere = {
            host
            for module, hosts in MODULES.items()
            if module not in self.modules
            for host in hosts
        } - set(self.extra_hosts)
        left_out = elsewhere | set(self.drop_hosts) | SHARED_HOSTS
        return [name for name in plant.hosts if name not in left_out]

    def routed_networks(self, plant: PlantFile) -> dict[str, str]:
        """VLAN alias at this site -> its subnet.

        For each network that has a router and a host of the site on it."""
        used = {
            vlan
            for (name, _key), (vlan, _address) in site_interfaces(self, plant).items()
            if name not in ROUTERS
        }
        present = set(self.hosts(plant))
        found: dict[str, str] = {}
        for name, network in plant.networks.items():
            if (
                name in SHARED_VLANS
                or name in self.moves
                or network.gateway not in present
            ):
                continue
            found[self.vlan(name)] = self.address(network.subnet)
        for move in (*self.moves.values(), *self.cells.values()):
            if move.gateway is not None:
                found[self.vlan(move.vlan)] = self.address(move.subnet)
        return {vlan: subnet for vlan, subnet in found.items() if vlan in used}


class SitesFile(_Entry):
    """Top level of sites.yaml."""

    sites: list[SiteSpec]


def load_file(path: Path = DEFAULT_PATH) -> list[SiteSpec]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        return SitesFile.model_validate(raw).sites
    except ValueError as ex:
        raise PlantError(f"{path.name}: {ex}") from ex


def check(
    sites: list[SiteSpec], plant: PlantFile, wan: dict[str, Any] | None = None
) -> None:
    """Raise PlantError for the first entry that does not fit plant.yaml or wan.yaml."""
    for label, values in (
        ("code", [site.code for site in sites]),
        ("index", [site.index for site in sites]),
    ):
        if len(set(values)) != len(values):
            raise PlantError(f"sites: a site {label} is used twice: {values}")
    # The FUXA builder and the app name a site as they name a variant.
    for site in sites:
        if site.code in plant.variants:
            raise PlantError(
                f"site {site.code}: its code is the name of a variant of plant.yaml"
            )
    areas = {area.id for area in plant.areas}
    vlans = {
        interface.vlan
        for host in plant.hosts.values()
        for interface in host.interfaces.values()
    }
    module_hosts = {host for hosts in MODULES.values() for host in hosts}
    for site in sites:
        where = f"site {site.code}"
        _known(where, "modules", site.modules, set(MODULES))
        _known(where, "extra_hosts", site.extra_hosts, module_hosts)
        _known(where, "drop_areas", site.drop_areas, areas)
        _known(where, "drop_hosts", site.drop_hosts, set(plant.hosts))
        _known(where, "moves", list(site.moves), vlans)
        _known(
            where,
            "cells",
            list(site.cells),
            {name for name in plant.hosts if name.startswith("plc-")},
        )
        present = set(site.hosts(plant))
        if site.time_server not in present:
            raise PlantError(
                f"{where}: time_server {site.time_server!r} is not a host of the site"
            )
        gone = sorted(set(site.cells) - present)
        if gone:
            raise PlantError(
                f"{where}: cells names PLCs that the site does not have: {gone}"
            )
        for key, move in (*site.moves.items(), *site.cells.items()):
            try:
                ipaddress.ip_network(move.subnet)
            except ValueError as ex:
                raise PlantError(
                    f"{where}: {key}: {move.subnet!r} is not a network"
                ) from ex
            if move.gateway is not None and move.gateway not in present:
                raise PlantError(
                    f"{where}: {key}: gateway {move.gateway!r} is not a host of "
                    "the site"
                )
        _no_address_twice(site, plant)
        if wan is not None:
            _fits_wan(site, plant, wan)


def _known(where: str, field: str, names: list[str], known: set[str]) -> None:
    unknown = sorted(set(names) - known)
    if unknown:
        raise PlantError(
            f"{where}: {field} names {unknown}, which plant.yaml does not have"
        )


def site_interfaces(
    site: SiteSpec, plant: PlantFile
) -> dict[tuple[str, str], tuple[str, str]]:
    """(host, interface) -> (VLAN alias, address), for each host of the site."""
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for name in site.hosts(plant):
        for key, interface in plant.hosts[name].interfaces.items():
            move = site.cells.get(name) if interface.vlan == "CTRL" else None
            move = move or site.moves.get(interface.vlan)
            if move is None:
                found[(name, key)] = (
                    site.vlan(interface.vlan),
                    site.address(interface.ip.split("/")[0]),
                )
            else:
                moved = site.address(move.subnet.rsplit(".", 1)[0] + ".0")
                last = move.address(interface.ip).rsplit(".", 1)[1]
                found[(name, key)] = (
                    site.vlan(move.vlan),
                    moved.rsplit(".", 1)[0] + "." + last,
                )
    return found


def _no_address_twice(site: SiteSpec, plant: PlantFile) -> None:
    """Two VLANs that merge at a site must not give two hosts one address."""
    seen: dict[tuple[str, str], str] = {}
    for (name, _key), (vlan, address) in site_interfaces(site, plant).items():
        other = seen.setdefault((vlan, address), name)
        if other != name:
            raise PlantError(
                f"site {site.code}: {name} and {other} both get {address} on "
                f"{vlan}; give a move a shift"
            )


def _fits_wan(site: SiteSpec, plant: PlantFile, wan: dict[str, Any]) -> None:
    """The site's uplinks are wan.yaml's links, and its networks are in its blocks."""
    where = f"site {site.code}"
    edge = site.host(EDGE)
    links = [link for link in wan["links"] if edge in (link["a"], link["b"])]
    theirs = []
    for link in links:
        hosts = list(ipaddress.ip_network(link["net"]).hosts())
        isp, address = (
            (link["b"], hosts[0]) if link["a"] == edge else (link["a"], hosts[1])
        )
        theirs.append((isp, str(address)))
    ours = [(uplink.isp, uplink.address.split("/")[0]) for uplink in site.uplinks]
    if ours != theirs:
        raise PlantError(
            f"{where}: its uplinks are {ours}, and wan.yaml gives {edge} {theirs}"
        )
    blocks = [ipaddress.ip_network(block) for block in wan["site_blocks"].get(edge, [])]
    for vlan, subnet in site.routed_networks(plant).items():
        network = ipaddress.ip_network(subnet)
        if network.is_private and not any(network.subnet_of(block) for block in blocks):
            raise PlantError(
                f"{where}: {vlan} ({subnet}) is in no block that wan.yaml gives {edge}"
            )
    for lan in wan["lans"]:
        if (
            lan["router"] == edge
            and site.routed_networks(plant).get(lan["vlan"]) != lan["net"]
        ):
            raise PlantError(
                f"{where}: wan.yaml puts its host on {lan['vlan']} ({lan['net']}), "
                f"and the site has {site.routed_networks(plant).get(lan['vlan'])} there"
            )


def uplink_links(site: SiteSpec, wan: dict[str, Any]) -> list[tuple[str, str]]:
    """(VLAN alias, network) of each access link of the site, the primary first.

    Both are the WAN generator's, because the ISP's router is on the same
    link. It names a link `WAN-<a>-<b>` in capitals, with the short names of
    the link's two routers in the order of wan.yaml (tools/wangen,
    `link_vlan`), and the link's network is `net` there. The prefix length of
    the address in sites.yaml is not used.
    """
    short = {router["name"]: router["short"] for router in wan["routers"]}
    edge = site.host(EDGE)
    links: list[tuple[str, str]] = []
    for uplink in site.uplinks:
        ends = {edge, uplink.isp}
        link = next((x for x in wan["links"] if {x["a"], x["b"]} == ends), None)
        if link is None:
            raise PlantError(
                f"site {site.code}: wan.yaml has no link between {edge} and "
                f"{uplink.isp}"
            )
        alias = f"WAN-{short[link['a']]}-{short[link['b']]}".upper()
        links.append((alias, str(link["net"])))
    return links


def load_wan(path: Path = WAN_PATH) -> dict[str, Any]:
    """wan/wan.yaml as data."""
    if not path.is_file():
        raise PlantError(f"{path} does not exist; a site needs it for its uplinks")
    wan: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return wan


def load(
    path: Path = DEFAULT_PATH,
    plant: PlantFile | None = None,
    wan_path: Path | None = WAN_PATH,
) -> list[SiteSpec]:
    """Read sites.yaml and check it against plant.yaml and, when it exists, wan.yaml."""
    sites = load_file(path)
    plant = plant or load_plant(_ROOT / "plant" / "plant.yaml")
    wan = None
    if wan_path is not None and wan_path.is_file():
        wan = yaml.safe_load(wan_path.read_text(encoding="utf-8"))
    check(sites, plant, wan)
    return sites
