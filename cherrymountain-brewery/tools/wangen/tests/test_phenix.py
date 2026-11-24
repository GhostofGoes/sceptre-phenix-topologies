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
        "internet-to-inet",
    ]
    assert "10.20.30.11" in tests["fc-historian"][0]["testScript"]


def test_soh_pings_the_internet_host_from_every_site() -> None:
    apps = {app["name"]: app for app in phenix.scenario()["spec"]["apps"]}
    for host, tests in apps["soh"]["metadata"]["hostCustomTests"].items():
        last = tests[-1]
        assert last["name"] == "internet-to-inet", host
        assert "198.18.110.11" in last["testScript"]


def scorch() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, list[str]]]]:
    apps = {app["name"]: app for app in phenix.scenario()["spec"]["apps"]}
    metadata = apps["scorch"]["metadata"]
    return (
        {component["name"]: component for component in metadata["components"]},
        {run["name"]: run for run in metadata["runs"]},
    )


def commands(component: dict[str, Any], hostname: str, stage: str) -> list[dict[str, Any]]:
    return next(vm for vm in component["metadata"]["vms"] if vm["hostname"] == hostname).get(stage, [])


def test_every_scorch_run_names_components_that_exist_and_act_in_that_stage() -> None:
    components, runs = scorch()
    assert list(runs) == ["site-uplink-silent", "head-office-uplink-silent", "isp-loses-transit", "head-office-restart"]
    known = {router.name for router in model.ROUTERS} | {lan.host for lan in model.LANS}
    for run in runs.values():
        for stage in ("configure", "start", "stop"):
            for name in run.get(stage, []):
                component = components[name]
                if component["type"] == "cc":
                    vms = component["metadata"]["vms"]
                    assert {vm["hostname"] for vm in vms} <= known
                    assert any(vm.get(stage) for vm in vms), f"{name} does nothing in {stage}"


def test_a_fault_is_made_at_the_isp_and_repaired_in_the_same_run() -> None:
    components, runs = scorch()
    for name, router, interface in (
        ("site-uplink-silent", "shadecast", "eth3"),
        ("head-office-uplink-silent", "shadecast", "eth2"),
        ("isp-loses-transit", "cb-west", "eth1"),
    ):
        fault = commands(components[name], router, "start")[0]["args"]
        assert f"iifname {interface} counter drop" in fault
        assert f"oifname {interface} counter drop" in fault
        assert commands(components[name], router, "stop")[0]["args"] == "nft delete table inet cmfault"
        assert runs[name]["start"][0] == name
        assert runs[name]["stop"][0] == name
    # The interface names are those of the topology.
    found = nodes()
    by_vlan = {interface["vlan"]: interface["name"] for interface in found["shadecast"]["network"]["interfaces"]}
    assert (by_vlan["WAN-SC-FC"], by_vlan["WAN-SC-FR"]) == ("eth3", "eth2")
    by_vlan = {interface["vlan"]: interface["name"] for interface in found["cb-west"]["network"]["interfaces"]}
    assert by_vlan["INET-CBW-SC"] == "eth1"


def test_the_site_checks_name_its_tunnels_and_gateways() -> None:
    components, _runs = scorch()
    moved = commands(components["site-on-second-uplink"], "fc-fw-edge", "start")
    assert [command["validator"] for command in moved] == ["grep -q 'dev vti102'", "grep -q 'via 198.18.102.1 '"]
    back = commands(components["site-on-first-uplink"], "fc-fw-edge", "stop")
    assert [command["validator"] for command in back] == ["grep -q 'dev vti101'", "grep -q 'via 203.0.113.9 '"]
    assert moved[0]["args"] == "ip route show 10.20.0.0/16"


def test_every_site_host_checks_the_head_office_and_the_internet_in_every_stage() -> None:
    components, _runs = scorch()
    reach = {vm["hostname"]: vm for vm in components["reach"]["metadata"]["vms"]}
    assert sorted(reach) == ["cs-historian", "ep-historian", "fc-historian", "fr-historian"]
    for stage in ("configure", "start", "stop"):
        assert [command["args"].split()[-1] for command in reach["cs-historian"][stage]] == [
            "10.20.30.11",
            "198.18.110.11",
        ]
        assert all(command["validator"] == "grep -q ' 0% packet loss'" for command in reach["cs-historian"][stage])
        assert [command["args"].split()[-1] for command in reach["fr-historian"][stage]] == ["198.18.110.11"]


def test_the_transit_run_does_not_ask_a_site_for_the_internet_during_the_fault() -> None:
    """The failover check proves the ISP's first router only, so fc keeps ShadeCast's default route."""
    components, runs = scorch()
    assert "reach-vpn" in runs["isp-loses-transit"]["start"]
    assert "reach" not in runs["isp-loses-transit"]["start"]
    assert "reach" in runs["isp-loses-transit"]["stop"]
    during = commands(components["reach-vpn"], "fc-historian", "start")
    assert [command["args"].split()[-1] for command in during] == ["10.20.30.11"]
    # The head office's host still reaches the internet, through the other ISP.
    assert commands(components["reach-vpn"], "fr-historian", "start")[0]["args"].endswith("198.18.110.11")
