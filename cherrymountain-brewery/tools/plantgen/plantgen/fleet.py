"""The plant description of each site of the fleet.

A site is the plant of plant.yaml with parts left out and with its networks
moved (plant/sites.yaml; docs/multisite/PLANS.md, sections 4.1 and 6).
`site_plant` writes that down as a plant description of the site alone: one
variant, the site's hosts under the site's names, and the site's networks.
The steps that run on a variant of plant.yaml then run on it unchanged:
expand, allocate, check, render.

The site-profiles spike (spikes/site-profiles) ran these edits as a probe.
Three of its workarounds are fixes in plantgen now. A clock can follow a server
outside the site (`NtpSpec.external`), a flag on the variant replaces the lists
of variant names (`VariantSpec.slice`), and a site gets the snapshot keys of
the federates that it runs (`_snapshot`).

The edits are on fields, not on text. A hostname, a VLAN alias, or an address
changes where the schema says that the field holds one. So a name that is also
a kind keeps its meaning: the role `ews`, the device type `pv-inverter`, and
the HELICS device `bas-vent` do not change. `prose` is the one place that
rewrites text.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import re
from collections.abc import Callable, Iterable
from typing import Any, Final

from pydantic import ValidationError

from plantgen import sites
from plantgen.model import (
    FuxaSource,
    HostSpec,
    Interface,
    Network,
    NtpHost,
    NtpSource,
    Plant,
    PlantError,
    PlantFile,
    VariantSpec,
    expand,
)
from plantgen.render import is_bas_front_end
from plantgen.sites import Move, SiteSpec

#: The variant of plant.yaml that is the whole plant. A site is this variant
#: without the parts that the site leaves out.
BASE_VARIANT: Final[str] = "full"
#: The directory of the sites under plant/generated. No variant can have this name.
DIRECTORY: Final[str] = "fleet"
#: The VLAN whose PLCs a site can spread over one network for each cell.
CONTROL_VLAN: Final[str] = "CTRL"
#: The zone of the network sensor at a site that has the zone's router. A
#: site with one firewall has no such zone, and its sensor is in the plant's.
SENSOR_VLAN: Final[str] = "SOC"
#: The floor of every site's layout, whatever else the site merges.
FLOOR: Final[str] = (
    "At every site, the office and a network with a PLC are different networks "
    "with a firewall between them (PLANS.md 4.1)."
)
#: The VLAN of plant.yaml whose hosts are the office, and the role of a PLC.
OFFICE_VLAN: Final[str] = "CORP"
PLC_ROLE: Final[str] = "plc"
#: The plain name of a site's sensor, a node that the topology file adds.
SENSOR_NAME: Final[str] = "sensor"
#: The NTP client of the sensor, a Linux host like the other servers.
SENSOR_NTP_CLIENT: Final[str] = "chrony"
#: The mirror app puts its taps on this VLAN. No host of plant.yaml is on it.
MIRROR_VLAN: Final[str] = "MIRROR"
#: The key in a variant's `app` block that names hosts (see `_leave_out`).
WAZUH: Final[str] = "wazuh"


def site_plant(site: SiteSpec, plant: PlantFile, wan: dict[str, Any]) -> PlantFile:
    """The plant description of `site`, with one variant that has the site's code.

    `plant` is plant.yaml and `wan` is wan/wan.yaml (`sites.load_wan`), which
    names the site's access links.
    """
    try:
        doc = plant.model_copy(deep=True)
        variant = _variant(site, doc)
        _leave_out(doc, variant)
        _clocks(site, doc, variant, wan)
        _snapshot(doc, variant)
        _zones(site, doc)
        _floor(site, doc, variant, plant.hosts)
        doc.variants = {site.code: variant}
        _names(site, doc, plant.hosts)
        _uplinks(site, doc, wan)
        # The edits above assign to fields, which pydantic does not validate.
        return PlantFile.model_validate(doc.model_dump())
    except (PlantError, ValidationError) as exc:
        raise PlantError(f"site {site.code}: {exc}") from exc


def expand_site(site: SiteSpec, plant: PlantFile, wan: dict[str, Any]) -> Plant:
    """`site_plant`, expanded as a variant is. Its `file` is the description."""
    doc = site_plant(site, plant, wan)
    try:
        expanded = expand(doc, site.code)
    except PlantError as exc:
        raise PlantError(f"site {site.code}: {exc}") from exc
    return expanded.model_copy(update={"site": site.code})


def _variant(site: SiteSpec, doc: PlantFile) -> VariantSpec:
    """The site as a variant: its hosts, its areas, and the federates of those areas."""
    if BASE_VARIANT not in doc.variants:
        raise PlantError(
            f"plant.yaml has no variant {BASE_VARIANT!r}, which a site is a part of"
        )
    variant = doc.variants[BASE_VARIANT].model_copy(deep=True)
    if variant.host_overrides:
        raise PlantError(
            f"variant {BASE_VARIANT} has host_overrides "
            f"({sorted(variant.host_overrides)}); a site does not apply them"
        )
    present = set(site.hosts(doc))
    unknown = sorted(present - set(variant.hosts))
    if unknown:
        raise PlantError(f"variant {BASE_VARIANT} does not have the hosts {unknown}")
    kept: set[str] = set()
    dropped: set[str] = set()
    for area in doc.areas:
        federates = {unit.federate or area.federate for unit in area.units}
        if area.id in site.drop_areas:
            dropped |= federates
        else:
            kept |= federates
    variant.description = site.title
    # The order of the whole plant's list, so that a site's files list their
    # hosts as the files of the `full` variant do.
    variant.hosts = [name for name in variant.hosts if name in present]
    variant.areas = [area for area in variant.areas if area not in site.drop_areas]
    variant.federates = [
        name for name in variant.federates if name not in dropped - kept
    ]
    # The other end of the vendor's tunnel (vendor-ws) is a host of the
    # internet, and the fleet has no capture runs yet. The generator of the
    # fleet's scenario decides both.
    variant.wireguard = False
    variant.scorch = False
    return variant


def _leave_out(doc: PlantFile, variant: VariantSpec) -> None:
    """Take out what the site does not have, and every reference to it."""
    hosts = set(variant.hosts)
    _inherit_fuxa_sources(doc, hosts)
    doc.hosts = {name: host for name, host in doc.hosts.items() if name in hosts}
    for host in doc.hosts.values():
        if host.fuxa is not None:
            host.fuxa = [source for source in host.fuxa if source.host in hosts]
        host.upstream = [name for name in host.upstream if name in hosts]
    doc.areas = [area for area in doc.areas if area.id in variant.areas]
    used = {
        device.type
        for area in doc.areas
        for unit in area.units
        for device in unit.devices
    }
    doc.device_types = {
        name: points for name, points in doc.device_types.items() if name in used
    }
    doc.federates = {
        name: federate
        for name, federate in doc.federates.items()
        if name in variant.federates
    }
    variant.federate_sim = {
        name: settings
        for name, settings in variant.federate_sim.items()
        if name in variant.federates
    }
    # `loads` stays whole. A load of a federate that the site does not run
    # still counts for the power federate, at its `default_kw`.
    if doc.soh is not None:
        soh = doc.soh
        soh.skip_hosts = [name for name in soh.skip_hosts if name in hosts]
        soh.host_profiles = {
            name: profile
            for name, profile in soh.host_profiles.items()
            if name in hosts
        }
        soh.conduits = [
            conduit
            for conduit in soh.conduits
            if conduit.src in hosts and conduit.dst in hosts
        ]
    if doc.wireguard is not None and doc.wireguard.server not in hosts:
        doc.wireguard = None
    if doc.scorch is not None:
        captures = {
            run: [target for target in targets if target.split("|")[0] in hosts]
            for run, targets in doc.scorch.captures.items()
        }
        doc.scorch.captures = {
            run: targets for run, targets in captures.items() if targets
        }
    # `app` is the free metadata of the cherrymountain app, so the schema does
    # not say which of its values are hosts. The Wazuh block is the one part
    # that names hosts today. `_names` refuses any other value that is a
    # hostname, so a new key cannot keep a name of the whole plant unseen.
    wazuh: dict[str, Any] | None = variant.app.get(WAZUH)
    if wazuh is not None and wazuh.get("manager") not in hosts:
        # The agents of this site report to the manager of another site. The
        # generator of the fleet's scenario lists them there.
        del variant.app[WAZUH]
    elif wazuh is not None:
        wazuh["agents"] = [name for name in wazuh.get("agents", []) if name in hosts]


def _inherit_fuxa_sources(doc: PlantFile, hosts: set[str]) -> None:
    """The site's one SCADA host takes the devices of a FUXA host that is gone.

    The pub has no BAS front end. Its building controllers are still there, and
    FUXA evaluates their alarms, so the SCADA host reads all of their points.
    It also operates them, as the front end did (`FuxaSource.write`). The rule
    looks at the site's hosts: a FUXA host that the site left out, with a
    device that the site kept.
    """
    scadas = [name for name, host in doc.hosts.items() if host.role == "scada"]
    kept = [name for name in scadas if name in hosts]
    for name in scadas:
        gone = doc.hosts[name]
        left = [source for source in gone.fuxa or [] if source.host in hosts]
        if name in hosts or not left:
            continue
        if len(kept) != 1:
            raise PlantError(
                f"{name} is not at the site, and its devices "
                f"{[source.host for source in left]} need one SCADA host to read "
                f"them; the site has {kept}"
            )
        heir = doc.hosts[kept[0]]
        if heir.fuxa is None:
            # No list means every PLC of the site.
            heir.fuxa = [
                FuxaSource(host=plc)
                for plc, host in doc.hosts.items()
                if host.role == "plc" and plc in hosts
            ]
        mine = {source.host: source for source in heir.fuxa}
        heir.fuxa = [s for s in heir.fuxa if s.host not in {x.host for x in left}]
        for source in left:
            narrow = mine.get(source.host)
            tags = list(source.tags)
            if narrow is not None and "*" not in tags:
                tags = list(dict.fromkeys([*narrow.tags, *tags]))
            write = source.write or is_bas_front_end(gone)
            heir.fuxa.append(FuxaSource(host=source.host, tags=tags, write=write))


def _clocks(
    site: SiteSpec, doc: PlantFile, variant: VariantSpec, wan: dict[str, Any]
) -> None:
    """The site's clocks follow the site's time server.

    A host that follows a server of the internet keeps that source (the edge
    and the office PC follow `inet-traffic`). The server is not a host of the
    site, so the variant names it under `external`, and the source carries the
    server's address in the fleet (`sites.internet_host_address`). Without the
    address, phenix's ntp app would give such a host the site's default
    source: the office PC would then ask a plant host, which the firewall
    drops. With it, the host asks the internet's server at a site alone and in
    the fleet. At a site alone, nothing answers.
    """
    ntp = variant.ntp
    hosts = set(variant.hosts)
    whole = doc.variants[BASE_VARIANT].ntp
    servers = {name for name, entry in whole.hosts.items() if entry.server}
    standard = whole.hosts.get(whole.default_source.hostname)
    ntp.hosts = {name: entry for name, entry in ntp.hosts.items() if name in hosts}
    server = site.time_server
    if server not in ntp.hosts or not ntp.hosts[server].server:
        if standard is None or not standard.server:
            raise PlantError(
                f"variant {BASE_VARIANT}: its default time source "
                f"{whole.default_source.hostname} has no server entry to copy for "
                f"{server}"
            )
        # The site's server runs what the whole plant's server runs.
        ntp.hosts[server] = standard.model_copy(deep=True)
    plant_nic = next(
        (key for key in doc.hosts[server].interfaces if key.startswith("IF")), None
    )
    if plant_nic is None:
        raise PlantError(f"time_server {server} has no plant interface to serve on")
    ntp.default_source = NtpSource(hostname=server, interface=plant_nic)
    sources = {
        entry.source.hostname
        for entry in (*ntp.hosts.values(), *ntp.roles.values())
        if entry.source is not None
    }
    ntp.external = sorted(sources & servers & sites.SHARED_HOSTS)
    # The address is the fleet's, not plant.yaml's: `inet-traffic` leaves the
    # single site's INET segment, and its old address is an uplink of `fc`.
    for entry in (*ntp.hosts.values(), *ntp.roles.values()):
        source = entry.source
        if source is not None and source.hostname in ntp.external:
            source.address = sites.internet_host_address(wan)
    # The sensor is a node of the site's topology file and no host of the plant.
    # It follows the site's server.
    if site.sensor:
        ntp.other_nodes = {SENSOR_NAME: NtpHost(client=SENSOR_NTP_CLIENT)}


def _floor(
    site: SiteSpec, doc: PlantFile, variant: VariantSpec, today: dict[str, HostSpec]
) -> None:
    """Refuse a layout below the floor that every site keeps (`FLOOR`).

    `today` are the hosts of plant.yaml, which say who is an office host: a
    host of the office VLAN there. A site may merge and split its networks as
    it likes above this floor.
    """
    office = [
        name
        for name in variant.hosts
        if name not in sites.ROUTERS
        and any(leg.vlan == OFFICE_VLAN for leg in today[name].interfaces.values())
    ]
    plcs = [name for name in variant.hosts if doc.hosts[name].role == PLC_ROLE]
    of_plc = {
        leg.vlan: name for name in plcs for leg in doc.hosts[name].interfaces.values()
    }
    for name in office:
        for leg in doc.hosts[name].interfaces.values():
            if leg.vlan in of_plc:
                raise PlantError(
                    f"the office host {name} and the PLC {of_plc[leg.vlan]} share "
                    f"the network {leg.vlan}. {FLOOR}"
                )
            network = doc.networks.get(leg.vlan)
            if network is None or network.gateway not in sites.ROUTERS:
                raise PlantError(
                    f"the office network {leg.vlan} of {name} has no firewall as "
                    f"its gateway. {FLOOR}"
                )


def _snapshot(doc: PlantFile, variant: VariantSpec) -> None:
    """The ground-truth snapshot asks only for federates that the site runs."""
    snapshot = doc.snapshot
    if snapshot is None:
        return
    if snapshot.federate not in variant.federates:
        doc.snapshot = None
        return
    others = [name for name in variant.federates if name != snapshot.federate]
    # A key is `<federate>/<name>`, and each part can be a pattern.
    snapshot.keys = [
        key
        for key in snapshot.keys
        if any(fnmatch.fnmatchcase(name, key.split("/")[0]) for name in others)
    ]


def _move(site: SiteSpec, host: str, vlan: str) -> Move | None:
    """Where the interface of `host` on `vlan` goes, as `sites.site_interfaces` says."""
    cell = site.cells.get(host) if vlan == CONTROL_VLAN else None
    return cell or site.moves.get(vlan)


def _legs(interfaces: Iterable[Interface]) -> dict[str, Interface]:
    """A router's interfaces, numbered in their order."""
    return {f"IF{number}": leg for number, leg in enumerate(interfaces)}


def _zones(site: SiteSpec, doc: PlantFile) -> None:
    """The site's own zones: VLANs merged, split, renamed, or renumbered.

    Subnets stay in the numbers of the Front Range plan here. `_names` moves
    the company's blocks by the site's index.
    """
    hosts = doc.hosts
    for name, host in hosts.items():
        for interface in host.interfaces.values():
            move = _move(site, name, interface.vlan)
            if move is None:
                continue
            prefix = interface.ip.split("/")[1]
            interface.ip = f"{move.address(interface.ip)}/{prefix}"
            interface.vlan = move.vlan
            doc.networks[move.vlan] = Network(subnet=move.subnet, gateway=move.gateway)
    # A router gets a leg in each new network that it routes: the one firewall
    # of a small site in its plant zone, `fw-ot` in each cell's network.
    for move in (*site.moves.values(), *site.cells.values()):
        if move.gateway is None:
            continue
        router = hosts[move.gateway]
        if all(leg.vlan != move.vlan for leg in router.interfaces.values()):
            block = ipaddress.ip_network(move.subnet)
            first = f"{block.network_address + 1}/{block.prefixlen}"
            leg = Interface(vlan=move.vlan, ip=first)
            router.interfaces = _legs([*router.interfaces.values(), leg])
    # A network stays when a host that is not a router is on it. The DMZ of a
    # site with one firewall goes this way, and so does the internet's side of
    # the edge, which `_uplinks` replaces.
    kept = {
        interface.vlan
        for name, host in hosts.items()
        if name not in sites.ROUTERS
        for interface in host.interfaces.values()
    }
    kept.add(MIRROR_VLAN)
    sensor = doc.networks.get(SENSOR_VLAN)
    if site.sensor and sensor is not None and sensor.gateway in hosts:
        kept.add(SENSOR_VLAN)
    doc.networks = {
        alias: network for alias, network in doc.networks.items() if alias in kept
    }
    for alias, network in doc.networks.items():
        if network.gateway is not None and network.gateway not in hosts:
            raise PlantError(
                f"network {alias} has hosts of the site, and its router "
                f"{network.gateway} is not at the site; give {alias} a move"
            )
    for name in sites.ROUTERS & hosts.keys():
        router = hosts[name]
        router.interfaces = _legs(
            leg for leg in router.interfaces.values() if leg.vlan in doc.networks
        )


def _uplinks(site: SiteSpec, doc: PlantFile, wan: dict[str, Any]) -> None:
    """The edge gets a link to each ISP of the site, the primary link first.

    A link is a network of its own with the edge's address from sites.yaml.
    Its alias and its subnet are those that the WAN generator gives the link,
    because the ISP's router is on the same VLAN (`sites.uplink_links`). So
    the alias has no site prefix, and this step runs after `_names`. The
    ISP's router is in wan/wan.yaml and not here, so the network has no
    gateway. The uplinks are the edge's first interfaces: the WAN generator's
    configuration lists name them eth0 and eth1.
    """
    name = site.host(sites.EDGE)
    if name not in doc.hosts:
        raise PlantError(f"the site has no {name} to connect its uplinks to")
    edge = doc.hosts[name]
    links: list[Interface] = []
    found = sites.uplink_links(site, wan)
    for (alias, net), uplink in zip(found, site.uplinks, strict=True):
        if alias in doc.networks:
            raise PlantError(f"the uplink alias {alias} is a network of the site")
        subnet = ipaddress.ip_network(net)
        address = uplink.address.split("/")[0]
        if ipaddress.ip_address(address) not in subnet:
            raise PlantError(
                f"the uplink address {address} is not in {net}, the network of "
                f"{alias} in wan.yaml"
            )
        doc.networks[alias] = Network(subnet=net)
        links.append(Interface(vlan=alias, ip=f"{address}/{subnet.prefixlen}"))
    edge.interfaces = _legs([*links, *edge.interfaces.values()])


def prose(site: SiteSpec, hosts: Iterable[str]) -> Callable[[str], str]:
    """A function that gives the hostnames in a description the site's prefix.

    This is the one place that rewrites text, because a description is prose
    and has no fields: "peer read from plc-cellar" becomes "peer read from
    ep-plc-cellar". It is for the fields that people read (`desc`, the name of
    a unit, the cause of an alarm, the description of a node) and for nothing
    that a program reads.
    """
    # A name must stand alone. It does not match inside a longer word, or
    # after the `/` of a HELICS key (`building/bas-vent.co2_ppm`).
    names = sorted(set(hosts) - sites.SHARED_HOSTS, key=len, reverse=True)
    token = re.compile(
        r"(?<![A-Za-z0-9_./-])("
        + "|".join(re.escape(name) for name in names)
        + r")(?![A-Za-z0-9_-])"
    )

    def stamp(text: str) -> str:
        return token.sub(lambda match: site.host(match.group(1)), text)

    return stamp


def _names(site: SiteSpec, doc: PlantFile, plant_hosts: Iterable[str]) -> None:
    """Give every host, VLAN, and address of the description the site's own.

    A hostname gets the site's prefix, a VLAN alias the prefix in capitals,
    and a block of the company moves by the site's index (`SiteSpec.host`,
    `vlan`, and `address`). A host or a VLAN that the fleet has once keeps its
    name. Federate names, HELICS keys, device names, and tag names stay: each
    site has its own federation (PLANS.md, section 4.7).
    """
    host, vlan, address = site.host, site.vlan, site.address
    stamp = prose(site, plant_hosts)

    def optional(name: str | None) -> str | None:
        return host(name) if name is not None else None

    doc.networks = {
        vlan(alias): Network(
            subnet=address(network.subnet), gateway=optional(network.gateway)
        )
        for alias, network in doc.networks.items()
    }
    for federate in doc.federates.values():
        federate.host = host(federate.host)
        for output in federate.outputs:
            output.desc = stamp(output.desc)
        for truth in federate.unit_outputs:
            truth.desc = stamp(truth.desc)
    for load in doc.loads:
        load.desc = stamp(load.desc)
    for points in doc.device_types.values():
        for point in points.values():
            point.written_by = optional(point.written_by)
    doc.hosts = {host(name): spec for name, spec in doc.hosts.items()}
    for spec in doc.hosts.values():
        for interface in spec.interfaces.values():
            interface.vlan = vlan(interface.vlan)
            interface.ip = address(interface.ip)
        spec.upstream = [host(name) for name in spec.upstream]
        for source in spec.fuxa or []:
            source.host = host(source.host)
    for rationale in (
        *doc.alarm_rationale.values(),
        *(
            alarm
            for area in doc.areas
            for unit in area.units
            for tag in unit.tags
            for alarm in tag.alarms
        ),
    ):
        for field in ("cause", "consequence", "action"):
            text: str | None = getattr(rationale, field)
            if text is not None:
                setattr(rationale, field, stamp(text))
    for area in doc.areas:
        area.name = stamp(area.name)
        area.plc = optional(area.plc)
        area.rio = optional(area.rio)
        for unit in area.units:
            unit.name = stamp(unit.name)
            unit.rio = optional(unit.rio)
            for tag in unit.tags:
                tag.desc = stamp(tag.desc)
    if doc.soh is not None:
        soh = doc.soh
        soh.skip_hosts = [host(name) for name in soh.skip_hosts]
        soh.host_profiles = {
            host(name): profile for name, profile in soh.host_profiles.items()
        }
        for conduit in soh.conduits:
            conduit.src, conduit.dst = host(conduit.src), host(conduit.dst)
            conduit.desc = stamp(conduit.desc)
    if doc.wireguard is not None:
        tunnel = doc.wireguard
        tunnel.server, tunnel.client = host(tunnel.server), host(tunnel.client)
        tunnel.server_address = address(tunnel.server_address)
        tunnel.client_address = address(tunnel.client_address)
        tunnel.endpoint = address(tunnel.endpoint)
        tunnel.client_allowed_ips = [address(net) for net in tunnel.client_allowed_ips]
    if doc.scorch is not None:
        doc.scorch.captures = {
            run: [
                "|".join((host(target.split("|")[0]), *target.split("|")[1:]))
                for target in targets
            ]
            for run, targets in doc.scorch.captures.items()
        }
    for variant in doc.variants.values():
        variant.hosts = [host(name) for name in variant.hosts]
        broker = variant.helics.broker
        broker.hostname = host(broker.hostname)
        ntp = variant.ntp
        ntp.default_source.hostname = host(ntp.default_source.hostname)
        ntp.hosts = {host(name): entry for name, entry in ntp.hosts.items()}
        ntp.other_nodes = {
            site.host(name): entry for name, entry in ntp.other_nodes.items()
        }
        for entry in (*ntp.hosts.values(), *ntp.roles.values()):
            if entry.source is not None:
                entry.source.hostname = host(entry.source.hostname)
        wazuh: dict[str, Any] | None = variant.app.get(WAZUH)
        if wazuh is not None:
            wazuh["manager"] = host(wazuh["manager"])
            wazuh["agents"] = [host(name) for name in wazuh["agents"]]
        unseen = sorted(_strings(variant.app) & (set(plant_hosts) - sites.SHARED_HOSTS))
        if unseen:
            raise PlantError(
                f"the app block of variant {BASE_VARIANT} names the hosts {unseen} "
                f"outside its {WAZUH} block; plantgen.fleet must learn that key "
                "to give them the site's prefix"
            )


def _strings(node: Any) -> set[str]:
    """Every string value in a tree of dicts and lists."""
    if isinstance(node, dict):
        return set().union(*(_strings(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_strings(value) for value in node))
    return {node} if isinstance(node, str) else set()
