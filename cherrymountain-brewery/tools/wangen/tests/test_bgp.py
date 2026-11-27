"""The `bgp` block: what moves out of a router's commands, and what stays in `config`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from wangen import bgp, model, vyos

DATA: Path = Path(__file__).resolve().parent / "data"
KINDS: list[str] = [model.WIREGUARD, model.VTI]


@pytest.fixture(autouse=True)
def _loaded():  # noqa: ANN202
    model.load()
    yield
    model.load()


def routers() -> list[model.Router]:
    return list(model.ROUTERS)


@pytest.mark.parametrize("name", ["fr-fw-edge", "tumbleweed"])
def test_the_block_equals_the_fixture_that_phenix_renders(name: str) -> None:
    """phenix renders these two blocks in its tests and gets the generator's commands."""
    want = yaml.safe_load((DATA / f"bgp-{name}.yaml").read_text(encoding="utf-8"))["bgp"]
    block, _rest = bgp.split(vyos.commands(model.BY_NAME[name]))
    assert block == want


@pytest.mark.parametrize("kind", KINDS)
def test_config_keeps_every_other_command_in_its_order(kind: str) -> None:
    for router in routers():
        lines = vyos.commands(router, kind=kind)
        _block, rest = bgp.split(lines)
        assert rest == [line for line in lines if not bgp.owned(line.split()[1:])], router.name
        assert not [line for line in rest if line.startswith(("set protocols bgp ", "set protocols bfd "))]
        assert not [line for line in rest if line.startswith("set policy ") and " local-route " not in line]


def test_the_tunnels_the_routes_and_the_source_route_map_stay_in_config() -> None:
    _block, rest = bgp.split(vyos.commands(model.BY_NAME["fc-fw-edge"], kind=model.VTI))
    assert "set system ip protocol bgp route-map SET-SOURCE" in rest
    for start in ("set interfaces vti ", "set vpn ipsec ", "set protocols static ", "set protocols failover "):
        assert [line for line in rest if line.startswith(start)], start
    assert [line for line in rest if line.startswith("set policy local-route rule ")]


def test_every_name_that_a_block_uses_is_in_the_block() -> None:
    """phenix refuses a block that names a list, a route map, or a BFD profile that nothing defines."""
    kinds = ("routeMaps", "prefixLists", "communityLists", "bfdProfiles")
    for router in routers():
        block, _rest = bgp.split(vyos.commands(router))
        defined = {kind: {item["name"] for item in block.get(kind, [])} for kind in kinds}
        used: dict[str, set[str]] = {kind: set() for kind in kinds}
        for neighbor in block["neighbors"]:
            used["routeMaps"] |= {neighbor[way]["routeMap"] for way in ("import", "export") if way in neighbor}
            used["routeMaps"] |= {neighbor[key] for key in ("defaultOriginateRouteMap",) if key in neighbor}
            used["bfdProfiles"] |= {neighbor[key] for key in ("bfdProfile",) if key in neighbor}
        for route_map in block["routeMaps"]:
            for rule in route_map["rules"]:
                match: dict[str, Any] = rule.get("match", {})
                used["prefixLists"] |= {match[key] for key in ("prefixList",) if key in match}
                used["communityLists"] |= {match[key] for key in ("communityList",) if key in match}
        for kind in kinds:
            assert used[kind] <= defined[kind], (router.name, kind)
        assert used["routeMaps"], router.name


def test_a_block_has_the_speaker_and_one_neighbor_for_each_session() -> None:
    block, _rest = bgp.split(vyos.commands(model.BY_NAME["fr-fw-edge"]))
    assert block["localAS"] == model.BY_NAME["fr-fw-edge"].asn
    assert block["multipath"] == {"asPathRelax": True, "ebgp": 2}
    # Two ISPs, and one neighbor inside each of the five tunnels.
    assert len(block["neighbors"]) == 2 + len(model.TUNNELS)
    first = block["neighbors"][0]
    assert first["timers"] == {
        "keepalive": model.CORE_KEEPALIVE_S,
        "holdtime": model.CORE_HOLD_S,
        "connectRetry": 5,
    }
    assert first["softReconfigurationInbound"] is True
    assert first["bfdProfile"] == "WAN"


def test_an_isp_sends_the_head_office_a_default_route_under_a_condition() -> None:
    block, rest = bgp.split(vyos.commands(model.BY_NAME["shadecast"]))
    head_office = next(n for n in block["neighbors"] if n["remoteAS"] == model.BY_NAME[model.HUB].asn)
    assert head_office["defaultOriginateRouteMap"] == "DEFAULT-IF-TRANSIT"
    assert "defaultOriginate" not in head_office
    assert block["communityLists"][0]["rules"][0]["regex"] == f"_{model.BY_NAME['shadecast'].asn}:1_"
    # What is left is the route that the ISP's own prefix stands on.
    assert rest == [f"set protocols static route {model.BY_NAME['shadecast'].originates[0]} blackhole distance 250"]


@pytest.mark.parametrize(
    "line",
    [
        "set protocols bgp neighbor 198.18.0.1 peer-group ISPS",
        "set protocols bgp address-family ipv6-unicast network 2001:db8::/32",
        "set policy route-map FROM-ISP rule 10 set as-path prepend '64505 64505'",
        "set protocols bfd peer 198.18.0.1 multihop",
        "delete policy prefix-list OWN",
    ],
)
def test_a_command_of_the_block_that_it_has_no_key_for_is_an_error(line: str) -> None:
    with pytest.raises(model.WanError, match="has no key for"):
        bgp.split(["set protocols bgp system-as 64505", line])


def test_lists_without_a_speaker_are_an_error() -> None:
    with pytest.raises(model.WanError, match="system-as"):
        bgp.split(["set policy prefix-list OWN rule 10 action permit"])


def test_commands_with_no_routing_give_no_block() -> None:
    lines = ["set system time-zone UTC", "set interfaces dummy dum0 address 198.18.105.1/32"]
    assert bgp.split(lines) == ({}, lines)


def test_the_metadata_of_a_router_is_its_block_and_its_config() -> None:
    for router in routers():
        metadata = vyos.metadata(router)
        block, rest = bgp.split(vyos.commands(router))
        assert metadata["bgp"] == block
        assert metadata.get("config", []) == rest
    # cb-south announces no prefix of its own, so it has no line outside the block.
    assert list(vyos.metadata(model.BY_NAME["cb-south"])) == ["bgp"]
