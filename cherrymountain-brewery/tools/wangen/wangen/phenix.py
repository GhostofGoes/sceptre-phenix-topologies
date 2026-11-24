"""The `wan` variant as phenix files: the topology, the scenario, and the workflow.

The variant is the mock internet, the head office's edge, the three other site
edges, and one host on each site's LAN (docs/multisite/PLANS.md, stage 2). It
has no plant. The topology and the `config` lists follow what the wan-bgp spike
ran on VyOS in CI with four routers; this is all ten.
"""

from __future__ import annotations

import hashlib
import ipaddress
from typing import Any, Final

import yaml

from wangen import model, vyos

NAME: Final[str] = "cherrymountain-wan"
BRIDGE: Final[str] = "cherrymtn-wan"
#: The fleet's range (PLANS.md, section 3), so that the fleet can take these IDs unchanged.
VLAN_RANGE: Final[tuple[int, int]] = (100, 599)
#: The access links (a site edge to an ISP) take 500 and up; the internet's own links take 510 and up.
ACCESS_BASE: Final[int] = 500
CORE_BASE: Final[int] = 510
#: Every pre-shared key of the committed scenario comes from this text. The keys
#: are placeholders for a closed range, like plantgen's WireGuard keys.
PSK_SEED: Final[str] = "cherrymountain-wan"

ROUTER_IMAGE: Final[str] = "vyos.qc2"
HOST_IMAGE: Final[str] = "cm-svc.qc2"


def psk(tunnel: model.Tunnel) -> str:
    return hashlib.sha256(f"{PSK_SEED}/{tunnel.name}".encode()).hexdigest()[:40]


def is_access(link: model.Link) -> bool:
    """Whether a link joins a site's edge to an ISP."""
    return link.a in model.SITE_BLOCKS or link.b in model.SITE_BLOCKS


def link_vlan(link: model.Link) -> str:
    kind = "WAN" if is_access(link) else "INET"
    return f"{kind}-{model.BY_NAME[link.a].short}-{model.BY_NAME[link.b].short}".upper()


def vlans() -> dict[str, int]:
    """Every VLAN alias of the variant with its ID."""
    plan: dict[str, int] = {}
    access = [link for link in model.LINKS if is_access(link)]
    core = [link for link in model.LINKS if not is_access(link)]
    for base, links in ((ACCESS_BASE, access), (CORE_BASE, core)):
        for number, link in enumerate(links):
            plan[link_vlan(link)] = base + number
    for lan in model.LANS:
        plan[lan.vlan] = lan.vlan_id
    if len(set(plan.values())) != len(plan):
        raise model.WanError(f"two VLANs have one ID: {plan}")
    low, high = VLAN_RANGE
    if not all(low <= vid <= high for vid in plan.values()):
        raise model.WanError(f"a VLAN ID is outside {low} to {high}: {plan}")
    return dict(sorted(plan.items(), key=lambda item: item[1]))


def _interface(name: str, vlan: str, address: str, mask: int, gateway: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "vlan": vlan, "address": address, "mask": mask}
    if gateway:
        entry["gateway"] = gateway
    return {**entry, "proto": "static", "type": "ethernet"}


def site_of(router: model.Router) -> str:
    """`fr`, `fc`, `ep`, or `cs` for a site's edge; `inet` for a router of the internet."""
    return router.short if router.name in model.SITE_BLOCKS else "inet"


def topology() -> dict[str, Any]:
    """The topology. A router's interfaces are in the order of its links, as its `config` list expects."""
    nodes: list[dict[str, Any]] = []
    for router in model.ROUTERS:
        interfaces = [
            _interface(eth, link_vlan(link), own, ipaddress.ip_network(link.net).prefixlen)
            for eth, link, _other, _gateway, own in vyos.uplinks(router)
        ]
        for lan in model.LANS:
            if lan.router == router.name:
                mask = ipaddress.ip_network(lan.net).prefixlen
                interfaces.append(_interface(f"eth{len(interfaces)}", lan.vlan, lan.gateway(), mask))
        nodes.append(
            {
                "type": "Router",
                "general": {"hostname": router.name, "description": f"{router.title} (AS{router.asn})"},
                "labels": {"site": site_of(router)},
                "hardware": {
                    "os_type": "vyos",
                    "drives": [{"image": ROUTER_IMAGE, "inject_partition": 3}],
                    "memory": 2048,
                    "vcpus": 2,
                },
                "network": {"interfaces": interfaces},
            }
        )
    for lan in model.LANS:
        router = model.BY_NAME[lan.router]
        mask = ipaddress.ip_network(lan.net).prefixlen
        nodes.append(
            {
                "type": "VirtualMachine",
                "general": {"hostname": lan.host, "description": f"A host on {lan.vlan}, behind {router.name}"},
                "labels": {"site": site_of(router)},
                "hardware": {
                    "os_type": "linux",
                    "drives": [{"image": HOST_IMAGE, "inject_partition": 1}],
                    "memory": 1024,
                    "vcpus": 1,
                },
                "network": {"interfaces": [_interface("eth0", lan.vlan, lan.host_address(), mask, lan.gateway())]},
            }
        )
    return {
        "apiVersion": "phenix.sandia.gov/v1",
        "kind": "Topology",
        "metadata": {"name": NAME},
        "spec": {"nodes": nodes},
    }


def _ping_test(name: str, address: str) -> dict[str, str]:
    return {
        "name": name,
        "executor": "bash",
        "testScript": f"if ping -c 3 -W 2 {address} >/dev/null 2>&1; then\n  echo REACHED\nelse\n  echo UNREACHED\nfi\n",
        "testStdout": "REACHED",
    }


def soh_tests() -> dict[str, list[dict[str, str]]]:
    """The pings of each site host.

    Through the VPN: every site to the head office, and the head office to every
    site. Outside the VPN: every site host to the host on the internet, which
    needs the edge's NAT and a default route.
    """
    lans = {lan.router: lan for lan in model.LANS}
    hub = lans[model.HUB]
    internet = next(lan for lan in model.LANS if lan.advertise_public)
    sites = sorted({tunnel.site for tunnel in model.TUNNELS}, key=[r.name for r in model.ROUTERS].index)
    tests: dict[str, list[dict[str, str]]] = {
        hub.host: [_ping_test(f"vpn-to-{lans[site].host}", lans[site].host_address()) for site in sites]
    }
    for site in sites:
        tests[lans[site].host] = [_ping_test(f"vpn-to-{hub.host}", hub.host_address())]
    for tested in tests.values():
        tested.append(_ping_test(f"internet-to-{internet.host}", internet.host_address()))
    return tests


def scenario() -> dict[str, Any]:
    configs = {router.name: vyos.commands(router, psk=psk) for router in model.ROUTERS}
    return {
        "apiVersion": "phenix.sandia.gov/v2",
        "kind": "Scenario",
        "metadata": {"name": NAME, "annotations": {"topology": NAME}},
        "spec": {
            "apps": [
                {"name": "startup"},
                {
                    "name": "vrouter",
                    "hosts": [{"hostname": name, "metadata": {"config": lines}} for name, lines in configs.items()],
                },
                {
                    "name": "soh",
                    "metadata": {
                        "startupDelay": "5m",
                        "c2Timeout": "15m",
                        "testReachability": "off",
                        "skipHosts": [router.name for router in model.ROUTERS],
                        "hostCustomTests": soh_tests(),
                    },
                },
            ]
        },
    }


def workflow() -> dict[str, Any]:
    low, high = VLAN_RANGE
    return {
        "apiVersion": "phenix.sandia.gov/v0",
        "kind": "Workflow",
        "metadata": {},
        "spec": {
            "auto": {"create": "${BRANCH_NAME}", "update": True, "restart": True},
            "topology": NAME,
            "scenario": NAME,
            "vlans": vlans(),
            "vlanRange": {"min": low, "max": high},
            "defaultBridge": BRIDGE,
        },
    }


HEADERS: Final[dict[str, str]] = {
    "topology": """\
# cherrymountain-wan: the mock internet and the company's WAN, with no plant
# (docs/multisite/PLANS.md, stage 2). Ten VyOS routers and five hosts:
#   the transit carrier (cb-west, cb-south), four ISPs (shadecast, tumbleweed,
#   marmot, bristlecone), the head office's edge (fr-fw-edge), the edges of the
#   three other sites (fc, ep, cs), one host on each site's LAN, and one host
#   on the internet.
# 15 VMs, 25 vCPUs, 25 GB. A router's interfaces are in the order that its
# `config` list in scenario-wan.yml expects.
#
# Generated by tools/wangen from wan/wan.yaml. Do not edit: change wan.yaml and
# run `python -m wangen` in tools/wangen.
""",
    "scenario": """\
# cherrymountain-wan scenario (docs/multisite/PLANS.md, stage 2). The `vrouter`
# app gets a `config` list for each router: BGP and its policies, BFD, the
# tunnels to the head office, each tunnel's tie to its uplink, the discard
# routes, NAT, a site's failover default routes, and the head office's source
# filter on each tunnel. The list needs the `config` option of the vrouter app
# (GhostofGoes/sceptre-phenix#27). The soh app pings from each site's host:
# through the VPN, and to the host on the internet.
#
# The pre-shared keys are placeholders derived from a fixed text, for a closed
# range. They are not secrets.
#
# Generated by tools/wangen from wan/wan.yaml. Do not edit: change wan.yaml and
# run `python -m wangen` in tools/wangen.
""",
    "workflow": """\
# Workflow for the wan variant: the mock internet and the company's WAN, with
# no plant (docs/multisite/PLANS.md, stage 2). Apply it with its own branch:
#   docker exec phenix phenix workflow apply \\
#     -b cherrymountain-wan -c phenix-wan.yml /phenix/topologies/cherrymountain-brewery
# The VLAN IDs are the fleet's: a site's zone keeps its ID, the access links to
# the ISPs take 500 to 506, and the internet's own links take 510 and up.
#
# Generated by tools/wangen from wan/wan.yaml. Do not edit: change wan.yaml and
# run `python -m wangen` in tools/wangen.
""",
}


def render(kind: str) -> str:
    """One generated file as text."""
    document = {"topology": topology, "scenario": scenario, "workflow": workflow}[kind]()
    body = yaml.safe_dump(document, sort_keys=False, default_flow_style=False, width=10_000, allow_unicode=True)
    return HEADERS[kind] + body
