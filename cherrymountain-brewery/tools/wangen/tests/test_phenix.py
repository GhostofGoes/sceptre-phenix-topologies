"""The wan variant's phenix files: current, complete, and consistent with the configuration lists."""

from __future__ import annotations

import ipaddress
from typing import Any

import pytest

from wangen import cli, model, phenix, vyos


@pytest.fixture(autouse=True)
def _loaded() -> None:
    model.load()


def nodes() -> dict[str, dict[str, Any]]:
    return {node["general"]["hostname"]: node for node in phenix.topology()["spec"]["nodes"]}


def test_the_committed_files_are_current() -> None:
    assert cli.stale() == [], "run `python -m wangen` in tools/wangen"


def test_the_variant_has_every_router_and_one_host_for_each_lan() -> None:
    found = nodes()
    routers = [name for name, node in found.items() if node["type"] == "Router"]
    assert routers == [router.name for router in model.ROUTERS]
    assert [name for name in found if name not in routers] == [lan.host for lan in model.LANS]
    assert sum(node["hardware"]["vcpus"] for node in found.values()) == 25
    assert sum(node["hardware"]["memory"] for node in found.values()) == 25 * 1024


def test_a_routers_interfaces_are_the_ones_its_list_configures() -> None:
    """The `config` list names eth0, eth1, ... by the order of the router's links."""
    found = nodes()
    for router in model.ROUTERS:
        interfaces = found[router.name]["network"]["interfaces"]
        for (eth, link, _other, _gateway, own), interface in zip(vyos.uplinks(router), interfaces, strict=False):
            assert (interface["name"], interface["address"]) == (eth, own)
            assert interface["vlan"] == phenix.link_vlan(link)
        assert [interface["name"] for interface in interfaces] == [f"eth{n}" for n in range(len(interfaces))]


def test_both_ends_of_a_vlan_share_a_subnet_and_no_address_is_used_twice() -> None:
    by_vlan: dict[str, set[ipaddress.IPv4Network]] = {}
    addresses: list[str] = []
    for node in nodes().values():
        for interface in node["network"]["interfaces"]:
            network = ipaddress.ip_interface(f"{interface['address']}/{interface['mask']}").network
            by_vlan.setdefault(interface["vlan"], set()).add(network)
            addresses.append(interface["address"])
    assert all(len(networks) == 1 for networks in by_vlan.values()), by_vlan
    assert len(addresses) == len(set(addresses))
    assert set(by_vlan) == set(phenix.vlans())


def test_the_vlan_ids_follow_the_fleets_plan() -> None:
    plan = phenix.vlans()
    access = {alias: vid for alias, vid in plan.items() if alias.startswith("WAN-")}
    assert sorted(access.values()) == list(range(500, 507))
    assert plan["FR-OPS"] == 106
    assert plan["FC-PLANT"] == 221
    assert plan["EP-OPS"] == 306
    assert plan["CS-PLANT"] == 421
    assert phenix.workflow()["spec"]["vlans"] == plan


def test_every_router_gets_its_list_and_every_line_is_one_the_app_takes() -> None:
    apps = {app["name"]: app for app in phenix.scenario()["spec"]["apps"]}
    hosts = {host["hostname"]: host["metadata"]["config"] for host in apps["vrouter"]["hosts"]}
    assert list(hosts) == [router.name for router in model.ROUTERS]
    for name, lines in hosts.items():
        assert lines == vyos.commands(model.BY_NAME[name], psk=phenix.psk)
        assert all(vyos.refused(line) == "" for line in lines)
    edge = "\n".join(hosts["fc-fw-edge"])
    assert "PRE-SHARED-KEY-OF-THIS-TUNNEL" not in edge
    assert f"secret '{phenix.psk(model.TUNNELS[0])}'" in edge


def test_soh_pings_through_the_vpn_in_both_directions() -> None:
    apps = {app["name"]: app for app in phenix.scenario()["spec"]["apps"]}
    soh = apps["soh"]["metadata"]
    assert soh["skipHosts"] == [router.name for router in model.ROUTERS]
    tests = soh["hostCustomTests"]
    assert sorted(tests) == ["cs-historian", "ep-historian", "fc-historian", "fr-historian"]
    assert [test["name"] for test in tests["fr-historian"]] == [
        "vpn-to-fc-historian",
        "vpn-to-ep-historian",
        "vpn-to-cs-historian",
    ]
    assert "10.20.30.11" in tests["fc-historian"][0]["testScript"]
