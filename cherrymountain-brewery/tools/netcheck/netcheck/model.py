"""Load the topology, scenario, and workflow YAML into one network model.

The loaders follow what phenix does when it applies a workflow directory:
``includeTopologies`` appends the included topologies' nodes after the
topology's own, and an app with ``fromScenario`` takes its metadata and hosts
from the named scenario.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

#: cherrymountain-brewery/ (this file is tools/netcheck/netcheck/model.py).
ROOT: Path = Path(__file__).resolve().parents[3]


class LoadError(Exception):
    """A config could not be read or does not have the shape phenix needs."""


class Iface(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    vlan: str
    address: str
    mask: int
    gateway: str | None = None
    dns: list[str] = Field(default_factory=list)
    proto: str = "static"
    ruleset_in: str | None = None
    ruleset_out: str | None = None

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.IPv4Network(f"{self.address}/{self.mask}", strict=False)


class Route(BaseModel):
    destination: str
    next: str


class Injection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    src: str
    dst: str
    permissions: str | None = None


class Rule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    action: str
    protocol: str = "tcp"
    description: str | None = None
    stateful: bool = False
    source: dict[str, Any] | None = None
    destination: dict[str, Any] | None = None


class Ruleset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    default: str
    description: str | None = None
    rules: list[Rule] = Field(default_factory=list)


class NatRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    interface: str
    srcAddr: str | None = None  # noqa: N815 - phenix field name
    destAddr: str | None = None  # noqa: N815
    dstPort: int | None = None  # noqa: N815
    protocol: str | None = None
    translation: str


class IpsecTunnel(BaseModel):
    local: str
    remote: str


class IpsecSite(BaseModel):
    local: str
    remote: str
    peer: str
    tunnels: list[IpsecTunnel] = Field(default_factory=list)


class Emulator(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    egress: list[str] = Field(default_factory=list)
    bandwidth: str | None = None
    delay: int | None = None
    loss: int | None = None


class RouterConfig(BaseModel):
    """What the vrouter app says about one router."""

    acl_inbound: dict[str, str] = Field(default_factory=dict)  # iface -> ruleset
    acl_outbound: dict[str, str] = Field(default_factory=dict)
    rulesets: dict[str, Ruleset] = Field(default_factory=dict)
    snat: list[NatRule] = Field(default_factory=list)
    dnat: list[NatRule] = Field(default_factory=list)
    ipsec: list[IpsecSite] = Field(default_factory=list)
    emulators: list[Emulator] = Field(default_factory=list)


class TNode(BaseModel):
    """A topology node as phenix sees it."""

    model_config = ConfigDict(extra="ignore")

    hostname: str
    type: str
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    delay_c2: list[str] = Field(default_factory=list)
    os_type: str = "linux"
    image: str = ""
    inject_partition: int | None = None
    vcpus: int = 1
    memory_mb: int = 512
    injections: list[Injection] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    interfaces: list[Iface] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    router: RouterConfig = Field(default_factory=RouterConfig)
    source_file: str = ""

    @property
    def is_router(self) -> bool:
        return self.type.lower() in ("router", "firewall")

    def iface(self, name: str) -> Iface | None:
        for i in self.interfaces:
            if i.name == name:
                return i
        return None



class App(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    fromScenario: str | None = None  # noqa: N815
    disabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    hosts: list[dict[str, Any]] = Field(default_factory=list)


class Scenario(BaseModel):
    name: str
    annotations: dict[str, str]
    apps: list[App]
    source_file: str = ""

    def app(self, name: str) -> App | None:
        for a in self.apps:
            if a.name == name:
                return a
        return None

    def app_order(self) -> list[str]:
        return [a.name for a in self.apps]


class Workflow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topology: str
    scenario: str | None = None
    vlans: dict[str, int] = Field(default_factory=dict)
    vlan_range: dict[str, int] = Field(default_factory=dict)
    default_bridge: str | None = None
    auto: dict[str, Any] = Field(default_factory=dict)
    source_file: str = ""


class Topology(BaseModel):
    name: str
    nodes: list[TNode]
    includes: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)

    def node(self, hostname: str) -> TNode:
        for n in self.nodes:
            if n.hostname == hostname:
                return n
        raise KeyError(hostname)

    def names(self) -> list[str]:
        return [n.hostname for n in self.nodes]


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        doc: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LoadError(f"{path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise LoadError(f"{path}: not a YAML mapping")
    return doc


def _configs(root: Path) -> dict[tuple[str, str], tuple[Path, dict[str, Any]]]:
    """Every config under phenix-configs/ and the workflow files, by kind and name."""
    out: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    paths: list[Path] = sorted((root / "phenix-configs").glob("*.y*ml"))
    paths += sorted(root.glob("phenix*.yml"))
    for path in paths:
        doc: dict[str, Any] = read_yaml(path)
        kind: str = str(doc.get("kind", ""))
        name: str = str((doc.get("metadata") or {}).get("name", path.stem))
        out[(kind, name)] = (path, doc)
    return out


def _node(raw: dict[str, Any], source: str) -> TNode:
    general: dict[str, Any] = raw.get("general") or {}
    hardware: dict[str, Any] = raw.get("hardware") or {}
    network: dict[str, Any] = raw.get("network") or {}
    drives: list[dict[str, Any]] = hardware.get("drives") or [{}]
    delay: dict[str, Any] = raw.get("delay") or {}
    memory: Any = hardware.get("memory", 512)
    try:
        node: TNode = TNode(
            hostname=general["hostname"],
            type=raw.get("type", "VirtualMachine"),
            description=general.get("description", ""),
            labels=raw.get("labels") or {},
            annotations=raw.get("annotations") or {},
            delay_c2=[c["hostname"] for c in (delay.get("c2") or [])],
            os_type=hardware.get("os_type", "linux"),
            image=drives[0].get("image", ""),
            inject_partition=drives[0].get("inject_partition"),
            vcpus=int(hardware.get("vcpus", 1)),
            memory_mb=int(memory),
            injections=raw.get("injections") or [],
            commands=raw.get("commands") or [],
            interfaces=network.get("interfaces") or [],
            routes=network.get("routes") or [],
            source_file=source,
        )
    except (KeyError, ValueError) as exc:
        raise LoadError(f"{source}: bad node {general.get('hostname')!r}: {exc}") from exc
    for rs in network.get("rulesets") or []:
        node.router.rulesets[rs["name"]] = Ruleset(**rs)
    for i in node.interfaces:
        if i.ruleset_in:
            node.router.acl_inbound[i.name] = i.ruleset_in
        if i.ruleset_out:
            node.router.acl_outbound[i.name] = i.ruleset_out
    return node


def _load_topology(
    configs: dict[tuple[str, str], tuple[Path, dict[str, Any]]],
    name: str,
    root: Path,
    seen: tuple[str, ...] = (),
) -> Topology:
    if name in seen:
        raise LoadError(f"cyclic includeTopologies: {' -> '.join((*seen, name))}")
    if ("Topology", name) not in configs:
        raise LoadError(f"no topology named {name}")
    path, doc = configs[("Topology", name)]
    spec: dict[str, Any] = doc.get("spec") or {}
    rel: str = path.relative_to(root).as_posix()
    nodes: list[TNode] = [_node(n, rel) for n in spec.get("nodes") or []]
    files: list[str] = [rel]
    includes: list[str] = list(spec.get("includeTopologies") or [])
    have: set[str] = {n.hostname for n in nodes}
    for inc in includes:
        child: Topology = _load_topology(configs, inc, root, (*seen, name))
        for n in child.nodes:
            if n.hostname in have:
                raise LoadError(f"duplicate node {n.hostname} in included topology {inc}")
            have.add(n.hostname)
            nodes.append(n)
        files += child.source_files
    return Topology(name=name, nodes=nodes, includes=includes, source_files=files)


def _load_scenario(
    configs: dict[tuple[str, str], tuple[Path, dict[str, Any]]],
    name: str,
    root: Path,
) -> Scenario:
    if ("Scenario", name) not in configs:
        raise LoadError(f"no scenario named {name}")
    path, doc = configs[("Scenario", name)]
    meta: dict[str, Any] = doc.get("metadata") or {}
    apps: list[App] = [App(**a) for a in (doc.get("spec") or {}).get("apps") or []]
    for app in apps:
        if not app.fromScenario:
            continue
        src: Scenario = _load_scenario(configs, app.fromScenario, root)
        donor: App | None = src.app(app.name)
        if donor is None:
            raise LoadError(f"{name}: no app {app.name} in scenario {app.fromScenario}")
        app.metadata = donor.metadata
        app.hosts = donor.hosts
    return Scenario(
        name=name,
        annotations=meta.get("annotations") or {},
        apps=apps,
        source_file=path.relative_to(root).as_posix(),
    )


def _router_config(node: TNode, scenario: Scenario | None) -> None:
    """Fill ``node.router`` from the scenario's vrouter app host entry."""
    if scenario is None:
        return
    app: App | None = scenario.app("vrouter")
    if app is None:
        return
    for host in app.hosts:
        if host.get("hostname") != node.hostname:
            continue
        md: dict[str, Any] = host.get("metadata") or {}
        acl: dict[str, Any] = md.get("acl") or {}
        for rs in acl.get("rulesets") or []:
            node.router.rulesets[rs["name"]] = Ruleset(**rs)
        # phenix: `egress` becomes the interface's inbound ruleset (traffic
        # coming in on that NIC), `ingress` its outbound ruleset.
        node.router.acl_inbound.update(acl.get("egress") or {})
        node.router.acl_outbound.update(acl.get("ingress") or {})
        node.router.snat = [NatRule(**r) for r in md.get("snat") or []]
        node.router.dnat = [NatRule(**r) for r in md.get("dnat") or []]
        node.router.ipsec = [IpsecSite(**s) for s in md.get("ipsec") or []]
        node.router.emulators = [Emulator(**e) for e in md.get("emulators") or []]


class Variant(BaseModel):
    """One variant: workflow, topology (includes merged), scenario."""

    name: str
    workflow: Workflow
    topology: Topology
    scenario: Scenario | None


WORKFLOW_FILES: dict[str, str] = {
    "full": "phenix.yml",
    "lite": "phenix-lite.yml",
    "ci": "phenix-ci.yml",
}


def load_variant(variant: str, root: Path = ROOT) -> Variant:
    configs: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = _configs(root)
    wf_path: Path = root / WORKFLOW_FILES[variant]
    raw: dict[str, Any] = read_yaml(wf_path)
    spec: dict[str, Any] = raw.get("spec") or {}
    workflow: Workflow = Workflow(
        topology=spec["topology"],
        scenario=spec.get("scenario"),
        vlans=spec.get("vlans") or {},
        vlan_range=spec.get("vlanRange") or {},
        default_bridge=spec.get("defaultBridge"),
        auto=spec.get("auto") or {},
        source_file=wf_path.relative_to(root).as_posix(),
    )
    topology: Topology = _load_topology(configs, workflow.topology, root)
    scenario: Scenario | None = (
        _load_scenario(configs, workflow.scenario, root) if workflow.scenario else None
    )
    for n in topology.nodes:
        if n.is_router:
            _router_config(n, scenario)
    return Variant(
        name=variant,
        workflow=workflow,
        topology=topology,
        scenario=scenario,
    )
