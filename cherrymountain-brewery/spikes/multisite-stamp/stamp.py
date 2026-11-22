#!/usr/bin/env python3
"""Stamp the single-site plant out as several sites and see what breaks.

Needs Python 3.12+ with pyyaml and pydantic; `--schema` also needs
check-jsonschema on PATH and a sceptre-phenix checkout:
  python stamp.py                       # plantgen probe, topology stamp, budgets
  python stamp.py --schema ~/sceptre-phenix/schemas

Three questions, one per step (README.md has the result):

1. plantgen: does the generator accept a plant.yaml whose hostnames carry a
   site prefix and whose subnets and VLAN aliases belong to another site?
2. phenix: do four stamped copies of the site's topology merge into one
   experiment under phenix's rules (unique hostnames, unique addresses, one
   VLAN ID per alias) and still validate against its Topology schema?
3. Budget: what do the candidate site mixes cost in VMs, vCPUs, and memory?
4. Cluster: how does the largest mix sit on one to five minimega nodes of 48
   cores and 512 GB, and does a workflow that pins each site to a node and
   turns on the GRE mesh validate against phenix's Workflow schema?

Nothing here is a generator for the range. It rewrites strings, which is how
the probe finds every place that still assumes one plant.
"""

from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

HERE: Path = Path(__file__).resolve().parent
RANGE: Path = HERE.parents[1]
PLANT: Path = RANGE / "plant" / "plant.yaml"
CONFIGS: Path = RANGE / "phenix-configs"
PLANTGEN: Path = RANGE / "tools" / "plantgen"
EVIDENCE: Path = Path(os.environ.get("CM_EVIDENCE_DIR", "evidence")) / "multisite-stamp"

# Nodes of the single-site core that belong to the shared internet in a
# multi-site range, not to a site.
SHARED_HOSTS: frozenset[str] = frozenset({"isp-rtr", "inet-traffic", "util-rtr", "utility-derms", "vendor-ws"})
SHARED_VLANS: frozenset[str] = frozenset({"INET", "VENDOR", "UTIL", "MIRROR"})
# The second octet of each private block of the single-site plan (DESIGN.md
# 10.2). Site n (0-based) adds n to it: CTRL is 10.20.10.0/24 at the first
# site and 10.21.10.0/24 at the second.
PRIVATE_BLOCKS: tuple[int, ...] = (10, 20, 30, 40)
MGMT_SECOND_OCTET: int = 16


# A cluster node: 48 cores, two threads each, 512 GB.
NODE_THREADS: int = 96
NODE_MEMORY_GB: int = 512
# The single-site workflow, whose VLAN IDs a site keeps, plus 100 for each site after the first.
WORKFLOW: Path = RANGE / "phenix.yml"
VLAN_STEP: int = 100

# Keys whose value names a kind of host, and maps whose keys do.
KIND_KEYS: frozenset[str] = frozenset({"role", "type", "model", "profile", "federate", "desc", "description"})
KIND_MAPS: frozenset[str] = frozenset({"profiles", "roles", "device_types", "federates"})


class Site(BaseModel):
    """One brewery site of the stamped range."""

    code: str
    index: int
    title: str

    def host(self, name: str) -> str:
        return name if name in SHARED_HOSTS else f"{self.code}-{name}"

    def vlan(self, name: str) -> str:
        return name if name in SHARED_VLANS else f"{self.code.upper()}-{name}"

    def address(self, text: str) -> str:
        """Move every private address in `text` to this site's blocks."""

        def move(match: re.Match[str]) -> str:
            first, second = int(match.group(1)), int(match.group(2))
            if first == 10 and second in PRIVATE_BLOCKS:
                second += self.index
            elif first == 172 and second == MGMT_SECOND_OCTET:
                second += self.index
            return f"{first}.{second}."

        return re.sub(r"\b(10|172)\.(\d{1,3})\.(?=\d{1,3}\.\d{1,3}\b)", move, text)


SITES: list[Site] = [
    Site(code="fr", index=0, title="Front Range brewery and head office"),
    Site(code="hc", index=1, title="High Country brewpub"),
    Site(code="ws", index=2, title="Western Slope brewery"),
    Site(code="gc", index=3, title="Gulf Coast brewery"),
]


class Stamper:
    """Rewrites hostnames, VLAN aliases, and addresses in a YAML document."""

    def __init__(self, site: Site, hosts: set[str], vlans: set[str]) -> None:
        self.site: Site = site
        self.hosts: set[str] = hosts
        self.vlans: set[str] = vlans
        # Longest first, so `plc-cellar` is never rewritten as part of a longer name.
        names = sorted(hosts - SHARED_HOSTS, key=len, reverse=True)
        self.host_token: re.Pattern[str] = re.compile(
            r"(?<![A-Za-z0-9_.-])(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9_-])"
        )

    def text(self, value: str) -> str:
        if value in self.vlans:
            return self.site.vlan(value)
        value = self.host_token.sub(lambda m: self.site.host(m.group(1)), value)
        return self.site.address(value)

    def walk(self, node: Any, parent: str = "") -> Any:
        if isinstance(node, dict):
            # Some hostnames are also the name of a role, an app type, or a
            # SoH profile (ews, historian, sim-ctl). Those values are kinds,
            # not hosts, and keep their names.
            return {
                (key if parent in KIND_MAPS else self.walk(key)): (
                    value if key in KIND_KEYS else self.walk(value, str(key))
                )
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [self.walk(item, parent) for item in node]
        if isinstance(node, str):
            return self.text(node)
        return node


class Probe(BaseModel):
    """One plantgen run on a stamped plant.yaml."""

    name: str
    passed: bool
    detail: str = ""


class Budget(BaseModel):
    """The cost of a set of nodes."""

    vms: int = 0
    vcpus: int = 0
    memory_mb: int = 0

    def add(self, other: Budget, times: int = 1) -> Budget:
        return Budget(
            vms=self.vms + other.vms * times,
            vcpus=self.vcpus + other.vcpus * times,
            memory_mb=self.memory_mb + other.memory_mb * times,
        )

    def row(self) -> str:
        return f"{self.vms} | {self.vcpus} | {self.memory_mb / 1024:.0f}"


class Placement(BaseModel):
    """One way to spread a mix over cluster nodes: what each node runs, and what that costs it."""

    title: str
    nodes: dict[str, str] = Field(default_factory=dict)
    cost: dict[str, Budget] = Field(default_factory=dict)


class Result(BaseModel):
    probes: list[Probe] = Field(default_factory=list)
    merge: dict[str, Any] = Field(default_factory=dict)
    budgets: dict[str, Budget] = Field(default_factory=dict)
    plans: dict[str, Budget] = Field(default_factory=dict)
    placements: list[Placement] = Field(default_factory=list)
    cluster: dict[str, Any] = Field(default_factory=dict)


# ------------------------------ 1. plantgen ------------------------------


def stamp_plant(site: Site, hostnames: bool, plant: dict[str, Any]) -> dict[str, Any]:
    hosts, vlans = set(plant["hosts"]), set(plant["networks"])
    if not hostnames:
        # Renumber and rename the VLANs only: every host keeps its name.
        stamper = Stamper(site, set(), vlans)
        stamper.host_token = re.compile(r"(?!x)x")
        return stamper.walk(plant)
    return Stamper(site, hosts, vlans).walk(plant)


def run_plantgen(plant: dict[str, Any], variant: str, workdir: Path, package: Path = PLANTGEN) -> tuple[bool, str]:
    workdir.mkdir(parents=True, exist_ok=True)
    plant_path = workdir / "plant.yaml"
    plant_path.write_text(yaml.safe_dump(plant, sort_keys=False))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "plantgen",
            "--plant",
            str(plant_path),
            "--variant",
            variant,
            "--out",
            str(workdir / "out"),
        ],
        cwd=package,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        files = sum(1 for path in (workdir / "out").rglob("*") if path.is_file())
        return True, f"{files} files generated"
    lines = [line for line in (proc.stderr or proc.stdout).strip().splitlines() if line.strip()]
    frames = [line.strip() for line in lines if line.lstrip().startswith("File ") and "plantgen/" in line]
    where = re.sub(r'File ".*?/plantgen/', "", frames[-1]).replace('"', "") if frames else ""
    tail = " / ".join(lines[-3:]) if not frames else lines[-1]
    return False, f"{where}: {tail}" if where else tail


SIM_CTL_LITERAL: str = "plant.hosts['sim-ctl'].ip('mgmt')"
SIM_CTL_FROM_BROKER: str = "broker_host.ip(broker.interface)"


def patched_plantgen(work: Path) -> Path:
    """A copy of plantgen whose ground-truth endpoint follows the variant's broker host."""
    package = work / "plantgen-patched"
    shutil.copytree(
        PLANTGEN, package, ignore=shutil.ignore_patterns("__pycache__", "tests", ".pytest_cache", "*.egg-info")
    )
    render = package / "plantgen" / "render.py"
    text = render.read_text()
    if SIM_CTL_LITERAL not in text:
        raise RuntimeError("plantgen's sim-ctl lookup moved; update SIM_CTL_LITERAL")
    render.write_text(text.replace(SIM_CTL_LITERAL, SIM_CTL_FROM_BROKER))
    return package


PROSE: re.Pattern[str] = re.compile(r'"(?:desc|label|description|text|message)"\s*:|\(\*|^\s*#')


def leaks(site: Site, plant: dict[str, Any], out: Path) -> str:
    """Names and addresses of the first site that a stamped site's output still carries."""
    site_hosts = sorted(set(plant["hosts"]) - SHARED_HOSTS, key=len, reverse=True)
    host_token = re.compile(r"(?<![A-Za-z0-9_./-])(" + "|".join(map(re.escape, site_hosts)) + r")(?![A-Za-z0-9_-])")
    address = re.compile(r"\b(?:10\.(?:10|20|30|40)|172\.16)\.\d{1,3}\.\d{1,3}\b")
    kind = re.compile(r'"?(?:role|type|profile|model)"?\s*:\s*"?[a-z0-9-]+"?')
    found: Counter[str] = Counter()
    files, prose = 0, 0
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        files += 1
        for line in path.read_text(errors="replace").splitlines():
            # A hostname that is also a kind (see KIND_KEYS) is expected as a value of those
            # keys, and as the key of a device type's own map (`pv-inverter:`).
            line = kind.sub("", line)
            if re.fullmatch(r"\s*[a-z0-9-]+:\s*", line):
                continue
            hits = [m.group(1) for m in host_token.finditer(line)] + address.findall(line)
            if hits and PROSE.search(line):
                prose += len(hits)
                continue
            for hit in hits:
                found[f"{hit} in {path.relative_to(out)}"] += 1
    note = f"; {prose} mentions in descriptions and comments only" if prose else ""
    if not found:
        return f"no first-site hostname or address in the configuration of {files} files{note}"
    worst = ", ".join(f"{what} ({count})" for what, count in found.most_common(6))
    return f"{sum(found.values())} leftovers of the first site: {worst}{note}"


def probe_plantgen(result: Result, work: Path) -> None:
    plant: dict[str, Any] = yaml.safe_load(PLANT.read_text())
    site = SITES[1]
    patched = patched_plantgen(work)
    for variant in ("ci", "lite", "full"):
        passed, detail = run_plantgen(copy.deepcopy(plant), variant, work / f"asis-{variant}")
        result.probes.append(Probe(name=f"{variant}: plant.yaml as it is (control)", passed=passed, detail=detail))
        passed, detail = run_plantgen(stamp_plant(site, False, plant), variant, work / f"renumber-{variant}")
        result.probes.append(
            Probe(
                name=f"{variant}: site {site.code} subnets and VLAN aliases, same hostnames",
                passed=passed,
                detail=detail,
            )
        )
        stamped = stamp_plant(site, True, plant)
        passed, detail = run_plantgen(stamped, variant, work / f"prefix-{variant}")
        if passed:
            detail += "; " + leaks(site, plant, work / f"prefix-{variant}" / "out")
        result.probes.append(
            Probe(
                name=f"{variant}: site {site.code} subnets, VLAN aliases, and hostnames", passed=passed, detail=detail
            )
        )
        if passed:
            continue
        passed, detail = run_plantgen(stamped, variant, work / f"patched-{variant}", package=patched)
        if passed:
            detail += "; " + leaks(site, plant, work / f"patched-{variant}" / "out")
        result.probes.append(
            Probe(
                name=f"{variant}: the same, with plantgen's one `sim-ctl` lookup taken from the broker host",
                passed=passed,
                detail=detail,
            )
        )


# ------------------------------ 2. topology ------------------------------


def load_topology(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIGS / f"topology-{name}.yml").read_text())


def node_budget(nodes: list[dict[str, Any]]) -> Budget:
    return Budget(
        vms=len(nodes),
        vcpus=sum(int(n["hardware"].get("vcpus", 1)) for n in nodes),
        memory_mb=sum(int(n["hardware"].get("memory", 0)) for n in nodes),
    )


def stamp_topologies(result: Result, out: Path, schema_dir: Path | None) -> list[dict[str, Any]]:
    core, soc, remote = load_topology("core"), load_topology("soc"), load_topology("remote-access")
    hosts = {n["general"]["hostname"] for topo in (core, soc, remote) for n in topo["spec"]["nodes"]}
    vlans = {
        iface["vlan"]
        for topo in (core, soc, remote)
        for n in topo["spec"]["nodes"]
        for iface in n.get("network", {}).get("interfaces", [])
        if "vlan" in iface
    }
    site_nodes = [n for n in core["spec"]["nodes"] if n["general"]["hostname"] not in SHARED_HOSTS]
    shared_nodes = [n for n in core["spec"]["nodes"] if n["general"]["hostname"] in SHARED_HOSTS]

    out.mkdir(parents=True, exist_ok=True)
    merged: list[dict[str, Any]] = []
    files: list[Path] = []
    for site in SITES:
        stamper = Stamper(site, hosts, vlans)
        topo = {
            "apiVersion": core["apiVersion"],
            "kind": "Topology",
            "metadata": {"name": f"cherrymountain-site-{site.code}"},
            "spec": {"nodes": stamper.walk(copy.deepcopy(site_nodes))},
        }
        path = out / f"topology-site-{site.code}.yml"
        path.write_text(yaml.safe_dump(topo, sort_keys=False))
        files.append(path)
        merged += topo["spec"]["nodes"]

    # phenix refuses a duplicate hostname when it merges included topologies,
    # SoH maps one address to one host, and minimega maps one VLAN ID to one alias.
    names = Counter(n["general"]["hostname"] for n in merged)
    addresses: Counter[str] = Counter()
    aliases: set[str] = set()
    longest = max(names, key=len)
    for node in merged:
        for iface in node.get("network", {}).get("interfaces", []):
            aliases.add(iface.get("vlan", ""))
            if iface.get("address"):
                addresses[iface["address"]] += 1
    duplicates = sorted(a for a, count in addresses.items() if count > 1)
    result.merge = {
        "site topology files": len(files),
        "nodes in the four sites": len(merged),
        "duplicate hostnames": sorted(n for n, count in names.items() if count > 1),
        "duplicate addresses": duplicates,
        "VLAN aliases in the four sites": len(aliases - {""}),
        "longest hostname": f"{longest} ({len(longest)} characters; phenix allows 63)",
    }
    for site in SITES:
        subnets = sorted(
            {
                str(ipaddress.ip_interface(f"{i['address']}/{i['mask']}").network)
                for n in merged
                if n["general"]["hostname"].startswith(f"{site.code}-")
                for i in n.get("network", {}).get("interfaces", [])
                if i.get("address")
            },
            key=lambda s: ipaddress.ip_network(s),
        )
        result.merge[f"subnets of {site.code}"] = ", ".join(subnets)

    if schema_dir:
        schema = schema_dir / "topology.schema.json"
        checker = shutil.which("check-jsonschema")
        if checker and schema.exists():
            proc = subprocess.run(
                [checker, "--schemafile", str(schema), "--base-uri", schema_dir.as_uri() + "/", *map(str, files)],
                capture_output=True,
                text=True,
                check=False,
            )
            result.merge["phenix Topology schema"] = (
                f"ok ({len(files)} files)" if proc.returncode == 0 else (proc.stdout + proc.stderr).strip()[-600:]
            )
        else:
            result.merge["phenix Topology schema"] = "not checked (no check-jsonschema or no schema)"

    by_name = {n["general"]["hostname"]: n for n in core["spec"]["nodes"]}

    def pick(*names_: str) -> list[dict[str, Any]]:
        return [by_name[name] for name in names_]

    result.budgets = {
        "site, full plant (core without the shared internet nodes)": node_budget(site_nodes),
        "shared internet nodes of the single-site core": node_budget(shared_nodes),
        "SOC module (malcolm, wazuh, soc-analyst, soc-purple)": node_budget(soc["spec"]["nodes"]),
        "remote-access module (vendor-ws, dmz-vpn, dmz-jump)": node_budget(remote["spec"]["nodes"]),
        # A brewpub: the brewhouse, the cellar, the utilities that serve them
        # (glycol, steam, CIP), and the building; no packaging line, no
        # on-site generation, no DER link.
        "site, brewpub (brewhouse, cellar, utilities, BAS)": node_budget(
            pick(
                "fw-edge",
                "fw-ot",
                "ot-svc",
                "corp-ws",
                "scada-fuxa",
                "historian",
                "ews",
                "hmi-brewhouse",
                "plc-brewhouse",
                "plc-cellar",
                "plc-utilities",
                "rio-brewhouse",
                "rio-cellar",
                "rio-utilities",
                "glycol-chiller",
                "bas-bbmd",
                "bas-fuxa",
                "bas-rtu-taproom",
                "bas-coldroom",
                "bas-vent",
                "sim-ctl",
                "sim-process",
                "sim-building",
            )  # fmt: skip
        ),
        # The CI variant's slice behind the two site firewalls, with the two
        # hosts the head office talks to: one fermentation cell, a historian,
        # and an engineering workstation.
        "site, cellar slice (the ci variant behind the firewalls, with a historian and an EWS)": node_budget(
            pick(
                "fw-edge",
                "fw-ot",
                "ot-svc",
                "scada-fuxa",
                "historian",
                "ews",
                "plc-cellar",
                "rio-cellar",
                "sim-ctl",
                "sim-process",
            )  # fmt: skip
        ),
    }
    full = result.budgets["site, full plant (core without the shared internet nodes)"]
    pub = result.budgets["site, brewpub (brewhouse, cellar, utilities, BAS)"]
    cell = result.budgets["site, cellar slice (the ci variant behind the firewalls, with a historian and an EWS)"]
    soc_b = result.budgets["SOC module (malcolm, wazuh, soc-analyst, soc-purple)"]
    remote_b = result.budgets["remote-access module (vendor-ws, dmz-vpn, dmz-jump)"]
    # The mock internet and the head office's own additions, as the plans size
    # them (docs/multisite/PLANS.md): routers at 1 vCPU and 2048 MB (VyOS) or
    # 512 MB (FRR on Linux), small services at 1 vCPU and 1024 MB.
    internet_vyos = Budget(vms=6, vcpus=6, memory_mb=6 * 2048).add(Budget(vms=6, vcpus=6, memory_mb=6 * 1024))
    internet_frr = Budget(vms=6, vcpus=6, memory_mb=6 * 512).add(Budget(vms=6, vcpus=6, memory_mb=6 * 1024))
    head_office = Budget(vms=3, vcpus=6, memory_mb=3 * 4096)
    sensor = Budget(vms=1, vcpus=4, memory_mb=16384)
    result.budgets["mock internet, VyOS routers (6 routers, 6 small hosts)"] = internet_vyos
    result.budgets["mock internet, FRR routers (6 routers, 6 small hosts)"] = internet_frr
    result.budgets["head office additions (fleet historian, corporate server, second office desktop)"] = head_office
    result.budgets["network sensor at a site (Hedgehog-style, 4 vCPU, 16 GB)"] = sensor
    # Every mix has the mock internet as FRR routers and the head-office additions.
    shared = Budget().add(internet_frr).add(head_office)
    result.plans = {
        "M1: four full plants, central SOC": shared
        .add(full, 4).add(soc_b).add(remote_b),
        "M2: four full plants, central SOC, a network sensor at each of the other three sites": shared
        .add(full, 4).add(soc_b).add(remote_b).add(sensor, 3),
        "M3: two full plants and two brewpubs, central SOC": shared
        .add(full, 2).add(pub, 2).add(soc_b).add(remote_b),
        "M4: one full plant and three cellar slices, central SOC": shared
        .add(full, 1).add(cell, 3).add(soc_b).add(remote_b),
        "M5: four full plants, no SOC and no vendor access": shared
        .add(full, 4),
    }  # fmt: skip

    # 4. The largest mix (M2) on a cluster. The first site's node also runs
    # what the range has once: the mock internet, the head office's additions,
    # the SOC, and the vendor's remote access.
    once = shared.add(soc_b).add(remote_b)
    site = {"fr": full, "hc": full.add(sensor), "ws": full.add(sensor), "gc": full.add(sensor)}
    layouts: dict[str, list[tuple[str, ...]]] = {
        "one node": [("once", "fr", "hc", "ws", "gc")],
        "two nodes": [("once", "fr", "hc"), ("ws", "gc")],
        "three nodes": [("once", "fr"), ("hc", "gc"), ("ws",)],
        "four nodes, one for each site": [("once", "fr"), ("hc",), ("ws",), ("gc",)],
        "five nodes, one for each site and one for what the range has once": [("once",), ("fr",), ("hc",), ("ws",), ("gc",)],
    }  # fmt: skip
    label = {"once": "internet, head office, SOC, vendor access"}
    for title, groups in layouts.items():
        placement = Placement(title=title)
        for number, group in enumerate(groups, start=1):
            cost = Budget()
            for part in group:
                cost = cost.add(once if part == "once" else site[part])
            placement.nodes[f"node {number}"] = "; ".join(label.get(part, f"site {part}") for part in group)
            placement.cost[f"node {number}"] = cost
        result.placements.append(placement)
    return merged


# ------------------------------- 4. Cluster -------------------------------


def cluster_workflow(result: Result, merged: list[dict[str, Any]], out: Path, schema_dir: Path | None) -> None:
    """Write a workflow that pins each site's VMs to a node, and check it against phenix's schema."""
    single = yaml.safe_load(WORKFLOW.read_text())["spec"]
    vlans: dict[str, int] = {}
    for site in SITES:
        for alias, number in single["vlans"].items():
            if alias in SHARED_VLANS:
                vlans.setdefault(alias, number)
            else:
                vlans[site.vlan(alias)] = number + VLAN_STEP * site.index
    node_of = {site.code: f"compute{site.index + 1}" for site in SITES}
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
            # The sites use 100 to 499, and the internet's links 500 to 539 (PLANS.md section 3).
            "vlanRange": {"min": 100, "max": 599},
            "defaultBridge": "cherrymtn-fleet",
            # VLANs cross the nodes inside GRE tunnels, so the switch between them needs no trunk.
            "useGREMesh": True,
            # The node that runs phenix runs VMs too.
            "deployMode": "all",
            "schedules": schedules,
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    path = out / "phenix-fleet.yml"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False))
    ids = Counter(vlans.values())
    used = {i.get("vlan") for n in merged for i in n.get("network", {}).get("interfaces", [])} - {None, ""}
    result.cluster = {
        "VMs with a node in `schedules`": f"{len(schedules)} of {len(merged)} (the four sites; the shared nodes are not stamped)",
        "VMs for each node": json.dumps(dict(sorted(Counter(schedules.values()).items()))),
        "VLAN aliases with an ID": len(vlans),
        "VLAN IDs given twice": sorted(number for number, count in ids.items() if count > 1),
        "aliases of the site topologies without an ID": sorted(used - set(vlans)),
        "VLAN range": f"{workflow['spec']['vlanRange']['min']} to {workflow['spec']['vlanRange']['max']}",
        "default bridge": f"{workflow['spec']['defaultBridge']} ({len(workflow['spec']['defaultBridge'])} characters; OVS allows 15)",
    }  # fmt: skip
    if not schema_dir:
        return
    schema = schema_dir / "workflow.schema.json"
    checker = shutil.which("check-jsonschema")
    if checker and schema.exists():
        proc = subprocess.run(
            [checker, "--schemafile", str(schema), "--base-uri", schema_dir.as_uri() + "/", str(path)],
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
    (EVIDENCE / "result.json").write_text(result.model_dump_json(indent=2) + "\n")
    lines = [
        "# multisite-stamp",
        "",
        "## plantgen on a stamped plant.yaml",
        "",
        "| Run | Result | Detail |",
        "|---|---|---|",
    ]
    for probe in result.probes:
        detail = probe.detail.replace("|", "\\|")
        lines.append(f"| {probe.name} | {'pass' if probe.passed else '**fail**'} | {detail} |")
    lines += ["", "## Four stamped site topologies in one experiment", "", "| Item | Value |", "|---|---|"]
    for key, value in result.merge.items():
        shown = json.dumps(value) if isinstance(value, list) else str(value)
        lines.append(f"| {key} | {shown} |")
    lines += ["", "## Building blocks", "", "| Block | VMs | vCPUs | Memory (GB) |", "|---|---:|---:|---:|"]
    for key, budget in result.budgets.items():
        lines.append(f"| {key} | {budget.row()} |")
    lines += [
        "",
        "## Site mixes on a host with 48 cores and 96 threads",
        "",
        "| Mix | VMs | vCPUs | Memory (GB) | vCPUs per thread | vCPUs per core |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, budget in result.plans.items():
        lines.append(f"| {key} | {budget.row()} | {budget.vcpus / 96:.1f} | {budget.vcpus / 48:.1f} |")
    lines += [
        "",
        f"## Mix M2 on a cluster of nodes with {NODE_THREADS // 2} cores, {NODE_THREADS} threads, and {NODE_MEMORY_GB} GB",
        "",
        "| Cluster | Node | Runs | VMs | vCPUs | Memory (GB) | vCPUs per thread | Memory used |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for placement in result.placements:
        for node, runs in placement.nodes.items():
            cost = placement.cost[node]
            lines.append(
                f"| {placement.title} | {node} | {runs} | {cost.row()} | {cost.vcpus / NODE_THREADS:.2f} "
                f"| {cost.memory_mb / 1024 / NODE_MEMORY_GB:.0%} |"
            )
    lines += ["", "## A workflow that pins each site to a node", "", "| Item | Value |", "|---|---|"]
    for key, value in result.cluster.items():
        shown = json.dumps(value) if isinstance(value, list) else str(value)
        lines.append(f"| {key} | {shown} |")
    (EVIDENCE / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schema", type=Path, help="the schemas/ directory of a sceptre-phenix checkout")
    parser.add_argument("--out", type=Path, help="keep the stamped topology files here")
    args = parser.parse_args()
    result = Result()
    with tempfile.TemporaryDirectory(prefix="cm-stamp-") as tmp:
        probe_plantgen(result, Path(tmp) / "plantgen")
        out = args.out or Path(tmp) / "topologies"
        merged = stamp_topologies(result, out, args.schema)
        cluster_workflow(result, merged, out, args.schema)
    write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
