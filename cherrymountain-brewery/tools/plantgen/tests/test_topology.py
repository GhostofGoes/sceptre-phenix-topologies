"""plantgen: the phenix topology file of each site (topology-site-<code>.yml)."""

from __future__ import annotations

import copy
import ipaddress
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from plantgen import fleet, sites, topology
from plantgen.cli import DEFAULT_PLANT
from plantgen.model import PlantError, PlantFile, load_file

CONFIGS: Path = DEFAULT_PLANT.parents[1] / "phenix-configs"
CODES: list[str] = ["fr", "ep", "cs", "fc"]
Node = dict[str, Any]


@pytest.fixture(scope="module")
def plant_file() -> PlantFile:
    return load_file(DEFAULT_PLANT)


@pytest.fixture(scope="module")
def wan() -> dict[str, Any]:
    return sites.load_wan()


@pytest.fixture(scope="module")
def specs(plant_file: PlantFile) -> dict[str, sites.SiteSpec]:
    return {site.code: site for site in sites.load(plant=plant_file)}


@pytest.fixture(scope="module")
def today() -> dict[str, Node]:
    """The nodes of the hand-written modules."""
    return topology.load_nodes(CONFIGS)


@pytest.fixture(scope="module")
def docs(
    plant_file: PlantFile, specs: dict[str, sites.SiteSpec], wan: dict[str, Any]
) -> dict[str, PlantFile]:
    """The plant description of each site."""
    return {
        code: fleet.site_plant(site, plant_file, wan) for code, site in specs.items()
    }


@pytest.fixture(scope="module")
def texts(
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    docs: dict[str, PlantFile],
    today: dict[str, Node],
) -> dict[str, str]:
    """The text of each site's topology file, as plantgen renders it now."""
    return {
        code: topology.render(site, plant_file, docs[code], today)
        for code, site in specs.items()
    }


@pytest.fixture(scope="module")
def nodes(texts: dict[str, str]) -> dict[str, dict[str, Node]]:
    """The nodes of each site's file, by hostname."""
    found: dict[str, dict[str, Node]] = {}
    for code, text in texts.items():
        listed = yaml.safe_load(text)["spec"]["nodes"]
        found[code] = {node["general"]["hostname"]: node for node in listed}
        assert len(found[code]) == len(listed), code
    return found


def _interfaces(node: Node) -> list[dict[str, Any]]:
    listed: list[dict[str, Any]] = node["network"]["interfaces"]
    return listed


def _routes(node: Node) -> list[tuple[str, str]]:
    return [
        (route["destination"], route["next"])
        for route in node["network"].get("routes", [])
    ]


def _addresses(node: Node) -> Iterator[str]:
    """Every address that a node holds, apart from the destination of a route."""
    for interface in _interfaces(node):
        yield interface["address"]
        yield from interface.get("dns", [])
        if "gateway" in interface:
            yield interface["gateway"]
    for _destination, via in _routes(node):
        yield via


# -- the files ------------------------------------------------------------------


@pytest.mark.parametrize("code", CODES)
def test_committed_topology_files_are_current(code: str, texts: dict[str, str]) -> None:
    path = CONFIGS / f"topology-site-{code}.yml"
    assert path.read_text(encoding="utf-8") == texts[code]


def test_phenix_configs_holds_a_file_for_each_site_and_no_other_site() -> None:
    names = sorted(path.name for path in CONFIGS.glob("topology-site-*"))
    assert names == sorted(f"topology-site-{code}.yml" for code in CODES)


@pytest.mark.parametrize("code", CODES)
def test_a_file_says_what_it_is_and_what_it_lacks(
    code: str, texts: dict[str, str]
) -> None:
    lines = texts[code].splitlines()
    comment = [
        line for line in lines[: lines.index("apiVersion: phenix.sandia.gov/v1")]
    ]
    assert comment and all(line.startswith("#") for line in comment)
    head = "\n".join(comment)
    assert head.startswith(f"# cherrymountain-site-{code}: ")
    for words in (
        "plant/plant.yaml and plant/sites.yaml",
        "Do not edit.",
        f"rulesets are not here. They are in scenario-site-{code}.yml",
        "conduits between sites",
        "a whole scenario",
    ):
        assert words in head, words
    doc = yaml.safe_load(texts[code])
    assert (doc["kind"], doc["metadata"]) == (
        "Topology",
        {"name": f"cherrymountain-site-{code}"},
    )
    # Block style, as the prek hook for phenix files wants it.
    assert (
        "{" not in texts[code].replace("${BRANCH_NAME}", "") and "[" not in texts[code]
    )


def test_a_firewall_gets_its_rulesets_from_the_sites_scenario(
    nodes: dict[str, dict[str, Node]],
) -> None:
    """No topology file holds a ruleset. Each firewall of a site has its
    rulesets in the `vrouter` app of scenario-site-<code>.yml, which netcheck
    writes and checks: one for each interface, default drop."""
    for code, found in nodes.items():
        routers = {name for name, node in found.items() if node["type"] == "Firewall"}
        assert len(routers) == (1 if code in ("cs", "fc") else 2)
        for node in found.values():
            assert set(node["network"]) <= {"interfaces", "routes"}, node["general"]
            assert "rulesets" not in node["network"]
        scenario = yaml.safe_load(
            (CONFIGS / f"scenario-site-{code}.yml").read_text(encoding="utf-8")
        )
        assert (
            scenario["metadata"]["annotations"]["topology"]
            == f"cherrymountain-site-{code}"
        )
        (vrouter,) = (
            app for app in scenario["spec"]["apps"] if app["name"] == "vrouter"
        )
        assert {host["hostname"] for host in vrouter["hosts"]} == routers
        for host in vrouter["hosts"]:
            acl = host["metadata"]["acl"]
            legs = [leg["name"] for leg in _interfaces(found[host["hostname"]])]
            assert list(acl["egress"]) == legs
            assert {rs["name"] for rs in acl["rulesets"]} == set(acl["egress"].values())
            assert all(rs["default"] == "drop" for rs in acl["rulesets"])


def test_the_node_count_of_each_site(
    nodes: dict[str, dict[str, Node]], docs: dict[str, PlantFile]
) -> None:
    """The counts of PLANS.md 4.1: 39, and 35, 26, and 19 with a sensor."""
    assert {code: len(found) for code, found in nodes.items()} == {
        "fr": 39,
        "ep": 35,
        "cs": 26,
        "fc": 19,
    }
    for code, found in nodes.items():
        extra = set(found) - set(docs[code].hosts)
        assert extra == (set() if code == "fr" else {f"{code}-sensor"})
        assert set(docs[code].hosts) <= set(found)


def test_the_sensor_of_each_layout(nodes: dict[str, dict[str, Node]]) -> None:
    """In the SOC zone behind two firewalls, and in the plant network behind one."""
    where = {
        code: (leg["vlan"], leg["address"], leg["mask"], leg["gateway"])
        for code in ("ep", "cs", "fc")
        for leg in _interfaces(nodes[code][f"{code}-sensor"])
    }
    assert where == {
        "ep": ("EP-SOC", "10.42.10.60", 24, "10.42.10.1"),
        "cs": ("CS-PLANT", "192.168.20.60", 24, "192.168.20.1"),
        "fc": ("FC-PLANT", "10.21.10.60", 24, "10.21.10.1"),
    }
    sensor = nodes["fc"]["fc-sensor"]
    assert sensor["hardware"] == {
        "os_type": "linux",
        "vcpus": 4,
        "memory": 16384,
        "drives": [{"image": "cm-sensor.qc2"}],
    }
    assert sensor["labels"] == {"zone": "plant", "site": "fc"}
    assert nodes["ep"]["ep-sensor"]["labels"]["zone"] == "soc"
    # The sensor is the last node: it waits for nothing and nothing waits for it.
    assert list(nodes["fc"])[-1] == "fc-sensor"


# -- the four files in one experiment -------------------------------------------


def test_the_four_files_share_no_hostname_and_no_address(
    nodes: dict[str, dict[str, Node]],
) -> None:
    hostnames = Counter(name for found in nodes.values() for name in found)
    assert [name for name, count in hostnames.items() if count > 1] == []
    addresses = Counter(
        interface["address"]
        for found in nodes.values()
        for node in found.values()
        for interface in _interfaces(node)
    )
    assert [address for address, count in addresses.items() if count > 1] == []
    for code, found in nodes.items():
        assert all(name.startswith(f"{code}-") for name in found)
        assert all(node["labels"]["site"] == code for node in found.values())


def test_a_vlan_alias_has_one_subnet_in_the_four_files(
    nodes: dict[str, dict[str, Node]], docs: dict[str, PlantFile]
) -> None:
    subnets: dict[str, set[str]] = {}
    owners: dict[str, set[str]] = {}
    for code, found in nodes.items():
        for node in found.values():
            for leg in _interfaces(node):
                subnet = ipaddress.ip_interface(
                    f"{leg['address']}/{leg['mask']}"
                ).network
                subnets.setdefault(leg["vlan"], set()).add(str(subnet))
                owners.setdefault(leg["vlan"], set()).add(code)
    assert {alias: found for alias, found in subnets.items() if len(found) > 1} == {}
    # No two sites share a VLAN, and no two aliases share a subnet.
    assert {alias: codes for alias, codes in owners.items() if len(codes) > 1} == {}
    assert len({next(iter(found)) for found in subnets.values()}) == len(subnets)
    # 53 aliases, as the site-profiles spike counted them.
    assert len(subnets) == 53


@pytest.mark.parametrize("code", CODES)
def test_every_interface_is_on_a_network_of_the_plant_description(
    code: str, nodes: dict[str, dict[str, Node]], docs: dict[str, PlantFile]
) -> None:
    doc = docs[code]
    for name, node in nodes[code].items():
        legs = _interfaces(node)
        assert legs, name
        for leg in legs:
            assert (leg["type"], leg["proto"]) == ("ethernet", "static")
            subnet = ipaddress.ip_network(doc.networks[leg["vlan"]].subnet)
            assert ipaddress.ip_address(leg["address"]) in subnet, name
            assert leg["mask"] == subnet.prefixlen, name
        if name not in doc.hosts:
            continue
        # A host's interfaces are those of the description, in its order.
        described = doc.hosts[name].interfaces
        assert [f"{leg['address']}/{leg['mask']}" for leg in legs] == [
            interface.ip for interface in described.values()
        ]
        router = node["type"] in topology.ROUTER_TYPES
        wanted = [f"eth{n}" for n in range(len(legs))] if router else list(described)
        assert [leg["name"] for leg in legs] == wanted


@pytest.mark.parametrize("code", CODES)
def test_every_name_and_address_in_a_file_is_the_sites_own(
    code: str,
    nodes: dict[str, dict[str, Node]],
    docs: dict[str, PlantFile],
    plant_file: PlantFile,
) -> None:
    """A gateway, a name server, a next hop, and a host to wait for are at the
    site. A route goes to a network of the site."""
    doc = docs[code]
    networks = {
        alias: ipaddress.ip_network(network.subnet)
        for alias, network in doc.networks.items()
    }
    held = {
        interface["address"]
        for node in nodes[code].values()
        for interface in _interfaces(node)
    }
    tunnel = doc.wireguard
    inside = ipaddress.ip_interface(tunnel.server_address).network if tunnel else None
    plain = set(plant_file.hosts) - sites.SHARED_HOSTS
    token = re.compile(
        r"(?<![A-Za-z0-9_./-])(?:([a-z]{2})-)?("
        + "|".join(re.escape(name) for name in sorted(plain, key=len, reverse=True))
        + r")(?![A-Za-z0-9_-])"
    )
    for name, node in nodes[code].items():
        for address in _addresses(node):
            assert address in held, (name, address)
        for destination, _via in _routes(node):
            known = ipaddress.ip_network(destination) in {*networks.values(), inside}
            assert known or destination == "0.0.0.0/0", (name, destination)
        for wait in node.get("delay", {}).get("c2", []):
            assert wait["hostname"] in nodes[code], name
        # Prose names the site's hosts, too.
        described = [node["general"], *node.get("injections", [])]
        for text in (entry.get("description", "") for entry in described):
            assert all(match.group(1) == code for match in token.finditer(text)), text


def test_the_edges_uplinks_are_those_of_the_wan_variant(
    nodes: dict[str, dict[str, Node]],
    specs: dict[str, sites.SiteSpec],
    wan: dict[str, Any],
) -> None:
    """The WAN generator names each edge's uplinks eth0 and eth1 in its
    configuration lists. The site file and topology-wan.yml give them the same
    VLAN alias, address, and mask."""
    listed = yaml.safe_load((CONFIGS / "topology-wan.yml").read_text(encoding="utf-8"))
    theirs = {node["general"]["hostname"]: node for node in listed["spec"]["nodes"]}

    def uplinks(node: Node, count: int) -> list[tuple[str, str, str, int]]:
        return [
            (leg["name"], leg["vlan"], leg["address"], leg["mask"])
            for leg in _interfaces(node)[:count]
        ]

    for code, site in specs.items():
        edge = f"{code}-fw-edge"
        count = len(site.uplinks)
        ours = uplinks(nodes[code][edge], count)
        assert ours == uplinks(theirs[edge], count), edge
        assert [name for name, *_ in ours] == [f"eth{n}" for n in range(count)]
        links = sites.uplink_links(site, wan)
        assert [vlan for _, vlan, *_ in ours] == [alias for alias, _ in links]
        # The uplinks are all of the edge's access links, and nothing else is one.
        for node in (nodes[code][edge], theirs[edge]):
            access = [
                leg["vlan"]
                for leg in _interfaces(node)
                if leg["vlan"].startswith("WAN-")
            ]
            assert access == [vlan for _, vlan, *_ in ours]
    assert sites.uplink_links(specs["fc"], wan) == [
        ("WAN-SC-FC", "203.0.113.8/30"),
        ("WAN-MM-FC", "198.18.102.0/30"),
    ]


def test_an_uplink_needs_a_link_in_wan_yaml(
    specs: dict[str, sites.SiteSpec], wan: dict[str, Any]
) -> None:
    less = copy.deepcopy(wan)
    less["links"] = [link for link in less["links"] if link["a"] != "marmot"]
    with pytest.raises(PlantError, match="no link between fc-fw-edge and marmot"):
        sites.uplink_links(specs["fc"], less)


def test_the_forwarded_ports_of_the_four_sites_do_not_collide(
    nodes: dict[str, dict[str, Node]], today: dict[str, Node]
) -> None:
    """One experiment gives a port of the head node to one VM."""
    ports: Counter[str] = Counter()
    for found in nodes.values():
        for node in found.values():
            for entry in node.get("annotations", {}).get(topology.AUTOTUNNEL, []):
                ports[entry.split(":")[0]] += 1
    assert len(ports) > 9 and set(ports.values()) == {1}
    scada = {
        code: nodes[code][f"{code}-scada-fuxa"]["annotations"][topology.AUTOTUNNEL]
        for code in CODES
    }
    assert scada == {
        "fr": today["scada-fuxa"]["annotations"][topology.AUTOTUNNEL],
        "fc": ["11981:1881"],
        "ep": ["12081:1881"],
        "cs": ["12181:1881"],
    }


# -- a node follows today's node and the site's description ---------------------


def _plain(node: Node) -> Node:
    """A node of `fr` without the prefix on its hostnames and without the site label."""
    text = re.sub(r"(?<![A-Za-z0-9_./-])(?:fr|FR)-", "", yaml.safe_dump(node))
    plain: Node = yaml.safe_load(text)
    assert plain["labels"].pop("site") == "fr"
    return plain


def test_front_range_has_todays_nodes_under_its_own_names(
    nodes: dict[str, dict[str, Node]], today: dict[str, Node]
) -> None:
    """`fr` keeps the network of the single site. So the rules that build an
    interface, a gateway, a name server, a zone label, and a route give what a
    person wrote in the three modules, apart from the edge's WAN side."""
    assert [name.removeprefix("fr-") for name in nodes["fr"]] == [
        name for name in today if name not in sites.SHARED_HOSTS
    ]
    for name, node in nodes["fr"].items():
        source = today[name.removeprefix("fr-")]
        plain = _plain(node)
        assert set(plain) == set(source), name
        ours, theirs = plain.pop("network"), dict(source["network"])
        assert plain == {
            key: value for key, value in source.items() if key != "network"
        }
        if name == "fr-fw-edge":
            # Two access links take the place of the leg on the INET VLAN.
            inner = [leg for leg in theirs["interfaces"] if leg["vlan"] != "INET"]
            assert [
                {**leg, "name": f"eth{n}"} for n, leg in enumerate(inner, start=2)
            ] == ours["interfaces"][2:]
        elif name == "fr-corp-ws":
            # The office PC asks the internet for names, and that host moves.
            (leg,) = theirs["interfaces"]
            assert leg.pop("dns") == ["203.0.113.10"]
            assert ours["interfaces"] == [leg]
        else:
            assert ours["interfaces"] == theirs["interfaces"], name
        # A person wrote 10.20.0.0/16 for the networks behind the OT firewall.
        # The generator names each network, through the same next hop.
        written = {
            (ipaddress.IPv4Network(route["destination"]), route["next"])
            for route in theirs.get("routes", [])
        }
        made = {
            (ipaddress.IPv4Network(route["destination"]), route["next"])
            for route in ours.get("routes", [])
        }
        if name == "fr-fw-edge":
            default = (ipaddress.IPv4Network("0.0.0.0/0"), "203.0.113.1")
            assert default in written and default not in made
            written.remove(default)
        assert all(
            any(net.subnet_of(wide) and via == hop for wide, hop in written)
            for net, via in made
        ), name
        assert all(
            any(net.subnet_of(wide) and via == hop for net, via in made)
            for wide, hop in written
        ), name
    assert _routes(nodes["fr"]["fr-fw-ot"]) == [("0.0.0.0/0", "10.10.20.1")]
    assert ("10.99.0.0/24", "10.10.20.10") in _routes(nodes["fr"]["fr-dmz-jump"])


def test_the_routes_of_each_layout(nodes: dict[str, dict[str, Node]]) -> None:
    """The edge firewall and the DMZ hosts reach the zones behind the OT
    firewall through it. A site with one firewall has no static route."""
    for code in ("cs", "fc"):
        assert not [name for name, node in nodes[code].items() if _routes(node)]
    routed = {name for name, node in nodes["ep"].items() if _routes(node)}
    assert routed == {"ep-fw-edge", "ep-fw-ot", "ep-dmz-jump"}
    assert _routes(nodes["ep"]["ep-fw-ot"]) == [("0.0.0.0/0", "10.12.20.1")]
    edge = _routes(nodes["ep"]["ep-fw-edge"])
    # OPS, HMI, DERLINK, BAS, SOC, and the control network of each of five cells.
    assert len(edge) == 10 and {via for _, via in edge} == {"10.12.20.2"}
    assert ("10.22.42.0/24", "10.12.20.2") in edge
    assert edge == _routes(nodes["ep"]["ep-dmz-jump"])
    # No edge has a default route here: the WAN generator writes its WAN side.
    for code in CODES:
        assert "0.0.0.0/0" not in dict(_routes(nodes[code][f"{code}-fw-edge"]))


def test_the_name_server_of_each_site(nodes: dict[str, dict[str, Node]]) -> None:
    """The site's ot-svc, or at the pub the firewall, which forwards names.
    No interface keeps the single site's address of a host of the internet."""

    def servers(code: str, host: str) -> list[str]:
        (leg, *_) = _interfaces(nodes[code][f"{code}-{host}"])
        found: list[str] = leg.get("dns", [])
        return found

    assert servers("fr", "historian") == ["10.20.30.13"]
    assert servers("ep", "plc-cellar") == ["10.22.30.13"]
    assert servers("cs", "ot-svc") == servers("cs", "ews") == ["192.168.20.43"]
    assert servers("fc", "plc-cellar") == servers("fc", "historian") == ["10.21.10.1"]
    for code in CODES:
        assert servers(code, "corp-ws") == []
        for name, node in nodes[code].items():
            if name != "fc-fw-edge":
                assert "203.0.113.10" not in set(_addresses(node)), name
    # 203.0.113.10 was inet-traffic. In the fleet it is the pub's first uplink.
    assert _interfaces(nodes["fc"]["fc-fw-edge"])[0]["address"] == "203.0.113.10"


def test_a_small_site_waits_only_for_its_own_hosts(
    nodes: dict[str, dict[str, Node]], today: dict[str, Node]
) -> None:
    def waits(node: Node) -> list[str]:
        return [wait["hostname"] for wait in node.get("delay", {}).get("c2", [])]

    assert len(waits(today["scada-fuxa"])) == 5
    assert waits(nodes["fc"]["fc-scada-fuxa"]) == [
        "fc-plc-brewhouse",
        "fc-plc-cellar",
        "fc-plc-utilities",
    ]
    assert waits(nodes["fc"]["fc-hmi-brewhouse"]) == ["fc-scada-fuxa"]
    # The zone label follows the network that the host moved to.
    zones = {name: node["labels"]["zone"] for name, node in nodes["fc"].items()}
    assert (
        zones["fc-scada-fuxa"]
        == zones["fc-plc-cellar"]
        == zones["fc-bas-vent"]
        == "plant"
    )
    assert (zones["fc-corp-ws"], zones["fc-fw-edge"]) == ("office", "edge")
    assert zones["fc-rio-cellar"] == "field-cel" and zones["fc-sim-ctl"] == "mgmt"
    assert nodes["ep"]["ep-plc-cellar"]["labels"] == {"zone": "ctrl-cel", "site": "ep"}
    # An injection keeps its files: a path names a file, not a host of the site.
    source = today["ot-svc"]["injections"]
    assert [
        (entry["src"], entry["dst"]) for entry in nodes["cs"]["cs-ot-svc"]["injections"]
    ] == [(entry["src"], entry["dst"]) for entry in source]


# -- what does not fit ----------------------------------------------------------


def test_a_host_without_a_node_today_is_refused_by_name(
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    docs: dict[str, PlantFile],
    today: dict[str, Node],
) -> None:
    fewer = {name: node for name, node in today.items() if name != "historian"}
    with pytest.raises(
        PlantError,
        match=r"site fc: topology: no hand-written module .* \['fc-historian'\]",
    ):
        topology.site_topology(specs["fc"], plant_file, docs["fc"], fewer)


def test_a_name_server_that_is_no_host_is_refused_by_name(
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    docs: dict[str, PlantFile],
    today: dict[str, Node],
) -> None:
    changed = copy.deepcopy(today)
    changed["ews"]["network"]["interfaces"][0]["dns"] = ["10.20.30.99"]
    with pytest.raises(
        PlantError, match="cs-ews: its name server 10.20.30.99 is no host"
    ):
        topology.site_topology(specs["cs"], plant_file, docs["cs"], changed)


def test_the_sensors_address_must_be_free(
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    docs: dict[str, PlantFile],
    today: dict[str, Node],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(topology, "SENSOR_HOST_NUMBER", 40)
    with pytest.raises(PlantError, match="10.21.10.40 is taken by fc-scada-fuxa"):
        topology.site_topology(specs["fc"], plant_file, docs["fc"], today)


def test_a_missing_module_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(PlantError, match=r"topology-core\.yml does not exist"):
        topology.load_nodes(tmp_path)
