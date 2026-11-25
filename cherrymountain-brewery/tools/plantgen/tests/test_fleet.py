"""plantgen: the files of each site of the fleet (plant/generated/fleet/<code>)."""

from __future__ import annotations

import copy
import ipaddress
import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from plantgen import checks, fleet, injects, render, sites, topology
from plantgen.allocate import allocate
from plantgen.cli import DEFAULT_PLANT, generate_site, main, render_checked
from plantgen.model import (
    NtpHost,
    NtpSource,
    Plant,
    PlantError,
    PlantFile,
    expand,
    load_file,
)
from plantgen.render import render_all

FLEET: Path = DEFAULT_PLANT.parent / "generated" / fleet.DIRECTORY
CODES: list[str] = ["fr", "ep", "cs", "fc"]
#: The five federate keys of plant.yaml that a slice does not get.
CELLAR_OUTPUTS: set[str] = {"cellar/co2.draw_slpm", "cellar/beer.supply_psia"}
CELLAR_INPUTS: set[str] = {
    "brewhouse/knockout.gpm",
    "building/vent.co2_relay_ok",
    "packaging/beer.draw_gpm",
}


@pytest.fixture(scope="module")
def plant_file() -> PlantFile:
    return load_file(DEFAULT_PLANT)


@pytest.fixture(scope="module")
def whole_plant() -> dict[str, Any]:
    """plant.yaml as data. `_changed` gives a test its own copy."""
    data: dict[str, Any] = yaml.safe_load(DEFAULT_PLANT.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def specs(plant_file: PlantFile) -> dict[str, sites.SiteSpec]:
    return {site.code: site for site in sites.load(plant=plant_file)}


@pytest.fixture(scope="module")
def wan() -> dict[str, Any]:
    return sites.load_wan()


@pytest.fixture(scope="module")
def plants(
    plant_file: PlantFile, specs: dict[str, sites.SiteSpec], wan: dict[str, Any]
) -> dict[str, Plant]:
    """Each site, expanded as a variant is."""
    return {
        code: fleet.expand_site(site, plant_file, wan) for code, site in specs.items()
    }


@pytest.fixture(scope="module")
def site_files(plants: dict[str, Plant]) -> dict[str, dict[str, str]]:
    """The rendered files of each site."""
    return {code: render_all(plant, allocate(plant)) for code, plant in plants.items()}


def _changed(whole_plant: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(whole_plant)


def _helics(files: dict[str, str]) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(files["helics.json"])
    return doc


def _keys(federate: dict[str, Any], part: str) -> set[str]:
    return {entry["key"] for entry in federate[part]}


# -- every site generates -------------------------------------------------------


def test_sites_yaml_has_the_sites_that_this_file_tests(
    specs: dict[str, sites.SiteSpec],
) -> None:
    assert list(specs) == CODES


@pytest.mark.parametrize("code", CODES)
def test_every_site_passes_every_check(
    code: str, plants: dict[str, Plant], site_files: dict[str, dict[str, str]]
) -> None:
    """The database checks and the cross-file checks that a variant passes."""
    plant = plants[code]
    alloc = allocate(plant)
    assert checks.plant_checks(plant, alloc) == []
    assert checks.file_checks(plant, alloc, site_files[code]) == []
    for tag in plant.tags:
        assert tag.owner in plant.hosts, tag.name


@pytest.mark.parametrize("code", CODES)
def test_committed_site_files_are_current(
    code: str,
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    wan: dict[str, Any],
) -> None:
    """generated/fleet/<code> holds exactly what plantgen renders now.

    The site's copies of injected files, under `injects/`, are another test's
    (test_site_set.py)."""
    files = generate_site(specs[code], plant_file, wan)
    root = FLEET / code
    have = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and injects.DIRECTORY not in p.relative_to(root).parts
    }
    assert have == set(files)
    for rel, text in files.items():
        assert (root / rel).read_text(encoding="utf-8") == text, rel


def test_the_fleet_directory_holds_the_four_sites_and_nothing_else() -> None:
    assert sorted(path.name for path in FLEET.iterdir()) == sorted(CODES)


def test_the_size_of_each_site(
    plants: dict[str, Plant], site_files: dict[str, dict[str, str]]
) -> None:
    """The counts of the site-profiles spike's record (its result.json).

    `fr` is the full variant's 31 files without the two that name a host of the
    internet: the WireGuard block and the capture lists. `ep` has the same
    plant. `cs` has no power area (a PLC with three files, and the DNP3 map).
    `fc` also has no packaging area (two files) and no building SCADA (one).
    """
    assert {code: len(files) for code, files in site_files.items()} == {
        "fr": 29,
        "ep": 29,
        "cs": 25,
        "fc": 22,
    }
    sizes = {
        code: (len(plant.hosts), len(plant.plcs), len(plant.tags))
        for code, plant in plants.items()
    }
    assert sizes == {
        "fr": (39, 5, 516),
        "ep": (34, 5, 516),
        "cs": (25, 4, 458),
        "fc": (18, 3, 365),
    }
    for files in site_files.values():
        assert "phenix/wireguard.yaml" not in files
        assert "scorch/pcap.yaml" not in files


def test_the_brewpub_has_three_plcs_on_one_flat_network(
    plants: dict[str, Plant], site_files: dict[str, dict[str, str]]
) -> None:
    """`fc` has no packaging and no power, and one VLAN for all that controls."""
    plant, files = plants["fc"], site_files["fc"]
    plcs = {name.split("/")[1] for name in files if name.startswith("openplc/")}
    assert plcs == {"fc-plc-brewhouse", "fc-plc-cellar", "fc-plc-utilities"}
    assert not [name for name in files if name.startswith("dnp3/")]
    assert [name for name in files if name.startswith("fuxa/")] == [
        "fuxa/fc-scada-fuxa-tags.json"
    ]
    tags = json.loads(files["tags.json"])
    assert tags["variant"] == "fc"
    assert {tag["area"] for tag in tags["tags"]} == {"BH", "CEL", "UTL", "FAC"}
    helics = _helics(files)
    assert set(helics["federates"]) == {
        "weather",
        "brewhouse",
        "cellar",
        "utilities",
        "building",
    }
    # The site's own broker and ground-truth store, on the site's MGMT block.
    assert helics["broker"]["host"] == "fc-sim-ctl"
    assert helics["broker"]["address"] == "172.17.10.1"
    assert helics["ground_truth"]["endpoint"] == "http://172.17.10.1:9200"
    # A PLC keeps its last octet on FC-PLANT, and the SCADA host moves by 30.
    cellar = plant.hosts["fc-plc-cellar"].interfaces["IF0"]
    assert (cellar.vlan, cellar.ip) == ("FC-PLANT", "10.21.10.12/24")
    devices = {
        device["name"]: device["property"]
        for device in json.loads(files["fuxa/fc-scada-fuxa-tags.json"])["devices"]
    }
    assert devices["fc-plc-cellar"]["address"] == "10.21.10.12"
    assert devices["fc-plc-brewhouse"]["address"].startswith("opc.tcp://10.21.10.11:")
    # The building's controllers share the SCADA host's network: no BBMD.
    assert devices["fc-scada-fuxa-bacnet"] == {
        "address": "10.21.10.40",
        "broadcastAddress": "10.21.10.255",
    }
    master = json.loads(files["openplc/fc-plc-utilities/modbus_master.json"])
    hosts = {device["name"]: device["config"]["host"] for device in master}
    assert hosts == {
        "fc-glycol-chiller": "10.21.14.11",
        "fc-rio-utilities": "10.21.14.10",
        "fc-plc-cellar": "10.21.10.12",
    }
    # The one firewall has a leg in each zone, after its two uplinks.
    edge = plant.hosts["fc-fw-edge"].interfaces
    assert {key: (leg.vlan, leg.ip) for key, leg in edge.items()} == {
        "IF0": ("WAN-SC-FC", "203.0.113.10/30"),
        "IF1": ("WAN-MM-FC", "198.18.102.2/30"),
        "IF2": ("FC-OFFICE", "10.11.10.1/24"),
        "IF3": ("FC-PLANT", "10.21.10.1/24"),
    }


def test_a_site_without_an_area_takes_the_defaults_of_its_federate(
    site_files: dict[str, dict[str, str]],
) -> None:
    """No packaging at `fc`: nobody draws beer. No power at `cs`: the buses are live."""
    fc, cs = _helics(site_files["fc"]), _helics(site_files["cs"])
    assert fc["federates"]["cellar"]["input_defaults"] == {
        "packaging/beer.draw_gpm": 0.0
    }
    assert fc["federates"]["utilities"]["input_defaults"]["power/bus.msb.energized"]
    assert "packaging" in cs["federates"] and "power" not in cs["federates"]
    assert cs["federates"]["packaging"]["input_defaults"] == {
        "power/bus.pkg.energized": 1.0
    }


def test_the_networks_of_each_layout(plants: dict[str, Plant]) -> None:
    """The VLANs of the spike's record, and MIRROR."""
    vlans = {
        code: {alias for alias in plant.file.networks if alias != fleet.MIRROR_VLAN}
        for code, plant in plants.items()
    }
    assert vlans["fc"] == {
        "FC-FIELD-BH",
        "FC-FIELD-CEL",
        "FC-FIELD-UTL",
        "FC-MGMT",
        "FC-OFFICE",
        "FC-PLANT",
        "WAN-SC-FC",
        "WAN-MM-FC",
    }
    assert len(vlans["fr"]) == 16 and len(vlans["ep"]) == 20 and len(vlans["cs"]) == 9
    assert plants["cs"].file.networks["CS-PLANT"].subnet == "192.168.20.0/24"
    assert plants["cs"].file.networks["CS-MGMT"].subnet == "172.19.0.0/16"
    # A control network for each cell at `ep`, each with a leg of the OT firewall.
    legs = {
        leg.vlan: leg.ip for leg in plants["ep"].hosts["ep-fw-ot"].interfaces.values()
    }
    assert "EP-CTRL" not in legs
    assert legs["EP-CTRL-CEL"] == "10.22.42.1/24"
    assert plants["ep"].hosts["ep-plc-cellar"].ip("IF0") == "10.22.42.12"
    # The sensor's zone at a site with two firewalls, with no host of plant.yaml.
    assert legs["EP-SOC"] == "10.42.10.1/24"


@pytest.mark.parametrize("code", CODES)
def test_the_description_agrees_with_the_model_of_sites_yaml(
    code: str,
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    plants: dict[str, Plant],
) -> None:
    """fleet.py and sites.py work out a host's VLAN and address in two ways."""
    site, plant = specs[code], plants[code]
    assert list(plant.hosts) and set(plant.hosts) == {
        site.host(name) for name in site.hosts(plant_file)
    }
    for (name, key), (vlan, address) in sites.site_interfaces(site, plant_file).items():
        if name in sites.ROUTERS:
            continue
        interface = plant.hosts[site.host(name)].interfaces[key]
        assert (interface.vlan, interface.ip.split("/")[0]) == (vlan, address), name
    routed = {
        alias: network.subnet
        for alias, network in plant.file.networks.items()
        if network.gateway is not None
    }
    expected = site.routed_networks(plant_file)
    if site.sensor and not site.one_firewall:
        expected[site.vlan(fleet.SENSOR_VLAN)] = routed[site.vlan(fleet.SENSOR_VLAN)]
    assert routed == expected


def _without_fleet_addresses(node: Any) -> Any:
    """An app block of a site without the address that a time source outside
    the site carries. The full variant has that server as a node, so its
    sources need no address."""
    if isinstance(node, dict):
        outside = node.get("hostname") in sites.SHARED_HOSTS
        return {
            key: _without_fleet_addresses(value)
            for key, value in node.items()
            if not (outside and key == "address")
        }
    if isinstance(node, list):
        return [_without_fleet_addresses(item) for item in node]
    return node


def _without_the_internet(node: Any) -> Any:
    """An app block without the entries of the hosts that the fleet has once.

    An entry is a hostname in a list, a host's own key in a map, or a list
    item whose `hostname`, `src`, or `dst` is such a host.
    """

    def shared(item: Any) -> bool:
        if isinstance(item, dict):
            ends = (str(item.get(key, "")) for key in ("hostname", "src", "dst"))
            return any(end.split("|")[0] in sites.SHARED_HOSTS for end in ends)
        return item in sites.SHARED_HOSTS

    if isinstance(node, dict):
        return {
            key: _without_the_internet(value)
            for key, value in node.items()
            if key not in sites.SHARED_HOSTS
        }
    if isinstance(node, list):
        return [_without_the_internet(item) for item in node if not shared(item)]
    return node


def test_front_range_is_the_full_variant_under_its_own_names(
    plant_file: PlantFile,
    site_files: dict[str, dict[str, str]],
) -> None:
    """`fr` leaves nothing out but the hosts of the internet.

    Without its prefix, a file of `fr` equals the file of the `full` variant.
    The four app blocks that list hosts lack the entries of the internet's
    hosts, and nothing else.
    """
    full = render_checked(expand(plant_file, "full"))
    seen: set[str] = set()
    with_internet = {
        f"phenix/{app}.yaml" for app in ("cherrymountain", "ntp", "ot-sim", "soh")
    }
    # A file that only a site has: who may write its BACnet objects.
    assert render.BACNET_WRITERS_FILE in site_files["fr"]
    assert render.BACNET_WRITERS_FILE not in full
    for rel, text in site_files["fr"].items():
        if rel == render.BACNET_WRITERS_FILE:
            continue
        plain = re.sub(r"(?<![A-Za-z0-9_-])fr-", "", text)
        plain = plain.replace(
            render.SITE_HEADER.format(site="fr"), render.HEADER.format(variant="full")
        )
        plain = plain.replace('"variant": "fr"', '"variant": "full"')
        plain = plain.replace("variant: fr\n", "variant: full\n")
        name = rel.replace("fr-", "")
        seen.add(name)
        if name not in with_internet:
            assert plain == full[name], rel
            continue
        assert plain != full[name], rel
        assert plain.splitlines()[0] == full[name].splitlines()[0]
        whole = _without_the_internet(yaml.safe_load(full[name]))
        ours = _without_fleet_addresses(yaml.safe_load(plain))
        if name == "phenix/cherrymountain.yaml":
            # The app's block of a site names the site.
            assert ours["metadata"].pop("site") == "fr"
        assert ours == whole, rel
    # A site has no helics app block: the cherrymountain app starts its broker.
    assert set(full) - seen == {
        "phenix/helics.yaml",
        "phenix/wireguard.yaml",
        "scorch/pcap.yaml",
    }


# -- no name and no address of another site -------------------------------------


def _strings(node: Any, key: str = "") -> Iterator[tuple[str, str]]:
    """(key, text) for each string of a parsed file.

    A key of a dict is a string too, under the key `<key>`."""
    if isinstance(node, dict):
        for name, value in node.items():
            yield "<key>", str(name)
            yield from _strings(value, str(name))
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value, key)
    elif isinstance(node, str):
        yield key, node


def _blocks(site: sites.SiteSpec, plant: Plant) -> list[ipaddress.IPv4Network]:
    """The company's blocks at the site's index, and the site's own networks."""
    company = [f"10.{block}.0.0/16" for block in sites.PRIVATE_BLOCKS]
    company.append(f"172.{sites.MGMT_SECOND_OCTET}.0.0/16")
    own = [
        network.subnet
        for alias, network in plant.file.networks.items()
        if alias not in sites.SHARED_VLANS
    ]
    return [
        *(ipaddress.IPv4Network(site.address(block)) for block in company),
        *(ipaddress.IPv4Network(subnet) for subnet in own),
    ]


def _foreign(
    code: str,
    files: dict[str, str],
    plant: Plant,
    names: set[str],
    others: list[ipaddress.IPv4Network],
) -> list[str]:
    """What the files of one site hold of a name or an address that is not its own.

    This is the scan of the stamp spike (`leaks`), on parsed files. A hostname
    of plant.yaml must have this site's prefix. Three things look like a
    hostname and are not one, so they keep their plain names:

    - A kind: the value of `type` or `device_type`, and the key of a map of
      device types (`pv-inverter`) or of the Wazuh block.
    - A HELICS device, which a site does not rename: the value of `device`,
      and a key such as `building/bas-vent.co2_ppm` or `relay-main.close`.
    """
    devices = {device.name for device in plant.devices}
    kinds = set(plant.device_types) | {fleet.WAZUH}
    token = re.compile(
        r"(?<![A-Za-z0-9_-])(?:([a-z]{2})-)?("
        + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        + r")(?![A-Za-z0-9_-])"
    )
    address = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")

    def helics_device(text: str) -> bool:
        federate, slash, rest = text.partition("/")
        qualified = bool(slash) and federate in plant.federates
        device, dot, _point = (rest if qualified else text).partition(".")
        return device in devices and (qualified or bool(dot))

    found: list[str] = []
    for rel, body in files.items():
        if rel.endswith(".json"):
            parts = [("", rel), *_strings(json.loads(body))]
        elif rel.endswith(".yaml"):
            parts = [
                ("", rel),
                ("", body.splitlines()[0]),
                *_strings(yaml.safe_load(body)),
            ]
        else:
            parts = [("", rel), *(("", line) for line in body.splitlines())]
        for key, text in parts:
            kind = key in ("type", "device_type") or (key == "<key>" and text in kinds)
            if kind or (key == "device" and text in devices) or helics_device(text):
                continue
            for match in token.finditer(text):
                if match.group(1) != code:
                    found.append(f"{rel}: {match.group(0)} in {text[:60]!r}")
            for hit in address.findall(text):
                if any(ipaddress.IPv4Address(hit) in block for block in others):
                    found.append(f"{rel}: {hit} in {text[:60]!r}")
    return found


@pytest.fixture(scope="module")
def site_hosts(plant_file: PlantFile) -> set[str]:
    """The hostnames of plant.yaml that a site gives its prefix."""
    return set(plant_file.hosts) - sites.SHARED_HOSTS


@pytest.fixture(scope="module")
def blocks(
    specs: dict[str, sites.SiteSpec], plants: dict[str, Plant]
) -> dict[str, list[ipaddress.IPv4Network]]:
    return {code: _blocks(site, plants[code]) for code, site in specs.items()}


@pytest.mark.parametrize("code", CODES)
def test_no_file_of_a_site_holds_a_name_or_an_address_of_another_site(
    code: str,
    plants: dict[str, Plant],
    site_files: dict[str, dict[str, str]],
    site_hosts: set[str],
    blocks: dict[str, list[ipaddress.IPv4Network]],
) -> None:
    """Not a hostname of plant.yaml without this site's prefix, and not an
    address in a block or a network of another site. For `ep`, `cs`, and `fc`
    that excludes every name and every private address of `fr`."""
    others = [block for other in CODES if other != code for block in blocks[other]]
    assert _foreign(code, site_files[code], plants[code], site_hosts, others) == []
    # The scan finds something: each host of the site, under the site's name.
    text = "\n".join(site_files[code].values())
    for name in plants[code].hosts:
        assert name.startswith(f"{code}-") and name in text


def test_the_scan_finds_a_name_or_an_address_of_another_site(
    plants: dict[str, Plant],
    site_files: dict[str, dict[str, str]],
    site_hosts: set[str],
    blocks: dict[str, list[ipaddress.IPv4Network]],
) -> None:
    """The scan above can fail: each kind of leftover is found where it is put."""
    files = site_files["fc"]

    def scan(rel: str, old: str, new: str) -> list[str]:
        assert old in files[rel]
        changed = dict(files, **{rel: files[rel].replace(old, new, 1)})
        return _foreign("fc", changed, plants["fc"], site_hosts, blocks["fr"])

    assert len(scan("tags.json", '"plc": "fc-plc-cellar"', '"plc": "plc-cellar"')) == 1
    assert len(scan("tags.json", '"rio": "fc-bas-vent"', '"rio": "bas-vent"')) == 1
    assert len(scan("phenix/soh.yaml", "src: fc-plc-cellar", "src: fr-plc-cellar")) == 1
    assert len(scan("phenix/soh.yaml", "fc-sim-ctl:", "sim-ctl:")) == 1
    assert len(scan("helics.json", "172.17.10.1", "172.16.10.1")) == 1
    master = "openplc/fc-plc-cellar/modbus_master.json"
    assert len(scan(master, "10.21.12.10", "10.20.12.10")) == 1
    located = "openplc/fc-plc-cellar/located_vars.st"
    assert len(scan(located, "fc-plc-cellar", "plc-cellar")) == 1


def test_a_description_names_the_hosts_of_the_site(
    site_files: dict[str, dict[str, str]],
) -> None:
    """Prose gets the prefix too. A name that is a kind or a device keeps its own."""
    tags = {
        tag["name"]: tag for tag in json.loads(site_files["ep"]["tags.json"])["tags"]
    }
    assert "(peer read from ep-plc-cellar)" in tags["UTL.CIP.XS03.PV"]["desc"]
    vent = tags["FAC.VENT.AT01.PV"]
    assert vent["rio"] == "ep-bas-vent" and vent["device"] == "bas-vent"
    assert vent["helics_key"].startswith("building/bas-vent.")
    app = yaml.safe_load(site_files["ep"]["phenix/cherrymountain.yaml"])
    kinds = {host["hostname"]: host["metadata"]["type"] for host in app["hosts"]}
    assert kinds["ep-sim-ctl"] == "sim-ctl" and kinds["ep-historian"] == "historian"
    assert "wazuh" not in app["metadata"]
    # The head office has the Wazuh manager, and its agents are its own hosts.
    front = yaml.safe_load(site_files["fr"]["phenix/cherrymountain.yaml"])["metadata"]
    assert front["wazuh"]["manager"] == "fr-wazuh"
    assert len(front["wazuh"]["agents"]) == 11
    assert all(name.startswith("fr-") for name in front["wazuh"]["agents"])


# -- the three fixes that the site-profiles spike named -------------------------


def test_the_edge_and_the_office_pc_follow_a_server_of_the_internet(
    plants: dict[str, Plant], site_files: dict[str, dict[str, str]]
) -> None:
    """Clocks: a time source can be outside the site. No site host takes its place."""
    for code, files in site_files.items():
        assert plants[code].spec.ntp.external == ["inet-traffic"]
        assert checks.ntp_hosts(plants[code], allocate(plants[code])) == []
        app = yaml.safe_load(files["phenix/ntp.yaml"])
        entries = {host["hostname"]: host["metadata"] for host in app["hosts"]}
        assert "inet-traffic" not in entries
        for name in ("fw-edge", "corp-ws"):
            # The address is the server's in the fleet (test_site_set.py).
            assert entries[f"{code}-{name}"]["source"] == {
                "hostname": "inet-traffic",
                "interface": "IF0",
                "address": "198.18.110.11",
            }
    # The pub has no ot-svc: its SCADA host serves the site, as it does in ci.
    pub = yaml.safe_load(site_files["fc"]["phenix/ntp.yaml"])
    assert pub["metadata"]["defaultSource"] == {
        "hostname": "fc-scada-fuxa",
        "interface": "IF0",
    }
    entries = {host["hostname"]: host["metadata"] for host in pub["hosts"]}
    assert entries["fc-scada-fuxa"] == {"server": "chronyd"}
    assert entries["fc-plc-cellar"] == {"client": "chrony"}


def _ntp_errors(doc: PlantFile) -> list[str]:
    plant = expand(doc, "fc")
    return checks.ntp_hosts(plant, allocate(plant))


def test_a_time_source_outside_the_variant_must_be_named_and_in_reach(
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    wan: dict[str, Any],
) -> None:
    doc = fleet.site_plant(specs["fc"], plant_file, wan)
    ntp = doc.variants["fc"].ntp
    assert _ntp_errors(doc) == []
    # A field device has no routed network, so it cannot reach the internet.
    outside = NtpSource(
        hostname="inet-traffic", interface="IF0", address="198.18.110.11"
    )
    assert "fc-rio-cellar" not in ntp.hosts
    ntp.hosts["fc-rio-cellar"] = NtpHost(client="chrony", source=outside)
    assert _ntp_errors(doc) == [
        "ntp: fc-rio-cellar cannot reach inet-traffic, which is outside the "
        "variant: no network of the host is routed"
    ]
    del ntp.hosts["fc-rio-cellar"]
    ntp.external = ["inet-traffic", "fc-scada-fuxa"]
    assert _ntp_errors(doc) == ["ntp: external server fc-scada-fuxa is a variant host"]
    # A source that is neither a host of the variant nor named is a mistake.
    ntp.external = []
    assert sorted(_ntp_errors(doc)) == [
        "ntp: fc-corp-ws source inet-traffic is not a server",
        "ntp: fc-fw-edge source inet-traffic is not a server",
    ]


def test_a_variant_gets_the_whole_cellars_keys_by_a_flag_and_not_by_its_name(
    whole_plant: dict[str, Any], site_files: dict[str, dict[str, str]]
) -> None:
    """Variant names: no list in plant.yaml names the variants that get a key."""
    for files in site_files.values():
        cellar = _helics(files)["federates"]["cellar"]
        assert CELLAR_OUTPUTS <= _keys(cellar, "publications")
        assert CELLAR_INPUTS <= _keys(cellar, "subscriptions") | set(
            cellar["input_defaults"]
        )
    data = _changed(whole_plant)
    data["variants"] = {"any-name": data["variants"]["full"]}
    files = render_checked(expand(PlantFile.model_validate(data), "any-name"))
    cellar = _helics(files)["federates"]["cellar"]
    assert CELLAR_OUTPUTS <= _keys(cellar, "publications")
    assert CELLAR_INPUTS <= _keys(cellar, "subscriptions")
    # The flag takes the keys away, and then the utilities' input has no publisher.
    data["variants"]["any-name"]["slice"] = True
    with pytest.raises(PlantError, match=r"no publisher for cellar/co2\.draw_slpm"):
        render_checked(expand(PlantFile.model_validate(data), "any-name"))


def test_the_ci_slice_has_none_of_the_whole_cellars_keys(
    plant_file: PlantFile, whole_plant: dict[str, Any]
) -> None:
    assert plant_file.variants["ci"].slice
    assert not plant_file.variants["full"].slice
    files = render_checked(expand(plant_file, "ci"))
    cellar = _helics(files)["federates"]["cellar"]
    named = _keys(cellar, "publications") | _keys(cellar, "subscriptions")
    assert not (CELLAR_OUTPUTS | CELLAR_INPUTS) & (
        named | set(cellar["input_defaults"])
    )
    # A list of variant names on a key is not in the schema any more.
    data = _changed(whole_plant)
    data["federates"]["cellar"]["outputs"][0]["variants"] = ["full", "lite"]
    with pytest.raises(ValueError, match="variants"):
        PlantFile.model_validate(data)


def test_a_sites_snapshot_asks_only_for_federates_that_the_site_runs(
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    site_files: dict[str, dict[str, str]],
    wan: dict[str, Any],
) -> None:
    """Snapshot: `fc` runs no packaging and no power, `cs` no power."""
    whole = plant_file.snapshot
    assert whole is not None
    kept: dict[str, list[str]] = {}
    for code, site in specs.items():
        snapshot = fleet.site_plant(site, plant_file, wan).snapshot
        assert snapshot is not None
        kept[code] = snapshot.keys
        asked = _keys(
            _helics(site_files[code])["federates"]["weather"], "subscriptions"
        )
        federates = set(_helics(site_files[code])["federates"])
        assert {key.split("/")[0] for key in asked} <= federates - {"weather"}
    assert kept["fr"] == whole.keys and kept["ep"] == whole.keys
    assert set(whole.keys) - set(kept["cs"]) == {"power/*"}
    assert set(whole.keys) - set(kept["fc"]) == {
        "power/*",
        "packaging/bottles.total",
        "packaging/kegs.total",
        "packaging/ttb.bbl",
        "packaging/fill.*",
    }
    # A pattern for every federate stays, and so does the order of the list.
    assert "*/load.*.kw" in kept["fc"]
    assert kept["fc"] == [key for key in whole.keys if key in kept["fc"]]
    # With the whole plant's list, the pub's snapshot asks for what nobody publishes.
    doc = fleet.site_plant(specs["fc"], plant_file, wan)
    assert doc.snapshot is not None
    doc.snapshot.keys = list(whole.keys)
    plant = expand(doc, "fc")
    with pytest.raises(PlantError, match=r"snapshot: key pattern\(s\) \['power/\*'"):
        render_all(plant, allocate(plant))


# -- a site without a building front end ----------------------------------------


def test_the_one_scada_host_of_the_pub_reads_and_operates_its_building(
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    plants: dict[str, Plant],
    site_files: dict[str, dict[str, str]],
) -> None:
    """`fc` has no bas-fuxa, and its building controllers are still there. So
    its SCADA host takes the front end's devices: every point, with the right
    to write. The rule looks at the site's hosts, not at its code."""
    whole = plant_file.hosts["bas-fuxa"]
    assert whole.fuxa is not None
    pub = plants["fc"].hosts["fc-scada-fuxa"]
    assert pub.fuxa is not None
    sources = {source.host: (source.tags, source.write) for source in pub.fuxa}
    assert list(sources) == [
        "fc-plc-brewhouse",
        "fc-plc-cellar",
        "fc-plc-utilities",
        *(f"fc-{source.host}" for source in whole.fuxa),
    ]
    for source in whole.fuxa:
        assert sources[f"fc-{source.host}"] == (["*"], True)
    assert sources["fc-plc-cellar"] == (["*"], False)
    # A site with its own front end changes nothing: the SCADA host reads the
    # CO2 detection from one controller, and writes none of it.
    for code in ("fr", "ep", "cs"):
        kept = plants[code].hosts[f"{code}-scada-fuxa"].fuxa
        assert kept is not None
        assert [source.host for source in kept if source.write] == []
        assert kept[-1].host == f"{code}-bas-vent" and kept[-1].tags != ["*"]
    pub_tags = json.loads(site_files["fc"]["fuxa/fc-scada-fuxa-tags.json"])
    front_end = json.loads(site_files["ep"]["fuxa/ep-bas-fuxa-tags.json"])
    (ours,) = (d for d in pub_tags["devices"] if d["type"] == "BACnet")
    (theirs,) = front_end["devices"]
    assert [(t["name"], t["access"]) for t in ours["tags"]] == [
        (t["name"], t["access"]) for t in theirs["tags"]
    ]
    assert len(ours["tags"]) == 39
    assert sum(tag["access"] == "read-write" for tag in ours["tags"]) == 16
    # Every tag of the pub has a FUXA host that reads it, as at the other sites.
    for code, files in site_files.items():
        read = {
            tag["name"]
            for name, text in files.items()
            if name.startswith("fuxa/")
            for device in json.loads(text)["devices"]
            for tag in device["tags"]
        }
        every = {tag["name"] for tag in json.loads(files["tags.json"])["tags"]}
        assert every == read, code
    soh = yaml.safe_load(site_files["fc"]["phenix/soh.yaml"])["metadata"]
    flows = {(flow["src"], flow["dst"]) for flow in soh["testCustomReachability"]}
    assert ("fc-scada-fuxa", "fc-bas-coldroom|IF0") in flows


def test_an_alarm_that_fuxa_evaluates_needs_a_fuxa_host_that_reads_its_tag(
    plant_file: PlantFile, specs: dict[str, sites.SiteSpec], wan: dict[str, Any]
) -> None:
    """The check that the pub failed before its SCADA host took the building's
    points: two alarms of the building had no FUXA host to raise them."""
    doc = fleet.site_plant(specs["fc"], plant_file, wan)
    scada = doc.hosts["fc-scada-fuxa"]
    assert scada.fuxa is not None
    gone = ("fc-bas-rtu-taproom", "fc-bas-coldroom")
    scada.fuxa = [source for source in scada.fuxa if source.host not in gone]
    plant = expand(doc, "fc")
    alloc = allocate(plant)
    assert checks.file_checks(plant, alloc, render_all(plant, alloc)) == [
        "alarm FAC.RTU1.TT01.ALM_HIGH: FUXA evaluates it, and no FUXA instance "
        "reads FAC.RTU1.TT01.PV",
        "alarm FAC.CR.TT01.ALM_HIGH: FUXA evaluates it, and no FUXA instance "
        "reads FAC.CR.TT01.PV",
    ]


def test_a_front_end_that_is_gone_needs_one_scada_host_for_its_devices(
    plant_file: PlantFile, specs: dict[str, sites.SiteSpec], wan: dict[str, Any]
) -> None:
    site = specs["fc"].model_copy(
        update={"drop_hosts": [*specs["fc"].drop_hosts, "scada-fuxa"]}
    )
    with pytest.raises(
        PlantError,
        match=r"site fc: scada-fuxa is not at the site, and its devices .* has \[\]",
    ):
        fleet.site_plant(site, plant_file, wan)


# -- a description that does not fit --------------------------------------------


def test_a_network_that_loses_its_router_is_refused_by_name(
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    wan: dict[str, Any],
) -> None:
    """`cs` has no fw-ot. Without a move, its building VLAN has no router."""
    moves = {vlan: move for vlan, move in specs["cs"].moves.items() if vlan != "BAS"}
    site = specs["cs"].model_copy(update={"moves": moves})
    with pytest.raises(
        PlantError,
        match="site cs: network BAS has hosts of the site, and its router fw-ot",
    ):
        fleet.site_plant(site, plant_file, wan)


def test_a_site_refuses_a_hostname_that_it_cannot_see(
    whole_plant: dict[str, Any],
    specs: dict[str, sites.SiteSpec],
    wan: dict[str, Any],
) -> None:
    """The app block is free metadata: a new key that names a host must not
    keep a name of the whole plant."""
    data = _changed(whole_plant)
    data["variants"]["full"]["app"]["store"] = {"hosts": ["historian"]}
    plant = PlantFile.model_validate(data)
    with pytest.raises(
        PlantError, match=r"site ep: .* names the hosts \['historian'\]"
    ):
        fleet.site_plant(specs["ep"], plant, wan)


def test_a_site_needs_the_variant_of_the_whole_plant(
    whole_plant: dict[str, Any],
    specs: dict[str, sites.SiteSpec],
    wan: dict[str, Any],
) -> None:
    data = _changed(whole_plant)
    del data["variants"]["full"]
    plant = PlantFile.model_validate(data)
    with pytest.raises(PlantError, match="site fc: plant.yaml has no variant 'full'"):
        fleet.site_plant(specs["fc"], plant, wan)


# -- the command line -----------------------------------------------------------


def test_the_command_writes_and_checks_one_site(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "fc"
    assert main(["--site", "fc", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "sites.yaml fits plant.yaml and wan.yaml (4 sites)" in printed
    # 22 plant files, one copy of an injected file, the topology, the workflow.
    assert f"wrote 25 files to {out}" in printed
    assert (out / "topology-site-fc.yml").is_file()
    assert (out / "phenix-site-fc.yml").is_file()
    assert (out / "injects" / "vrouter" / "cm-vyos-config.sh").is_file()
    # One site, and no variant.
    assert printed.count("wrote") == 1
    assert main(["--check", "--site", "fc", "--out", str(out)]) == 0
    (out / "tags.json").write_text("{}\n", encoding="utf-8")
    (out / "phenix" / "ntp.yaml").unlink()
    assert main(["--check", "--site", "fc", "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "stale: tags.json" in err and "missing: phenix/ntp.yaml" in err


def test_the_command_refuses_a_site_that_sites_yaml_does_not_have(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--site", "zz", "--out", str(tmp_path / "zz")]) == 1
    assert "sites.yaml has no site 'zz'; it has ['fr', 'ep', 'cs', 'fc']" in (
        capsys.readouterr().err
    )
    assert not (tmp_path / "zz").exists()
    # Two things to write, and one directory to write them to.
    assert main(["--variant", "ci", "--site", "fc", "--out", str(tmp_path)]) == 2
    assert main(["--out", str(tmp_path)]) == 2
    # Without sites.yaml next to plant.yaml there is no site to write.
    shutil.copy(DEFAULT_PLANT, tmp_path / "plant.yaml")
    assert main(["--plant", str(tmp_path / "plant.yaml"), "--site", "fc"]) == 1
    assert "has no sites.yaml" in capsys.readouterr().err


def test_one_variant_alone_writes_no_site(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--variant", "ci", "--out", str(tmp_path / "ci")]) == 0
    printed = capsys.readouterr().out
    assert printed.count("wrote") == 1 and fleet.DIRECTORY not in printed


def _tree(tmp_path: Path) -> Path:
    """A copy of what a run for every site reads. Returns its plant.yaml."""
    root = DEFAULT_PLANT.parents[1]
    for rel in (
        "plant/plant.yaml",
        "plant/sites.yaml",
        "wan/wan.yaml",
        "phenix.yml",
        "phenix-wan.yml",
        *(f"phenix-configs/topology-{module}.yml" for module in topology.MODULES),
        *(f"phenix-injects/{path}" for path in injects.RENDERERS),
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(root / rel, tmp_path / rel)
    return tmp_path / "plant" / "plant.yaml"


def test_the_command_writes_every_site_and_check_fails_on_a_stale_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`python -m plantgen` writes the variants and, for each of the four
    sites, its files and its topology file. `--check` fails on a stale file of
    a site, and on what is left of a site that sites.yaml does not have."""
    plant = _tree(tmp_path)
    args = ["--plant", str(plant)]
    made = plant.parent / "generated"
    configs = tmp_path / "phenix-configs"
    assert main(args) == 0
    printed = capsys.readouterr().out
    for name in ("ci", "full", "lite", *(f"fleet/{code}" for code in CODES)):
        assert f"files to {made / name}\n" in printed
    for code in CODES:
        assert f"wrote {configs / f'topology-site-{code}.yml'}\n" in printed
        assert f"wrote {tmp_path / f'phenix-site-{code}.yml'}\n" in printed
    assert sorted(path.name for path in (made / "fleet").iterdir()) == sorted(CODES)
    assert len(list(configs.iterdir())) == len(topology.MODULES) + len(CODES)
    stale = made / "fleet" / "ep" / "phenix" / "ntp.yaml"
    text = stale.read_text(encoding="utf-8")
    stale.write_text(text.replace("inet-traffic", "ep-ot-svc"), encoding="utf-8")
    edited = configs / "topology-site-cs.yml"
    nodes = edited.read_text(encoding="utf-8")
    edited.write_text(nodes.replace("192.168.20.60", "192.168.20.61"), encoding="utf-8")
    (configs / "topology-site-fc.yml").unlink()
    shutil.copytree(made / "fleet" / "fc", made / "fleet" / "xx")
    shutil.copy(edited, configs / "topology-site-xx.yml")
    shutil.copy(tmp_path / "phenix-site-fc.yml", tmp_path / "phenix-site-xx.yml")
    assert main([*args, "--check"]) == 1
    captured = capsys.readouterr()
    assert f"{made / 'fleet' / 'ep'} is out of date" in captured.err
    assert "stale: phenix/ntp.yaml" in captured.err
    assert "stale: topology-site-cs.yml" in captured.err
    assert "missing: topology-site-fc.yml" in captured.err
    for left in (
        made / "fleet" / "xx",
        configs / "topology-site-xx.yml",
        tmp_path / "phenix-site-xx.yml",
    ):
        assert f"{left} is out of date" in captured.err
    for name in ("ci", "full", "lite", "fleet/fr", "fleet/cs", "fleet/fc"):
        assert f"{made / name} is up to date" in captured.out
    assert f"{configs / 'topology-site-fr.yml'} is up to date" in captured.out
    assert main(args) == 0
    assert not (made / "fleet" / "xx").exists()
    assert not (configs / "topology-site-xx.yml").exists()
    assert not (tmp_path / "phenix-site-xx.yml").exists()
    assert stale.read_text(encoding="utf-8") == text
    assert edited.read_text(encoding="utf-8") == nodes
    assert main([*args, "--check", "--site", "fc"]) == 0
    # A site needs wan.yaml for the names of its access links.
    (tmp_path / "wan" / "wan.yaml").unlink()
    assert main([*args, "--site", "fc"]) == 1
    assert "a site needs it for its uplinks" in capsys.readouterr().err


def test_a_variant_cannot_take_the_name_of_the_sites_directory(
    tmp_path: Path, whole_plant: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    data = _changed(whole_plant)
    data["variants"] = {"fleet": data["variants"]["ci"]}
    plant = tmp_path / "plant" / "plant.yaml"
    plant.parent.mkdir()
    plant.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    # Without sites.yaml the name is free.
    assert main(["--plant", str(plant), "--check", "--variant", "fleet"]) == 1
    assert "is out of date" in capsys.readouterr().err
    shutil.copy(sites.DEFAULT_PATH, plant.parent / "sites.yaml")
    assert main(["--plant", str(plant), "--variant", "fleet"]) == 1
    assert "variant name 'fleet'" in capsys.readouterr().err
    assert not (plant.parent / "generated").exists()


#: A sites.yaml that is not YAML, and one that is not a list of sites.
BAD_SITES: dict[str, str] = {
    "not-yaml": "sites: [\n",
    "not-sites": "sites:\n- code: fc\n  index: 99\n  colour: red\n",
}


@pytest.mark.parametrize("bad", list(BAD_SITES.values()), ids=list(BAD_SITES))
def test_a_run_for_one_variant_does_not_read_sites_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    """A variant does not depend on the sites. A run for everything reports a
    bad sites.yaml in one line, and still writes the variants."""
    plant = _tree(tmp_path)
    (plant.parent / "sites.yaml").write_text(bad, encoding="utf-8")
    one = ["--plant", str(plant), "--variant", "ci", "--out", str(tmp_path / "ci")]
    assert main(one) == 0
    assert main([*one, "--check"]) == 0
    captured = capsys.readouterr()
    assert "sites.yaml" not in captured.out + captured.err
    assert main(["--plant", str(plant)]) == 1
    captured = capsys.readouterr()
    said = [line for line in captured.err.splitlines() if line.strip()]
    assert len(said) == 1 and said[0].startswith("plantgen: sites.yaml: "), said
    assert f"files to {plant.parent / 'generated' / 'ci'}\n" in captured.out
