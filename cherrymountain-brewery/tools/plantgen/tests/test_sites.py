"""plant/sites.yaml: the four sites, their checks, and the spike that ran them."""

from __future__ import annotations

import copy
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from plantgen import sites
from plantgen.cli import DEFAULT_PLANT, main
from plantgen.model import PlantError, PlantFile, load_file

SPIKE: Path = DEFAULT_PLANT.parents[1] / "spikes" / "site-profiles"
STAMP: Path = DEFAULT_PLANT.parents[1] / "spikes" / "multisite-stamp"


@pytest.fixture(scope="module")
def plant() -> PlantFile:
    return load_file(DEFAULT_PLANT)


@pytest.fixture(scope="module")
def wan() -> dict[str, Any]:
    return yaml.safe_load(sites.WAN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fleet(plant: PlantFile) -> dict[str, sites.SiteSpec]:
    return {site.code: site for site in sites.load(plant=plant)}


def test_the_four_sites_load_and_fit_plant_yaml_and_wan_yaml(
    fleet: dict[str, sites.SiteSpec], plant: PlantFile
) -> None:
    assert list(fleet) == ["fr", "ep", "cs", "fc"]
    # With the sensor of each site but fr, the 39, 35, 26, and 19 VMs of PLANS.md.
    counts = {code: len(site.hosts(plant)) for code, site in fleet.items()}
    assert counts == {"fr": 39, "ep": 34, "cs": 25, "fc": 18}
    assert [site.sensor for site in fleet.values()] == [False, True, True, True]


def test_sites_yaml_equals_the_spikes_profiles(
    fleet: dict[str, sites.SiteSpec],
) -> None:
    """The spike's run is the evidence for these values, so the two must not drift."""
    sys.path.insert(0, str(SPIKE))
    try:
        import profiles  # noqa: PLC0415
    finally:
        sys.path.remove(str(SPIKE))
    assert [profile.code for profile in profiles.PROFILES] == list(fleet)
    for profile in profiles.PROFILES:
        theirs = profile.model_dump()
        ours = fleet[profile.code].model_dump()
        # The spike held an uplink as a pair, and dropped no unit inside an area.
        theirs["uplinks"] = [
            {"isp": isp, "address": address} for isp, address in theirs["uplinks"]
        ]
        assert theirs.pop("drop_units") == {}
        for key in ("modules", "extra_hosts", "drop_areas", "drop_hosts"):
            theirs[key] = list(theirs[key])
        assert ours == theirs
    assert sites.SHARED_HOSTS == profiles.stamp.SHARED_HOSTS
    assert sites.SHARED_VLANS == profiles.stamp.SHARED_VLANS
    assert sites.PRIVATE_BLOCKS == profiles.stamp.PRIVATE_BLOCKS
    assert {module: hosts for module, hosts in sites.MODULES.items()} == {
        "soc": profiles.SOC_HOSTS,
        "remote": profiles.REMOTE_HOSTS,
    }
    text = "10.20.10.12 10.50.1.1 172.16.0.5 192.168.20.11 203.0.113.2"
    for profile in profiles.PROFILES:
        assert fleet[profile.code].address(text) == profile.site().address(text)


def test_a_site_moves_names_and_the_companys_blocks(
    fleet: dict[str, sites.SiteSpec],
) -> None:
    ep = fleet["ep"]
    assert ep.host("historian") == "ep-historian"
    assert ep.host("vendor-ws") == "vendor-ws"
    assert ep.vlan("OPS") == "EP-OPS"
    assert ep.vlan("INET") == "INET"
    assert ep.address("10.20.30.11/24") == "10.22.30.11/24"
    assert ep.address("172.16.0.9") == "172.18.0.9"
    # A block that is not the company's stays: the internet, and cs's own plan.
    assert ep.address("203.0.113.2 192.168.20.11") == "203.0.113.2 192.168.20.11"


def test_the_routed_networks_of_each_layout(
    fleet: dict[str, sites.SiteSpec], plant: PlantFile
) -> None:
    networks = {code: site.routed_networks(plant) for code, site in fleet.items()}
    assert networks["fc"] == {
        "FC-OFFICE": "10.11.10.0/24",
        "FC-PLANT": "10.21.10.0/24",
    }
    assert networks["cs"] == {
        "CS-OFFICE": "192.168.10.0/24",
        "CS-PLANT": "192.168.20.0/24",
        "CS-BAS": "192.168.30.0/24",
    }
    # A control network for each cell, and none for all of the PLCs.
    cells = {vlan for vlan in networks["ep"] if vlan.startswith("EP-CTRL")}
    assert cells == {
        "EP-CTRL-BH",
        "EP-CTRL-CEL",
        "EP-CTRL-PKG",
        "EP-CTRL-UTL",
        "EP-CTRL-PWR",
    }
    assert networks["fr"]["FR-CTRL"] == "10.20.10.0/24"
    assert "FR-SOC" in networks["fr"]
    assert "EP-SOC" not in networks["ep"]


def test_merged_vlans_give_no_address_twice(
    fleet: dict[str, sites.SiteSpec], plant: PlantFile
) -> None:
    for site in fleet.values():
        found = sites.site_interfaces(site, plant)
        pairs = list(found.values())
        assert len(pairs) == len(set(pairs)), site.code
    fc = sites.site_interfaces(fleet["fc"], plant)
    # The pub's flat network: a PLC keeps its octet, and the SCADA host moves by 30.
    assert fc[("plc-cellar", "IF0")] == ("FC-PLANT", "10.21.10.12")
    vlan, address = fc[("scada-fuxa", "IF0")]
    old = plant.hosts["scada-fuxa"].interfaces["IF0"].ip.split("/")[0]
    assert vlan == "FC-PLANT"
    assert int(address.rsplit(".", 1)[1]) == int(old.rsplit(".", 1)[1]) + 30


def _raw() -> dict[str, Any]:
    return yaml.safe_load(sites.DEFAULT_PATH.read_text(encoding="utf-8"))


def _site(doc: dict[str, Any], code: str) -> dict[str, Any]:
    return next(site for site in doc["sites"] if site["code"] == code)


def _no_shift(doc: dict[str, Any]) -> None:
    _site(doc, "fc")["moves"]["OPS"]["shift"] = 0


def _swap_uplinks(doc: dict[str, Any]) -> None:
    _site(doc, "ep")["uplinks"].reverse()


def _outside_block(doc: dict[str, Any]) -> None:
    _site(doc, "ep")["cells"]["plc-power"]["subnet"] = "10.60.46.0/24"


def _wrong_lan(doc: dict[str, Any]) -> None:
    for move in _site(doc, "fc")["moves"].values():
        if move["vlan"] == "PLANT":
            move["subnet"] = "10.20.50.0/24"


def _cell_of_absent_plc(doc: dict[str, Any]) -> None:
    cell = {"vlan": "CTRL-PWR", "subnet": "10.20.46.0/24"}
    _site(doc, "fc").update(cells={"plc-power": cell})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda doc: _site(doc, "ep").update(code="fr"), "code is used twice"),
        (lambda doc: _site(doc, "ep").update(index=0), "index is used twice"),
        (lambda doc: _site(doc, "cs").update(drop_areas=["XYZ"]), "drop_areas names"),
        (lambda doc: _site(doc, "cs")["drop_hosts"].append("plc-x"), "drop_hosts"),
        (lambda doc: _site(doc, "fc").update(time_server="ot-svc"), "time_server"),
        (_cell_of_absent_plc, "does not have"),
        (lambda doc: _site(doc, "ep").update(extra_hosts=["historian"]), "extra_hosts"),
        (
            lambda doc: _site(doc, "cs")["moves"]["BAS"].update(gateway="fw-ot"),
            "gateway",
        ),
        (_no_shift, "give a move a shift"),
        (_swap_uplinks, "wan.yaml gives ep-fw-edge"),
        (_outside_block, "is in no block that wan.yaml gives ep-fw-edge"),
        (_wrong_lan, "wan.yaml puts its host on FC-PLANT"),
        (lambda doc: _site(doc, "cs").update(color="red"), "sites.yaml"),
    ],
)
def test_a_wrong_entry_is_refused_by_name(
    tmp_path: Path,
    plant: PlantFile,
    wan: dict[str, Any],
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    doc = copy.deepcopy(_raw())
    change(doc)
    path = tmp_path / "sites.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(PlantError, match=message):
        sites.check(sites.load_file(path), plant, wan)


def test_the_command_checks_sites_yaml(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check", "--variant", "ci"]) == 0
    assert (
        "sites.yaml fits plant.yaml and wan.yaml (4 sites)" in capsys.readouterr().out
    )
