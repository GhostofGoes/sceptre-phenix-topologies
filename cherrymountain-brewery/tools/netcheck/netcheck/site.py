"""The plan and the conduits of one site of the fleet, derived.

A site has no table of its own. Its plan and its conduits come from two
sources: the Front Range tables (``plan``, ``conduits``) and the site's plant
description (plant/sites.yaml, through plantgen's ``fleet.site_plant``).

- A node is the Front Range node under the site's name, with the interfaces of
  the plant description. A host that the site lacks is no node.
- A conduit keeps the pairs of hosts that are both at the site. A pair with an
  end on the internet, at the vendor, at the utility, or at another site is a
  later stage (``Site.left_out`` lists those rows).
- Which firewalls a pair crosses comes from the site's networks and routers
  (``Site.route``). A pair on one network crosses none: its part of the row is
  not enforced at this site. So one row of the matrix can have parts here.

The one fact that the Front Range matrix needed for this is the row
``plc-peer``: there the two PLCs share a network, and at a site with a control
network for each cell the read crosses the OT firewall.
"""

from __future__ import annotations

import ipaddress
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from netcheck import conduits, plan
from netcheck.model import ROOT
from netcheck.reference import Reference


def _plantgen() -> None:
    """Put plantgen on the import path: it holds the model of sites.yaml. It is
    code, so it comes from this checkout also when ``root`` is a copy of the data."""
    path: str = str(ROOT / "tools" / "plantgen")
    if path not in sys.path:
        sys.path.insert(0, path)


#: The Front Range zone of a host that only a site has, by its plain name. The
#: sensor is a device of the SOC wherever its leg is (PLANS.md 4.6): at a site
#: with no SOC zone it stands in the plant network. So the rows from `zone:SOC`
#: carry it (time, names, and syslog at the site's ot-svc), and the rows from
#: the zone of the network that it stands in do not: it is no HMI and no PLC.
SITE_ONLY_ZONES: dict[str, str] = {"sensor": "SOC"}


#: The Front Range VLAN whose hosts are the office.
OFFICE_VLAN: str = "CORP"
#: (VMs, vCPUs, GB) of each site with its sensor, as PLANS.md 4.1 states them
#: ("The size of the range"). A site that is not in this table has no totals
#: to compare, and its run says so.
TOTALS: dict[str, tuple[int, int, int]] = {
    "fr": (39, 62, 151),
    "ep": (35, 47, 82),
    "cs": (26, 37, 69),
    "fc": (19, 27, 55),
}


class SiteError(Exception):
    """A site's reference could not be derived."""


class Pair(BaseModel):
    """One (source host, destination host) of a conduit row at a site."""

    model_config = ConfigDict(frozen=True)

    conduit: str
    src: str
    dst: str
    #: The firewalls that the pair crosses, in the order of the path, each with
    #: the VLAN that the packet arrives on.
    hops: tuple[tuple[str, str], ...]
    #: The source came from a ``zone:`` selector: every host of that VLAN.
    zone_src: bool


class Site(BaseModel):
    """A site's reference and what the derivation found on the way."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    code: str
    reference: Reference
    #: Every pair of an enforced or unenforced row, with its path.
    pairs: tuple[Pair, ...]
    #: Rows of the matrix with no pair at the site, by why: an end is on the
    #: internet, at the vendor, or at the utility (``outside``), or the site
    #: does not have the host (``absent``).
    left_out: dict[str, tuple[str, ...]]
    #: Blocked paths of the Front Range list that no firewall separates here.
    open_paths: tuple[conduits.BlockedProbe, ...]
    #: Pairs of hosts on one zone network (a network with a router).
    same_network_pairs: int
    #: Row ids with a part that crosses no firewall here, and crosses one at
    #: the Front Range.
    flat_rows: tuple[str, ...]
    #: Row ids with a part that crosses no firewall here.
    unfiltered_rows: tuple[str, ...]
    #: Hostname -> (VLAN alias -> address), from the plant description.
    addresses: dict[str, dict[str, str]]
    #: VLAN alias -> hosts on it that are not routers (the sensor included).
    members: dict[str, tuple[str, ...]]
    routers: tuple[str, ...]
    #: The hosts of the office, and the PLCs (see ``checks.check_layout``).
    office: tuple[str, ...] = ()
    plcs: tuple[str, ...] = ()
    doc: Any = None


def codes(root: Path = ROOT) -> list[str]:
    """The codes of the sites in plant/sites.yaml."""
    _plantgen()
    from plantgen import sites  # noqa: PLC0415
    from plantgen.model import PlantError  # noqa: PLC0415

    try:
        return [site.code for site in sites.load_file(root / "plant" / "sites.yaml")]
    except PlantError as exc:
        raise SiteError(f"plant/{exc}") from exc


def load(code: str, root: Path = ROOT) -> Site:
    """Derive the reference of the site ``code``."""
    _plantgen()
    from plantgen import fleet, sites, topology, workflow  # noqa: PLC0415
    from plantgen.model import PlantError, load_file  # noqa: PLC0415

    try:
        plant_file = load_file(root / "plant" / "plant.yaml")
        wan = sites.load_wan(root / "wan" / "wan.yaml")
        found = sites.load(root / "plant" / "sites.yaml", plant_file, root / "wan" / "wan.yaml")
        spec = next((site for site in found if site.code == code), None)
        if spec is None:
            raise SiteError(f"sites.yaml has no site {code!r}; it has {[s.code for s in found]}")
        doc = fleet.site_plant(spec, plant_file, wan)
        # The fleet's ID rule is plantgen's. The single-site IDs that it starts
        # from are this tool's own plan, not the workflow file that plantgen reads.
        single: dict[str, int] = {name: vlan.vid for name, vlan in plan.VLANS.items()}
        bridge: str = workflow.bridge(spec)
        return _derive(
            spec, doc, topology, lambda used: workflow.vlan_ids(spec, used, single, wan), bridge
        )
    except (PlantError, OSError) as exc:
        raise SiteError(str(exc)) from exc


def _derive(
    spec: Any,
    doc: Any,
    topology: Any,
    vlan_ids: Callable[[set[str]], dict[str, int]],
    bridge: str,
) -> Site:
    code: str = spec.code
    here: dict[str, str] = {
        node.name: spec.host(node.name) for node in plan.NODES if spec.host(node.name) in doc.hosts
    }
    routers: set[str] = {n.gateway for n in doc.networks.values() if n.gateway is not None}
    addresses: dict[str, dict[str, str]] = {
        name: {i.vlan: i.ip.split("/")[0] for i in host.interfaces.values()}
        for name, host in doc.hosts.items()
    }
    sensor: dict[str, Any] | None = topology.sensor_node(spec, doc) if spec.sensor else None
    # A host that only a site has (the sensor) has no Front Range name. A row
    # of the matrix carries it through its zone (`SITE_ONLY_ZONES`).
    site_only: dict[str, list[str]] = {}
    if sensor is not None:
        leg: dict[str, Any] = sensor["network"]["interfaces"][0]
        addresses[sensor["general"]["hostname"]] = {leg["vlan"]: leg["address"]}
        site_only[SITE_ONLY_ZONES["sensor"]] = [sensor["general"]["hostname"]]
    members: dict[str, list[str]] = {}
    for name, legs in addresses.items():
        if name not in routers:
            for vlan in legs:
                members.setdefault(vlan, []).append(name)

    def route(src: str, dst: str) -> tuple[tuple[str, str], ...] | None:
        """The routers between two hosts, each with its inbound VLAN.

        Empty when the two share a network. None when no router joins them.
        A router forwards between the networks that it has a leg on, and a
        host with two networks does not.
        """
        if set(addresses[src]) & set(addresses[dst]):
            return ()
        goal: set[str] = set(addresses[dst]) if dst in routers else {next(iter(addresses[dst]))}
        start: list[str] = list(addresses[src]) if src in routers else [next(iter(addresses[src]))]
        paths: dict[str, tuple[tuple[str, str], ...]] = dict.fromkeys(start, ())
        queue: list[str] = list(start)
        while queue:
            vlan: str = queue.pop(0)
            if vlan in goal:
                return paths[vlan]
            for router in sorted(routers - {src}):
                if vlan not in addresses[router]:
                    continue
                for onward in addresses[router]:
                    if onward not in paths:
                        paths[onward] = (*paths[vlan], (router, vlan))
                        queue.append(onward)
        return None

    def members_here(selector: str) -> list[str]:
        """The hosts of the site that a selector of the matrix stands for."""
        names: list[str] = [
            here[name] for name in conduits.expand_selector(selector, "full") if name in here
        ]
        if selector.startswith("zone:"):
            names += site_only.get(selector.removeprefix("zone:"), [])
        return names

    # The name that a DNS probe asks for: the site's zone has the site's names.
    front_query: str = conduits.dns_query(conduits.DNS_PROBE_NAME)
    front_name, _, domain = conduits.DNS_PROBE_NAME.partition(".")
    site_query: str = conduits.dns_query(f"{spec.host(front_name)}.{domain}")

    pairs: list[Pair] = []
    left_out: dict[str, list[str]] = {"outside": [], "absent": []}
    full: set[str] = {n.name for n in plan.nodes_for("full")}
    for cd in conduits.CONDUITS:
        found: list[Pair] = []
        ends: set[str] = set()
        for selector in cd.srcs:
            for dst_selector in cd.dsts:
                for front_src in conduits.expand_selector(selector, "full"):
                    for front_dst in conduits.expand_selector(dst_selector, "full"):
                        ends |= {front_src, front_dst}
                if cd.dst_ip:
                    continue
                for src in members_here(selector):
                    for dst in members_here(dst_selector):
                        if src == dst:
                            continue
                        hops = route(src, dst)
                        if hops is None:
                            raise SiteError(
                                f"site {code}: {cd.id}: no router joins {src} and {dst}"
                            )
                        found.append(
                            Pair(
                                conduit=cd.id,
                                src=src,
                                dst=dst,
                                hops=hops,
                                zone_src=selector.startswith("zone:"),
                            )
                        )
        if not found:
            outside: bool = bool(cd.dst_ip) or any(e in full and _shared(spec, e) for e in ends)
            left_out["outside" if outside else "absent"].append(cd.id)
        pairs += found

    site_conduits: list[conduits.Conduit] = []
    for cd in conduits.CONDUITS:
        mine: list[Pair] = [p for p in pairs if p.conduit == cd.id]
        # One part for each set of firewalls and each set of destinations, so
        # that a part's sources times its destinations are exactly its pairs.
        parts: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = {}
        for src in dict.fromkeys(p.src for p in mine):
            via: dict[tuple[str, ...], list[str]] = {}
            for p in mine:
                if p.src == src:
                    via.setdefault(tuple(sorted({r for r, _ in p.hops})), []).append(p.dst)
            for firewalls, dsts in via.items():
                parts.setdefault((firewalls, tuple(dict.fromkeys(dsts))), []).append(src)
        for (firewalls, dsts), srcs in parts.items():
            probes: list[conduits.Probe] = [
                conduits.Probe(
                    src=here[p.src],
                    dst=here[p.dst],
                    proto=p.proto,
                    port=p.port,
                    packet=site_query if p.packet == front_query else p.packet,
                )
                for p in cd.probes
                if here.get(p.src) in srcs and here.get(p.dst) in dsts
            ]
            if not any(p.proto == "tcp" for p in probes):
                # The Front Range probes use one host for a zone. Where that
                # host is in another part here, the part gets its own probe.
                probes += [
                    conduits.Probe(src=srcs[0], dst=dsts[0], proto="tcp", port=s.port)
                    for s in cd.services
                    if s.proto == "tcp"
                ]
            where: str = "+".join(firewalls) if firewalls else "no firewall"
            site_conduits.append(
                conduits.Conduit(
                    id=cd.id,
                    text=f"{cd.text} [site {code}: {where}]",
                    srcs=tuple(srcs),
                    dsts=dsts,
                    services=cd.services,
                    via=firewalls,
                    enforced=bool(firewalls),
                    probes=tuple(probes),
                    note=cd.note,
                )
            )

    blocked: list[conduits.BlockedProbe] = []
    open_paths: list[conduits.BlockedProbe] = []
    for bp in conduits.BLOCKED_PROBES:
        if bp.src not in here or bp.dst not in here:
            continue
        moved = conduits.BlockedProbe(src=here[bp.src], dst=here[bp.dst], port=bp.port, why=bp.why)
        (open_paths if route(moved.src, moved.dst) == () else blocked).append(moved)

    front_range: dict[str, bool] = {cd.id: cd.enforced for cd in conduits.CONDUITS}
    unfiltered: list[str] = list(dict.fromkeys(p.conduit for p in pairs if not p.hops))
    zones: set[str] = {alias for alias, n in doc.networks.items() if n.gateway is not None}
    same: int = sum(len(m) * (len(m) - 1) // 2 for vlan, m in members.items() if vlan in zones)
    nodes: list[plan.Node] = [
        _node(spec, doc, topology, node, here[node.name], addresses)
        for node in plan.nodes_for("full")
        if node.name in here
    ]
    if sensor is not None:
        nodes.append(_sensor(sensor))
    return Site(
        code=code,
        reference=Reference(
            name=code,
            nodes=tuple(nodes),
            # Every VLAN that a node is on, the routers and the sensor too.
            vlans=_vlans(
                doc, addresses, vlan_ids({vlan for legs in addresses.values() for vlan in legs})
            ),
            conduits=tuple(site_conduits),
            blocked=tuple(blocked),
            external_vlans=frozenset(a for a, _ in _uplinks(spec, doc)),
            site=True,
            bridge=bridge,
            totals=TOTALS.get(code),
        ),
        pairs=tuple(pairs),
        left_out={why: tuple(ids) for why, ids in left_out.items()},
        open_paths=tuple(open_paths),
        same_network_pairs=same,
        flat_rows=tuple(i for i in unfiltered if front_range[i]),
        unfiltered_rows=tuple(unfiltered),
        addresses=addresses,
        members={vlan: tuple(hosts) for vlan, hosts in members.items()},
        routers=tuple(sorted(routers)),
        office=tuple(
            here[n.name]
            for n in plan.nodes_for("full")
            if n.name in here and not n.is_router and n.nics[0].vlan == OFFICE_VLAN
        ),
        plcs=tuple(here[name] for name in plan.PLCS if name in here),
        doc=doc,
    )


def _shared(spec: Any, host: str) -> bool:
    """A host that the fleet has once: it is on the internet, not at a site."""
    name: str = spec.host(host)
    return name == host


def _uplinks(spec: Any, doc: Any) -> list[tuple[str, str]]:
    """(VLAN alias, address) of the edge's legs on its access links."""
    edge = doc.hosts[spec.host("fw-edge")]
    return [(leg.vlan, leg.ip) for leg in list(edge.interfaces.values())[: len(spec.uplinks)]]


def _vlans(
    doc: Any, addresses: dict[str, dict[str, str]], ids: dict[str, int]
) -> dict[str, plan.Vlan]:
    """The networks that a node of the site is on, as rows of the VLAN table,
    each with its ID in the fleet's plan."""
    return {
        alias: plan.Vlan(
            name=alias,
            vid=ids[alias],
            subnet=network.subnet,
            gateway=addresses[network.gateway][alias] if network.gateway else None,
        )
        for alias, network in doc.networks.items()
        if alias in ids
    }


def _node(
    spec: Any,
    doc: Any,
    topology: Any,
    front: plan.Node,
    name: str,
    addresses: dict[str, dict[str, str]],
) -> plan.Node:
    """The Front Range node ``front`` as the site has it.

    The image, the size, and what it waits for are the Front Range plan's. The
    interfaces are the plant description's. The name server follows the plan:
    the site's ot-svc where the plan says ot-svc, the gateway where the site
    has no ot-svc, and none where the plan names a host of the internet."""
    host = doc.hosts[name]
    nics: list[plan.Nic] = []
    for number, (key, leg) in enumerate(host.interfaces.items()):
        address, mask = leg.ip.split("/")
        network = doc.networks.get(leg.vlan)
        gateway: str | None = None
        if not front.is_router and network is not None and network.gateway not in (None, name):
            gateway = addresses[network.gateway][leg.vlan]
        was: plan.Nic | None = next((n for n in front.nics if n.name == key), None)
        ot_svc: dict[str, str] | None = addresses.get(spec.host("ot-svc"))
        dns: list[str] = []
        for server in was.dns if was is not None and not front.is_router else ():
            if server == plan.OT_SVC_IP and ot_svc is not None:
                dns.append(next(iter(ot_svc.values())))
            elif server == plan.OT_SVC_IP and gateway is not None:
                dns.append(gateway)
        nics.append(
            plan.Nic(
                name=f"eth{number}" if front.is_router else key,
                vlan=leg.vlan,
                ip=address,
                mask=int(mask),
                gateway=gateway,
                dns=tuple(dns),
            )
        )
    first: str = next(iter(host.interfaces.values())).vlan
    return plan.Node(
        name=name,
        module=front.module,
        kind=front.kind,
        image=front.image,
        vcpus=front.vcpus,
        mem_gb=front.mem_gb,
        zone=front.zone if front.is_router else first.removeprefix(f"{spec.code.upper()}-").lower(),
        description=front.description,
        nics=tuple(nics),
        # The static routes follow the site's networks: plantgen has the rule.
        routes=tuple(
            plan.Route(**r) for r in topology.static_routes(doc, name, host, front.is_router)
        ),
        autotunnel=tuple(
            f"{int(entry.split(':')[0]) + topology.TUNNEL_STEP * spec.index}:{entry.split(':')[1]}"
            for entry in front.autotunnel
        ),
        delay_c2=tuple(spec.host(d) for d in front.delay_c2 if spec.host(d) in doc.hosts),
    )


def _sensor(node: dict[str, Any]) -> plan.Node:
    """The site's sensor, which the Front Range plan does not have: plantgen's
    stand-in (its image does not exist yet)."""
    leg: dict[str, Any] = node["network"]["interfaces"][0]
    return plan.Node(
        name=node["general"]["hostname"],
        module="core",
        image=node["hardware"]["drives"][0]["image"],
        vcpus=node["hardware"]["vcpus"],
        mem_gb=node["hardware"]["memory"] // 1024,
        zone=node["labels"]["zone"],
        description=node["general"]["description"],
        nics=(
            plan.Nic(
                name=leg["name"],
                vlan=leg["vlan"],
                ip=leg["address"],
                mask=leg["mask"],
                gateway=leg.get("gateway"),
            ),
        ),
    )


def subnet_of(site: Site, vlan: str) -> ipaddress.IPv4Network:
    return site.reference.vlans[vlan].network
