#!/usr/bin/env python3
"""Build four brewery sites that differ in size and network, and see what breaks.

Needs Python 3.12+ with pyyaml and pydantic; `--schema` also needs
check-jsonschema on PATH and a sceptre-phenix checkout:
  python profiles.py
  python profiles.py --schema ~/sceptre-phenix/schemas --out /tmp/fleet

Three questions (README.md has the result):

1. plantgen: what does a site's file have to say before the generator makes a
   site that is not a copy of the plant: fewer process areas, one firewall, a
   flat control network, a control network for each cell, another address
   plan?
2. phenix: do four sites that differ merge into one experiment under phenix's
   rules (unique hostnames, unique addresses, one VLAN ID per alias) and
   validate against its Topology schema?
3. Size: what does each site cost, and how do the four sit on the three-node
   cluster?

Like the stamp spike this is a probe, not a generator. It edits the plant file
one step at a time and runs the real plantgen after each step, so the record
shows which steps the generator needs and which it does not.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

HERE: Path = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("stamp", HERE.parent / "multisite-stamp" / "stamp.py")
assert _spec and _spec.loader
stamp = importlib.util.module_from_spec(_spec)
sys.modules["stamp"] = stamp
_spec.loader.exec_module(stamp)

EVIDENCE: Path = Path(os.environ.get("CM_EVIDENCE_DIR", "evidence")) / "site-profiles"

SOC_HOSTS: tuple[str, ...] = ("malcolm", "wazuh", "soc-analyst", "soc-purple")
REMOTE_HOSTS: tuple[str, ...] = ("dmz-vpn", "dmz-jump")
# The VLANs of the single-site plan that belong to the internet in a fleet.
INTERNET_VLANS: frozenset[str] = frozenset({"INET", "VENDOR", "UTIL"})


class Move(BaseModel):
    """Where the hosts of one of today's VLANs go at a site."""

    vlan: str
    # The new /24, numbered as the Front Range plan numbers it. The stamp then
    # adds the site's number to a `10.` block and leaves any other block alone.
    subnet: str
    # Added to a host's last octet, so that two VLANs can merge without a clash.
    shift: int = 0
    # The router of the new network, or None for an unrouted one.
    gateway: str | None = None

    def address(self, old: str) -> str:
        last = int(old.split("/")[0].split(".")[-1])
        base = ipaddress.ip_network(self.subnet).network_address
        return str(base + last + (self.shift if last != 1 else 0))


class Profile(BaseModel):
    """One brewery site: what it runs and how its network is cut."""

    code: str
    index: int
    title: str
    origin: str
    network: str
    # Modules of the single-site range the site has, and single hosts of them.
    modules: tuple[str, ...] = ()
    extra_hosts: tuple[str, ...] = ()
    drop_areas: tuple[str, ...] = ()
    drop_units: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    drop_hosts: tuple[str, ...] = ()
    # By today's VLAN alias, and by PLC for a control network of its own.
    moves: dict[str, Move] = Field(default_factory=dict)
    cells: dict[str, Move] = Field(default_factory=dict)
    # The host the site's clocks follow.
    time_server: str = "ot-svc"
    # (ISP, the edge's address on the access link).
    uplinks: tuple[tuple[str, str], ...]
    sensor: bool = True

    @property
    def one_firewall(self) -> bool:
        return "fw-ot" in self.drop_hosts

    def site(self) -> Any:
        return stamp.Site(code=self.code, index=self.index, title=self.title)


PROFILES: list[Profile] = [
    Profile(
        code="fr",
        index=0,
        title="Front Range: flagship brewery, taproom, head office",
        origin="The first brewery. Its network was rebuilt to the company's standard after it grew",
        network="Two firewalls with a DMZ between them, a zone for each level (DESIGN.md 10)",
        modules=("soc", "remote"),
        uplinks=(("shadecast", "203.0.113.2/29"), ("tumbleweed", "198.18.101.2/30")),
        sensor=False,
    ),
    Profile(
        code="ep",
        index=2,
        title="Eastern Plains: production brewery",
        origin="Built new by the company, to the standard and one step past it",
        network="Two firewalls and a DMZ with its own jump host; a control network for each process cell",
        extra_hosts=("dmz-jump",),
        drop_units={"FAC": ("RTU1",)},
        drop_hosts=("bas-rtu-taproom",),
        cells={
            "plc-brewhouse": Move(vlan="CTRL-BH", subnet="10.20.41.0/24", gateway="fw-ot"),
            "plc-cellar": Move(vlan="CTRL-CEL", subnet="10.20.42.0/24", gateway="fw-ot"),
            "plc-packaging": Move(vlan="CTRL-PKG", subnet="10.20.43.0/24", gateway="fw-ot"),
            "plc-utilities": Move(vlan="CTRL-UTL", subnet="10.20.44.0/24", gateway="fw-ot"),
            "plc-power": Move(vlan="CTRL-PWR", subnet="10.20.46.0/24", gateway="fw-ot"),
        },
        uplinks=(("tumbleweed", "198.18.101.10/30"), ("shadecast", "203.0.113.18/30")),
    ),
    Profile(
        code="cs",
        index=3,
        title="Colorado Springs: brewery and taproom, bought from another company",
        origin="Bought with its network as its integrator built it. The company is bringing it into the fleet",
        network="One firewall and three zones (office, plant, building); its own address plan in 192.168.0.0/16",
        drop_areas=("PWR",),
        drop_hosts=(
            "fw-ot",
            "plc-power",
            "relay-main",
            "pv-inverter",
            "bess-pcs",
            "gen-ats",
            "rtu-der",
            "sim-power",
        ),  # fmt: skip
        moves={
            "CORP": Move(vlan="OFFICE", subnet="192.168.10.0/24", gateway="fw-edge"),
            "OPS": Move(vlan="PLANT", subnet="192.168.20.0/24", shift=30, gateway="fw-edge"),
            "HMI": Move(vlan="PLANT", subnet="192.168.20.0/24", shift=20, gateway="fw-edge"),
            "CTRL": Move(vlan="PLANT", subnet="192.168.20.0/24", gateway="fw-edge"),
            "BAS": Move(vlan="BAS", subnet="192.168.30.0/24", gateway="fw-edge"),
            "FIELD-BH": Move(vlan="FIELD-BH", subnet="192.168.21.0/24"),
            "FIELD-CEL": Move(vlan="FIELD-CEL", subnet="192.168.22.0/24"),
            "FIELD-PKG": Move(vlan="FIELD-PKG", subnet="192.168.23.0/24"),
            "FIELD-UTL": Move(vlan="FIELD-UTL", subnet="192.168.24.0/24"),
        },
        uplinks=(("bristlecone", "198.18.103.2/30"),),
    ),
    Profile(
        code="fc",
        index=1,
        title="Fort Collins: brewpub",
        origin="Opened as a brewpub in Old Town Fort Collins. Nobody on site looks after its network",
        network="One firewall and two zones: the office, and one flat network for everything that controls",
        drop_areas=("PKG", "PWR"),
        drop_hosts=(
            "fw-ot",
            "ot-svc",
            "historian",
            "ews",
            "hmi-packaging",
            "bas-bbmd",
            "bas-fuxa",
            "plc-packaging",
            "rio-packaging",
            "plc-power",
            "relay-main",
            "pv-inverter",
            "bess-pcs",
            "gen-ats",
            "rtu-der",
            "sim-power",
        ),  # fmt: skip
        moves={
            "CORP": Move(vlan="OFFICE", subnet="10.10.10.0/24", gateway="fw-edge"),
            "OPS": Move(vlan="PLANT", subnet="10.20.10.0/24", shift=30, gateway="fw-edge"),
            "HMI": Move(vlan="PLANT", subnet="10.20.10.0/24", shift=20, gateway="fw-edge"),
            "CTRL": Move(vlan="PLANT", subnet="10.20.10.0/24", gateway="fw-edge"),
            "BAS": Move(vlan="PLANT", subnet="10.20.10.0/24", shift=30, gateway="fw-edge"),
        },
        time_server="scada-fuxa",
        uplinks=(("shadecast", "203.0.113.10/30"), ("marmot", "198.18.102.2/30")),
    ),
]


class Step(BaseModel):
    """One edit of a site's plant file, and what plantgen said after it."""

    name: str
    passed: bool
    said: str


class SiteResult(BaseModel):
    code: str
    title: str
    steps: list[Step] = Field(default_factory=list)
    generated: str = ""
    hosts: int = 0
    plcs: int = 0
    tags: int = 0
    vlans: list[str] = Field(default_factory=list)
    budget: stamp.Budget = Field(default_factory=stamp.Budget)
    with_sensor: stamp.Budget = Field(default_factory=stamp.Budget)


class Result(BaseModel):
    sites: list[SiteResult] = Field(default_factory=list)
    merge: dict[str, Any] = Field(default_factory=dict)
    shared: dict[str, stamp.Budget] = Field(default_factory=dict)
    total: stamp.Budget = Field(default_factory=stamp.Budget)
    placements: list[stamp.Placement] = Field(default_factory=list)
    cluster: dict[str, Any] = Field(default_factory=dict)


# ------------------------------ The site's hosts ------------------------------


def site_hosts(profile: Profile, plant: dict[str, Any]) -> list[str]:
    """The hosts of the single-site plant that this site has, in the plant file's order."""
    wanted = set(plant["variants"]["lite"]["hosts"]) - stamp.SHARED_HOSTS
    if "soc" in profile.modules:
        wanted |= set(SOC_HOSTS)
    if "remote" in profile.modules:
        wanted |= set(REMOTE_HOSTS)
    wanted |= set(profile.extra_hosts)
    wanted -= set(profile.drop_hosts)
    return [name for name in plant["hosts"] if name in wanted]


def move_of(profile: Profile, host: str, vlan: str) -> Move | None:
    if vlan == "CTRL" and host in profile.cells:
        return profile.cells[host]
    return profile.moves.get(vlan)


# ------------------------------ 1. plantgen ------------------------------
#
# Each step edits the plant file of one site. The steps run in this order, and
# plantgen runs after each one.


def step_hosts(profile: Profile, plant: dict[str, Any]) -> None:
    """The site's own hosts, areas, and federates as a variant; nothing else touched."""
    variant = copy.deepcopy(plant["variants"]["full" if profile.modules else "lite"])
    hosts = site_hosts(profile, plant)
    areas = [area for area in variant["areas"] if area not in profile.drop_areas]
    gone_federates = {a["federate"] for a in plant["areas"] if a["id"] in profile.drop_areas}
    variant.update(
        description=profile.title,
        hosts=hosts,
        areas=areas,
        federates=[name for name in variant["federates"] if name not in gone_federates],
        # The vendor's WireGuard client and the capture lists name hosts of the internet.
        wireguard=False,
        scorch=False,
    )
    if profile.drop_units:
        variant["units"] = {
            area["id"]: [u["id"] for u in area["units"] if u["id"] not in profile.drop_units[area["id"]]]
            for area in plant["areas"]
            if area["id"] in profile.drop_units
        }
    plant["variants"] = {"site": variant}


def step_references(profile: Profile, plant: dict[str, Any]) -> None:
    """Take out what names a host the site does not have."""
    variant = plant["variants"]["site"]
    hosts = set(variant["hosts"])
    for name in list(plant["hosts"]):
        if name not in hosts:
            del plant["hosts"][name]
    for host in plant["hosts"].values():
        if host.get("fuxa"):
            host["fuxa"] = [source for source in host["fuxa"] if source["host"] in hosts]
        if host.get("upstream"):
            host["upstream"] = [name for name in host["upstream"] if name in hosts]
    soh = plant["soh"]
    soh["conduits"] = [c for c in soh["conduits"] if c["src"] in hosts and c["dst"] in hosts]
    soh["skip_hosts"] = [name for name in soh.get("skip_hosts", []) if name in hosts]
    soh["host_profiles"] = {name: p for name, p in soh.get("host_profiles", {}).items() if name in hosts}
    wazuh = variant.get("app", {}).get("wazuh")
    if wazuh:
        if wazuh["manager"] in hosts:
            wazuh["agents"] = [name for name in wazuh["agents"] if name in hosts]
        else:
            del variant["app"]["wazuh"]
    federates = set(variant["federates"])
    variant["federate_sim"] = {k: v for k, v in variant.get("federate_sim", {}).items() if k in federates}
    plant["federates"] = {k: v for k, v in plant["federates"].items() if k in federates}
    plant["loads"] = [load for load in plant.get("loads", []) if load["federate"] in federates]
    plant["areas"] = [area for area in plant["areas"] if area["id"] in variant["areas"]]


def step_time(profile: Profile, plant: dict[str, Any]) -> None:
    """Every clock follows a server of the site; none follows a host of the internet."""
    variant = plant["variants"]["site"]
    hosts = set(variant["hosts"])
    ntp = variant["ntp"]
    server = profile.time_server
    ntp["default_source"] = {"hostname": server, "interface": "IF0"}
    entries = {name: entry for name, entry in ntp["hosts"].items() if name in hosts}
    for entry in entries.values():
        if entry.get("source", {}).get("hostname") not in hosts | {None}:
            del entry["source"]
    entries[server] = {"server": "chronyd"}
    ntp["hosts"] = entries


def step_snapshot(_profile: Profile, plant: dict[str, Any]) -> None:
    """The ground-truth snapshot asks only for keys of federates the site runs."""
    federates = set(plant["variants"]["site"]["federates"])
    snapshot = plant["snapshot"]
    snapshot["keys"] = [key for key in snapshot["keys"] if key.split("/")[0] in federates | {"*"}]


def step_variant_names(_profile: Profile, plant: dict[str, Any]) -> None:
    """Five federate keys are limited to the variants `full` and `lite` by name. Add the site's."""
    federates = set(plant["variants"]["site"]["federates"])
    for federate in plant["federates"].values():
        for key in federate.get("outputs", []) + federate.get("inputs", []):
            other = key["key"].split("/")[0] if "/" in key["key"] else None
            if key.get("variants") and (other is None or other in federates):
                key["variants"] = [*key["variants"], "site"]


def step_uplinks(profile: Profile, plant: dict[str, Any]) -> None:
    """The edge has an access link to each of its ISPs, and the internet's VLANs are gone."""
    networks = plant["networks"]
    for alias in INTERNET_VLANS:
        networks.pop(alias, None)
    edge = plant["hosts"]["fw-edge"]
    inside = [iface for iface in edge["interfaces"].values() if iface["vlan"] not in INTERNET_VLANS]
    links = []
    for number, (_isp, address) in enumerate(profile.uplinks):
        alias = f"WAN-{'AB'[number]}"
        networks[alias] = {"subnet": str(ipaddress.ip_interface(address).network)}
        links.append({"vlan": alias, "ip": address})
    edge["interfaces"] = {f"IF{n}": iface for n, iface in enumerate(links + inside)}


def step_zones(profile: Profile, plant: dict[str, Any]) -> None:
    """The site's own zones: VLANs merged, split, renamed, or renumbered."""
    networks = plant["networks"]
    hosts = plant["hosts"]
    used: set[str] = set()
    for name, host in hosts.items():
        for iface in host["interfaces"].values():
            move = move_of(profile, name, iface["vlan"])
            if move:
                mask = iface["ip"].split("/")[1]
                iface["vlan"], iface["ip"] = move.vlan, f"{move.address(iface['ip'])}/{mask}"
                networks[move.vlan] = {"subnet": move.subnet} | ({"gateway": move.gateway} if move.gateway else {})
            used.add(iface["vlan"])
    if profile.one_firewall:
        # The one firewall takes a leg in every routed zone, and has no DMZ behind it.
        edge = {key: iface for key, iface in hosts["fw-edge"]["interfaces"].items() if iface["vlan"] != "DMZ"}
        networks.pop("DMZ", None)
        legs = {iface["vlan"] for iface in edge.values()}
        for alias, network in networks.items():
            if network.get("gateway") == "fw-edge" and alias in used and alias not in legs:
                first = ipaddress.ip_network(network["subnet"]).network_address + 1
                edge[f"IF{len(edge)}"] = {"vlan": alias, "ip": f"{first}/24"}
                legs.add(alias)
        hosts["fw-edge"]["interfaces"] = {f"IF{n}": iface for n, iface in enumerate(edge.values())}
        used = {iface["vlan"] for host in hosts.values() for iface in host["interfaces"].values()}
    for name, move in profile.cells.items():
        if name in hosts:
            ot = hosts["fw-ot"]["interfaces"]
            first = ipaddress.ip_network(move.subnet).network_address + 1
            ot[f"IF{len(ot)}"] = {"vlan": move.vlan, "ip": f"{first}/24"}
    if profile.cells:
        ot = hosts["fw-ot"]["interfaces"]
        hosts["fw-ot"]["interfaces"] = {
            f"IF{n}": iface for n, iface in enumerate(i for i in ot.values() if i["vlan"] != "CTRL")
        }
        used = {iface["vlan"] for host in hosts.values() for iface in host["interfaces"].values()}
    if "soc" not in profile.modules and not (profile.sensor and not profile.one_firewall):
        networks.pop("SOC", None)
    for router in ("fw-edge", "fw-ot"):
        if router in hosts:
            legs = [iface for iface in hosts[router]["interfaces"].values() if iface["vlan"] in networks]
            hosts[router]["interfaces"] = {f"IF{n}": iface for n, iface in enumerate(legs)}
    used = {iface["vlan"] for host in hosts.values() for iface in host["interfaces"].values()}
    for alias in list(networks):
        if alias not in used and alias != "MIRROR":
            del networks[alias]


STEPS: list[tuple[str, Callable[[Profile, dict[str, Any]], None]]] = [
    ("The site's hosts, areas, and federates as a variant; nothing else changed", step_hosts),
    ("Every clock follows a server of the site", step_time),
    ("The ground-truth snapshot asks only for federates the site runs", step_snapshot),
    ("The site's variant added to the federate keys that name `full` and `lite`", step_variant_names),
    ("Take out what names a host the site does not have", step_references),
    ("An access link to each ISP in place of the INET VLAN", step_uplinks),
    ("The site's own zones: VLANs merged, split, renamed, or renumbered", step_zones),
]


def probe_site(profile: Profile, plant: dict[str, Any], work: Path, patched: Path) -> tuple[SiteResult, dict[str, Any]]:
    """Run the steps on a copy of the plant file. Returns the record and the stamped plant file."""
    record = SiteResult(code=profile.code, title=profile.title)
    doc = copy.deepcopy(plant)
    site = profile.site()
    stamped: dict[str, Any] = {}
    before: dict[str, str] = {}
    for number, (name, step) in enumerate(STEPS, 1):
        step(profile, doc)
        stamped = stamp.Stamper(site, set(plant["hosts"]), set(doc["networks"]) | set(plant["networks"])).walk(doc)
        stamped["variants"] = {"site": stamped["variants"].pop(next(iter(stamped["variants"])))}
        out = work / f"{profile.code}-{number}"
        passed, said = stamp.run_plantgen(stamped, "site", out, package=patched)
        if passed:
            # What the step changed in the generated files.
            files = {
                str(path.relative_to(out / "out")): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((out / "out").rglob("*"))
                if path.is_file()
            }
            changed = sorted(key for key in files.keys() | before.keys() if files.get(key) != before.get(key))
            if before:
                said += f"; {len(changed)} differ from the step before" + (
                    f" ({', '.join(changed[:4])}{', ...' if len(changed) > 4 else ''})" if changed else ""
                )
            before = files
        if passed and number == len(STEPS):
            said += "; " + stamp.leaks(site, plant, out / "out") if profile.index else ""
            record.generated = str(out / "out")
        record.steps.append(Step(name=name, passed=passed, said=said))
    variant = stamped["variants"]["site"]
    record.hosts = len(variant["hosts"])
    record.plcs = sum(1 for name in variant["hosts"] if stamped["hosts"][name]["role"] == "plc")
    if record.generated:
        tags = json.loads((Path(record.generated) / "tags.json").read_text())
        record.tags = len(tags["tags"])
    return record, stamped


# ------------------------------ 2. Topology ------------------------------


def site_nodes(profile: Profile, plant: dict[str, Any], sources: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The site's phenix nodes: today's nodes, with the interfaces of the site's plant file."""
    nodes = []
    for name in plant["variants"]["site"]["hosts"]:
        plain = name.split("-", 1)[1]
        node = copy.deepcopy(sources[plain])
        node["general"]["hostname"] = name
        old = node.get("network", {}).get("interfaces", [])
        new = []
        for number, (key, iface) in enumerate(plant["hosts"][name]["interfaces"].items()):
            address, mask = iface["ip"].split("/")
            router = node["type"] in ("Router", "Firewall")
            entry: dict[str, Any] = {
                "name": f"eth{number}" if router else key,
                "type": "ethernet",
                "proto": "static",
                "vlan": iface["vlan"],
                "address": address,
                "mask": int(mask),
            }
            gateway = plant["networks"].get(iface["vlan"], {}).get("gateway")
            if gateway and gateway != name and not router:
                entry["gateway"] = str(ipaddress.ip_interface(iface["ip"]).network.network_address + 1)
            new.append(entry)
        if old or new:
            node.setdefault("network", {})["interfaces"] = new
        # Routes and name servers of today's node name addresses of the one-site plan.
        node.get("network", {}).pop("routes", None)
        for dependency in node.get("delay", {}).get("c2", []):
            dependency["hostname"] = f"{profile.code}-{dependency['hostname']}"
        node["delay"] = {
            "c2": [d for d in node.get("delay", {}).get("c2", []) if d["hostname"] in plant["hosts"]]
        } if node.get("delay", {}).get("c2") else node.get("delay", {})  # fmt: skip
        if not node.get("delay", {}).get("c2"):
            node.pop("delay", None)
        nodes.append(node)
    if profile.sensor:
        zone = next(
            alias for alias in (f"{profile.code.upper()}-SOC", f"{profile.code.upper()}-PLANT")
            if alias in plant["networks"]
        )  # fmt: skip
        subnet = ipaddress.ip_network(plant["networks"][zone]["subnet"])
        nodes.append(
            {
                "type": "VirtualMachine",
                "general": {"hostname": f"{profile.code}-sensor", "description": "Network sensor (a stand-in)"},
                "hardware": {
                    "os_type": "linux",
                    "vcpus": 4,
                    "memory": 16384,
                    "drives": [{"image": "cm-sensor.qc2"}],
                },  # fmt: skip
                "network": {
                    "interfaces": [
                        {
                            "name": "IF0",
                            "type": "ethernet",
                            "proto": "static",
                            "vlan": zone,
                            "address": str(subnet.network_address + 60),
                            "mask": 24,
                            "gateway": str(subnet.network_address + 1),
                        }
                    ]
                },  # fmt: skip
            }
        )
    return nodes


def merge_sites(result: Result, plants: dict[str, dict[str, Any]], out: Path, schema_dir: Path | None) -> list[Any]:
    sources = {
        node["general"]["hostname"]: node
        for name in ("core", "soc", "remote-access")
        for node in stamp.load_topology(name)["spec"]["nodes"]
    }
    out.mkdir(parents=True, exist_ok=True)
    merged: list[dict[str, Any]] = []
    files: list[Path] = []
    for profile, record in zip(PROFILES, result.sites, strict=True):
        nodes = site_nodes(profile, plants[profile.code], sources)
        topology = {
            "apiVersion": "phenix.sandia.gov/v1",
            "kind": "Topology",
            "metadata": {"name": f"cherrymountain-site-{profile.code}"},
            "spec": {"nodes": nodes},
        }
        path = out / f"topology-site-{profile.code}.yml"
        path.write_text(yaml.safe_dump(topology, sort_keys=False))
        files.append(path)
        merged += nodes
        own = [n for n in nodes if not n["general"]["hostname"].endswith("-sensor")]
        record.budget = stamp.node_budget(own)
        record.with_sensor = stamp.node_budget(nodes)
        record.vlans = sorted({i["vlan"] for n in nodes for i in n.get("network", {}).get("interfaces", [])})

    names = Counter(n["general"]["hostname"] for n in merged)
    addresses: Counter[str] = Counter(
        i["address"] for n in merged for i in n.get("network", {}).get("interfaces", []) if i.get("address")
    )
    aliases = {i["vlan"] for n in merged for i in n.get("network", {}).get("interfaces", [])}
    result.merge = {
        "site topology files": len(files),
        "nodes in the four sites": len(merged),
        "duplicate hostnames": sorted(n for n, count in names.items() if count > 1),
        "duplicate addresses": sorted(a for a, count in addresses.items() if count > 1),
        "VLAN aliases in the four sites": len(aliases),
    }
    for profile in PROFILES:
        code = profile.code
        subnets = sorted(
            {
                str(ipaddress.ip_interface(f"{i['address']}/{i['mask']}").network)
                for n in merged
                if n["general"]["hostname"].startswith(f"{code}-")
                for i in n.get("network", {}).get("interfaces", [])
            },
            key=lambda s: ipaddress.ip_network(s),
        )
        result.merge[f"subnets of {code}"] = ", ".join(subnets)
    checker = shutil.which("check-jsonschema")
    if schema_dir and checker and (schema_dir / "topology.schema.json").exists():
        proc = subprocess.run(
            [
                checker,
                "--schemafile",
                str(schema_dir / "topology.schema.json"),
                "--base-uri",
                schema_dir.as_uri() + "/",
                *map(str, files),
            ],  # fmt: skip
            capture_output=True,
            text=True,
            check=False,
        )
        result.merge["phenix Topology schema"] = (
            f"ok ({len(files)} files)" if proc.returncode == 0 else (proc.stdout + proc.stderr).strip()[-600:]
        )
    else:
        result.merge["phenix Topology schema"] = "not checked (no check-jsonschema or no schema)"
    return merged


# --------------------------- 3. Size and cluster ---------------------------

LAYOUTS: dict[str, dict[str, tuple[str, ...]]] = {
    "L1: the head office apart": {
        "head node": ("ep",),
        "node 2": ("once", "fr"),
        "node 3": ("cs", "fc"),
    },
    "L2: the two small sites on the head node": {
        "head node": ("cs", "fc"),
        "node 2": ("once", "fr"),
        "node 3": ("ep",),
    },
    "L3: the head office alone on a node": {
        "head node": ("once", "cs"),
        "node 2": ("fr",),
        "node 3": ("ep", "fc"),
    },
}
CHOSEN: str = "L1: the head office apart"


def size(result: Result, merged: list[dict[str, Any]], out: Path, schema_dir: Path | None) -> None:
    # What the range has once, outside any site: the mock internet as VyOS
    # routers and the head office's additions (the stamp spike's figures). The
    # SOC and the vendor's gateway are in `fr`'s own count here.
    internet = stamp.Budget(vms=12, vcpus=12, memory_mb=6 * 2048 + 6 * 1024)
    head_office = stamp.Budget(vms=3, vcpus=6, memory_mb=3 * 4096)
    vendor = stamp.node_budget(
        [n for n in stamp.load_topology("remote-access")["spec"]["nodes"] if n["general"]["hostname"] == "vendor-ws"]
    )
    result.shared = {
        "mock internet, VyOS routers (6 routers, 6 small hosts)": internet,
        "the vendor's workstation, on the internet": vendor,
        "head office additions (fleet historian, corporate server, second office desktop)": head_office,
    }
    once = internet.add(vendor).add(head_office)
    cost = {record.code: record.with_sensor for record in result.sites} | {"once": once}
    result.total = stamp.Budget()
    for part in cost.values():
        result.total = result.total.add(part)
    label = {"once": "internet, vendor, head office additions"}
    for title, layout in LAYOUTS.items():
        placement = stamp.Placement(title=title)
        for node, parts in layout.items():
            total = stamp.Budget()
            for part in parts:
                total = total.add(cost[part])
            placement.nodes[node] = "; ".join(label.get(part, f"site {part}") for part in parts)
            placement.cost[node] = total
        result.placements.append(placement)

    # A workflow for the chosen layout: a VLAN ID for every alias, a node for every site VM.
    single = yaml.safe_load(stamp.WORKFLOW.read_text())["spec"]["vlans"]
    vlans: dict[str, int] = {"MIRROR": single["MIRROR"]}
    links = 0
    for profile, record in zip(PROFILES, result.sites, strict=True):
        base = 100 * (profile.index + 1)
        taken: set[int] = set()
        for alias in record.vlans:
            if alias == "MIRROR":
                continue
            plain = alias.split("-", 1)[1]
            if plain.startswith("WAN-"):
                # An access link belongs to the internet's block, 500 to 539.
                vlans[alias] = 500 + links
                links += 1
                continue
            number = base + single.get(plain, 0) % 100
            # An alias the one-site plan does not have takes the next free ID from 20 up.
            while number == base or number in taken:
                number = base + 20 + sum(1 for n in taken if n >= base + 20)
            taken.add(number)
            vlans[alias] = number
    node_of = {part: node.replace(" ", "-") for node, parts in LAYOUTS[CHOSEN].items() for part in parts}
    schedules = {n["general"]["hostname"]: node_of[n["general"]["hostname"].split("-", 1)[0]] for n in merged}
    workflow = {
        "apiVersion": "phenix.sandia.gov/v0",
        "kind": "Workflow",
        "metadata": {},
        "spec": {
            "auto": {"create": "cherrymountain-fleet", "update": True, "restart": True},
            "topology": "cherrymountain-fleet",
            "scenario": "cherrymountain-fleet",
            "vlans": dict(sorted(vlans.items(), key=lambda item: item[1])),
            "vlanRange": {"min": 100, "max": 599},
            "defaultBridge": "cherrymtn-fleet",
            "deployMode": "all",
            "schedules": schedules,
        },
    }
    path = out / "phenix-fleet.yml"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False))
    ids = Counter(vlans.values())
    used = {i["vlan"] for n in merged for i in n.get("network", {}).get("interfaces", [])}
    result.cluster = {
        "layout": CHOSEN,
        "VMs with a node in `schedules`": len(schedules),
        "VMs for each node": json.dumps(dict(sorted(Counter(schedules.values()).items()))),
        "VLAN aliases with an ID": len(vlans),
        "VLAN IDs given twice": sorted(number for number, count in ids.items() if count > 1),
        "aliases of the site topologies without an ID": sorted(used - set(vlans)),
    }
    checker = shutil.which("check-jsonschema")
    if schema_dir and checker and (schema_dir / "workflow.schema.json").exists():
        proc = subprocess.run(
            [
                checker,
                "--schemafile",
                str(schema_dir / "workflow.schema.json"),
                "--base-uri",
                schema_dir.as_uri() + "/",
                str(path),
            ],  # fmt: skip
            capture_output=True,
            text=True,
            check=False,
        )
        result.cluster["phenix Workflow schema"] = (
            "ok" if proc.returncode == 0 else (proc.stdout + proc.stderr).strip()[-600:]
        )
    else:
        result.cluster["phenix Workflow schema"] = "not checked (no check-jsonschema or no schema)"


# --------------------------------- Report ---------------------------------


def write(result: Result) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for record in result.sites:
        record.generated = ""
    (EVIDENCE / "result.json").write_text(result.model_dump_json(indent=2) + "\n")
    lines = ["# site-profiles", "", "## What plantgen said after each step", ""]
    for record in result.sites:
        lines += [f"### `{record.code}`: {record.title}", "", "| Step | Result | plantgen said |", "|---|---|---|"]
        for number, step in enumerate(record.steps, 1):
            said = step.said.replace("|", "\\|")
            lines.append(f"| {number}. {step.name} | {'pass' if step.passed else '**fail**'} | {said} |")
        lines.append("")
    lines += [
        "## The four sites",
        "",
        "| Site | Hosts in its plant file | PLCs | Tags | VLANs | VMs | vCPUs | Memory (GB) | With its sensor |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in result.sites:
        sensor = "none" if record.budget == record.with_sensor else record.with_sensor.row().replace(" | ", ", ")
        lines.append(
            f"| `{record.code}` | {record.hosts} | {record.plcs} | {record.tags} | {len(record.vlans)} "
            f"| {record.budget.row()} | {sensor} |"
        )
    lines += ["", "## VLANs of each site", "", "| Site | VLANs |", "|---|---|"]
    for record in result.sites:
        lines.append(f"| `{record.code}` | {', '.join(record.vlans)} |")
    lines += ["", "## The four sites in one experiment", "", "| Item | Value |", "|---|---|"]
    for key, value in result.merge.items():
        lines.append(f"| {key} | {json.dumps(value) if isinstance(value, list) else value} |")
    lines += ["", "## The whole range", "", "| Part | VMs | vCPUs | Memory (GB) |", "|---|---:|---:|---:|"]
    for key, budget in result.shared.items():
        lines.append(f"| {key} | {budget.row()} |")
    for record in result.sites:
        lines.append(f"| site `{record.code}`, with its sensor if it has one | {record.with_sensor.row()} |")
    lines.append(f"| **all of it** | {result.total.row()} |")
    lines += [
        "",
        f"All of it on one node would be {result.total.vcpus / stamp.NODE_THREADS:.2f} vCPUs per thread and "
        f"{result.total.memory_mb / 1024 / stamp.NODE_MEMORY_GB:.0%} of its memory.",
    ]
    lines += [
        "",
        f"## On three nodes of {stamp.NODE_THREADS // 2} cores, {stamp.NODE_THREADS} threads, and "
        f"{stamp.NODE_MEMORY_GB} GB",
        "",
        "| Layout | Node | Runs | VMs | vCPUs | Memory (GB) | vCPUs per thread | Memory used |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for placement in result.placements:
        for node, runs in placement.nodes.items():
            cost = placement.cost[node]
            lines.append(
                f"| {placement.title} | {node} | {runs} | {cost.row()} | {cost.vcpus / stamp.NODE_THREADS:.2f} "
                f"| {cost.memory_mb / 1024 / stamp.NODE_MEMORY_GB:.0%} |"
            )
    lines += ["", "## A workflow that pins each site to a node", "", "| Item | Value |", "|---|---|"]
    for key, value in result.cluster.items():
        lines.append(f"| {key} | {json.dumps(value) if isinstance(value, list) else value} |")
    (EVIDENCE / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--schema", type=Path, help="the schemas/ directory of a sceptre-phenix checkout")
    parser.add_argument("--out", type=Path, help="keep the site topology files and the workflow here")
    args = parser.parse_args()
    plant: dict[str, Any] = yaml.safe_load(stamp.PLANT.read_text())
    result = Result()
    with tempfile.TemporaryDirectory(prefix="cm-profiles-") as tmp:
        work = Path(tmp)
        patched = stamp.patched_plantgen(work)
        plants: dict[str, dict[str, Any]] = {}
        for profile in PROFILES:
            record, plants[profile.code] = probe_site(profile, plant, work / "plantgen", patched)
            result.sites.append(record)
        out = args.out or work / "topologies"
        merged = merge_sites(result, plants, out, args.schema)
        size(result, merged, out, args.schema)
        write(result)
    return 0 if all(record.steps[-1].passed for record in result.sites) else 1


if __name__ == "__main__":
    raise SystemExit(main())
