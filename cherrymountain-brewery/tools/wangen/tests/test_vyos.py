"""The configuration lists: equal to the wan-bgp spike's without the additions, and the additions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wangen import model, vyos

SPIKE: Path = Path(__file__).resolve().parents[3] / "spikes" / "wan-bgp"
ROUTERS: list[str] = [
    "cb-west",
    "cb-south",
    "shadecast",
    "tumbleweed",
    "marmot",
    "bristlecone",
    "fr-fw-edge",
    "fc-fw-edge",
    "ep-fw-edge",
    "cs-fw-edge",
]


@pytest.fixture
def spike():  # noqa: ANN201
    """The spike's sketch module, with its own model."""
    sys.path.insert(0, str(SPIKE))
    try:
        import vyos_sketch
    finally:
        sys.path.remove(str(SPIKE))
    before = vyos_sketch.HUB_OPENS
    yield vyos_sketch
    vyos_sketch.HUB_OPENS = before


@pytest.fixture(autouse=True)
def _loaded():  # noqa: ANN202
    model.load()
    yield
    model.load()


@pytest.mark.parametrize("name", ROUTERS)
@pytest.mark.parametrize("kind", [model.WIREGUARD, model.VTI])
@pytest.mark.parametrize("hub_opens", [False, True])
def test_the_list_equals_the_spikes(spike, name: str, kind: str, hub_opens: bool) -> None:  # noqa: ANN001
    spike.HUB_OPENS = hub_opens
    model.HUB_OPENS = hub_opens
    theirs = spike.commands(spike.lab.BY_NAME[name], kind=kind)
    ours = vyos.commands(model.BY_NAME[name], kind=kind, spike_only=True)
    assert ours == theirs


def test_the_default_kind_is_the_one_wan_yaml_names() -> None:
    edge = model.BY_NAME["fc-fw-edge"]
    assert vyos.commands(edge) == vyos.commands(edge, kind=model.KIND)
    assert any(line.startswith("set vpn ipsec ") for line in vyos.commands(edge))


def test_every_line_is_one_the_vrouter_app_takes() -> None:
    for name in ROUTERS:
        for line in vyos.commands(model.BY_NAME[name]):
            assert vyos.refused(line) == "", line


def added(name: str, kind: str = model.VTI) -> list[str]:
    """What the generator adds to the spike's list of a router."""
    router = model.BY_NAME[name]
    spike = set(vyos.commands(router, kind=kind, spike_only=True))
    return [line for line in vyos.commands(router, kind=kind) if line not in spike]


def test_the_head_office_admits_from_a_tunnel_only_its_sites_sources() -> None:
    lines = added("fr-fw-edge")
    # fc has two tunnels (marks 101 and 102); the group holds its blocks and both tunnel ends.
    group = [line.rsplit(" ", 1)[1] for line in lines if " network-group VPN-FC network " in line]
    assert group == ["10.11.0.0/16", "10.21.0.0/16", "10.255.0.1/32", "10.255.0.3/32"]
    for chain in ("forward", "input"):
        for rule, vti, site in ((100, "vti101", "FC"), (101, "vti102", "FC"), (104, "vti105", "CS")):
            base = f"set firewall ipv4 {chain} filter rule {rule}"
            assert f"{base} action drop" in lines
            assert f"{base} inbound-interface name {vti}" in lines
            assert f"{base} source group network-group '!VPN-{site}'" in lines
            assert f"{base} log" in lines
    assert not [line for line in added("fc-fw-edge") if line.startswith("set firewall ipv4 ")]


def test_the_source_filter_names_wireguard_interfaces_with_wireguard_tunnels() -> None:
    lines = added("fr-fw-edge", kind=model.WIREGUARD)
    assert "set firewall ipv4 forward filter rule 100 inbound-interface name wg101" in lines
    assert not [line for line in lines if " name vti" in line]


def test_the_head_office_translates_to_its_nat_address_on_both_isps() -> None:
    lines = [line for line in added("fr-fw-edge") if line.startswith("set nat ")]
    assert lines == [
        "set nat source rule 100 outbound-interface name eth0",
        "set nat source rule 100 source group network-group PRIVATE",
        "set nat source rule 100 translation address 198.18.105.2",
        "set nat source rule 110 outbound-interface name eth1",
        "set nat source rule 110 source group network-group PRIVATE",
        "set nat source rule 110 translation address 198.18.105.2",
    ]
    assert model.NAT_ADDRESS != model.HUB_ENDPOINT


def test_a_site_translates_to_the_address_of_each_uplink() -> None:
    lines = [line for line in added("ep-fw-edge") if line.startswith("set nat ")]
    assert [line for line in lines if "translation" in line] == [
        "set nat source rule 100 translation address masquerade",
        "set nat source rule 110 translation address masquerade",
    ]
    assert "set firewall group network-group PRIVATE network 192.168.0.0/16" in added("ep-fw-edge")


def test_a_sites_default_routes_are_failover_routes_not_static_routes() -> None:
    lines = vyos.commands(model.BY_NAME["fc-fw-edge"])
    assert not [line for line in lines if line.startswith("set protocols static route 0.0.0.0/0 ")]
    base = "set protocols failover route 0.0.0.0/0 next-hop"
    assert [line for line in lines if line.startswith(base)] == [
        f"{base} 203.0.113.9 interface eth0",
        f"{base} 203.0.113.9 metric 10",
        f"{base} 203.0.113.9 check target 203.0.113.9 interface eth0",
        f"{base} 203.0.113.9 check timeout 2",
        f"{base} 198.18.102.1 interface eth1",
        f"{base} 198.18.102.1 metric 20",
        f"{base} 198.18.102.1 check target 198.18.102.1 interface eth1",
        f"{base} 198.18.102.1 check timeout 2",
    ]
    # The tunnels' own tables keep their static routes.
    assert "set protocols static table 101 route 0.0.0.0/0 next-hop 203.0.113.9" in lines
    # The head office gets its default routes from BGP.
    assert not [line for line in vyos.commands(model.BY_NAME["fr-fw-edge"]) if " failover " in line]


@pytest.mark.parametrize(("isp", "neighbor"), [("shadecast", "203.0.113.2"), ("tumbleweed", "198.18.101.2")])
def test_an_isp_sends_a_default_route_only_while_it_holds_a_transit_prefix(isp: str, neighbor: str) -> None:
    lines = vyos.commands(model.BY_NAME[isp])
    originate = f"set protocols bgp neighbor {neighbor} address-family ipv4-unicast default-originate"
    assert f"{originate} route-map DEFAULT-IF-TRANSIT" in lines
    assert originate not in lines
    # Continental Backbone's prefixes: what both of its routers originate.
    transit = [
        line.rsplit(" ", 1)[1]
        for line in lines
        if line.startswith("set policy prefix-list TRANSIT ") and " prefix " in line
    ]
    assert transit == ["198.18.0.0/24", "198.18.110.0/24"]
    assert "set policy route-map DEFAULT-IF-TRANSIT rule 10 match ip address prefix-list TRANSIT" in lines


def test_default_requires_replaces_the_providers_prefixes() -> None:
    model.BY_NAME["shadecast"].default_requires = ["198.18.101.0/24"]
    lines = vyos.commands(model.BY_NAME["shadecast"])
    assert "set policy prefix-list TRANSIT rule 10 prefix 198.18.101.0/24" in lines
    assert not [line for line in lines if "198.18.110.0/24" in line and "TRANSIT" in line]


def test_an_isp_with_no_provider_and_no_customer_prefix_has_no_condition() -> None:
    for name in ("cb-west", "cb-south", "marmot", "bristlecone"):
        assert not [line for line in vyos.commands(model.BY_NAME[name]) if "default-originate" in line]
    assert not [line for line in vyos.commands(model.BY_NAME["cb-west"]) if "TRANSIT" in line]


def test_the_head_office_takes_each_isps_routes_and_no_private_prefix() -> None:
    lines = vyos.commands(model.BY_NAME["fr-fw-edge"])
    for neighbor in ("203.0.113.1", "198.18.101.1"):
        base = f"set protocols bgp neighbor {neighbor} address-family ipv4-unicast route-map import"
        assert f"{base} FROM-ISP" in lines
    assert "set policy route-map FROM-ISP rule 10 match ip address prefix-list INTERNET" in lines
    internet = [line.split("INTERNET ", 1)[1] for line in lines if line.startswith("set policy prefix-list INTERNET ")]
    assert internet == [
        "rule 1 action deny",
        "rule 1 prefix 10.0.0.0/8",
        "rule 1 le 32",
        "rule 2 action deny",
        "rule 2 prefix 172.16.0.0/12",
        "rule 2 le 32",
        "rule 3 action deny",
        "rule 3 prefix 192.168.0.0/16",
        "rule 3 le 32",
        "rule 10 action permit",
        "rule 10 prefix 0.0.0.0/0",
        "rule 10 le 24",
    ]
    # Every route from an ISP gets the endpoint as its source, and a site's block gets the LAN address.
    assert "set policy route-map SET-SOURCE rule 10 match ip address prefix-list INTERNET" in lines
    assert "set policy route-map SET-SOURCE rule 10 set src 198.18.105.1" in lines
    assert "set policy route-map SET-SOURCE rule 15 match ip address prefix-list SITE-NETS" in lines
    assert not [line for line in lines if "DEFAULT-ONLY" in line]
