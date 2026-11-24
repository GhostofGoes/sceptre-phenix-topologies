"""What a site needs besides its plant files: its workflow and its own copies
of the injected files that hold a name or an address of the plant."""

from __future__ import annotations

import ipaddress
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from plantgen import injects, sites, topology, workflow
from plantgen.cli import (
    DEFAULT_PLANT,
    TOPOLOGY_DIR,
    SiteSet,
    SiteSources,
    generate_site_set,
    main,
)
from plantgen.model import PlantError, PlantFile, load_file

CODES: list[str] = ["fr", "ep", "cs", "fc"]
CONFIGS: Path = TOPOLOGY_DIR / "phenix-configs"
INJECTS: Path = TOPOLOGY_DIR / "phenix-injects"
#: The aliases that the WAN generator pins too: one zone of each site, and the
#: seven access links.
SHARED_WITH_WAN: set[str] = {
    "FR-OPS",
    "FC-PLANT",
    "EP-OPS",
    "CS-PLANT",
    "WAN-SC-FR",
    "WAN-TW-FR",
    "WAN-SC-FC",
    "WAN-MM-FC",
    "WAN-TW-EP",
    "WAN-SC-EP",
    "WAN-BC-CS",
}
#: The private blocks of the Front Range site (wan.yaml, `site_blocks`) and its
#: MGMT network.
FRONT_RANGE = re.compile(r"\b(?:10\.(?:10|20|30|40)|172\.16)\.\d+\.\d+")
#: A Front Range address that a copy at another site keeps, and why.
KEPT: dict[str, dict[str, str]] = {
    "vrouter/cm-vyos-config.sh": {
        "10.40.10.11": "the fleet has one Wazuh, at the head office",
    },
}


@pytest.fixture(scope="module")
def plant_file() -> PlantFile:
    return load_file(DEFAULT_PLANT)


@pytest.fixture(scope="module")
def specs(plant_file: PlantFile) -> dict[str, sites.SiteSpec]:
    return {site.code: site for site in sites.load(plant=plant_file)}


@pytest.fixture(scope="module")
def sources() -> SiteSources:
    return SiteSources(
        wan=sites.load_wan(),
        nodes=topology.load_nodes(CONFIGS),
        vlan_ids=workflow.load_single_site(TOPOLOGY_DIR / workflow.SINGLE_SITE),
        injects=INJECTS,
    )


@pytest.fixture(scope="module")
def sets(
    plant_file: PlantFile, specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> dict[str, SiteSet]:
    """What plantgen writes for each site now."""
    return {
        code: generate_site_set(site, plant_file, sources)
        for code, site in specs.items()
    }


def _vlans(made: SiteSet) -> dict[str, int]:
    pinned: dict[str, int] = yaml.safe_load(made.workflow)["spec"]["vlans"]
    return pinned


def _aliases(made: SiteSet) -> set[str]:
    nodes = yaml.safe_load(made.topology)["spec"]["nodes"]
    return {leg["vlan"] for node in nodes for leg in node["network"]["interfaces"]}


def _copies(made: SiteSet) -> dict[str, str]:
    prefix = f"{injects.DIRECTORY}/"
    return {
        path.removeprefix(prefix): text
        for path, text in made.files.items()
        if path.startswith(prefix)
    }


# --- the workflow ---


@pytest.mark.parametrize("code", CODES)
def test_the_committed_workflow_is_current(code: str, sets: dict[str, SiteSet]) -> None:
    path = TOPOLOGY_DIR / workflow.FILE_NAME.format(code=code)
    assert path.read_text(encoding="utf-8") == sets[code].workflow


@pytest.mark.parametrize("code", CODES)
def test_a_workflow_pins_every_vlan_of_its_topology_and_no_other(
    code: str, sets: dict[str, SiteSet]
) -> None:
    assert set(_vlans(sets[code])) == _aliases(sets[code])


def test_the_ids_follow_the_fleets_plan(sets: dict[str, SiteSet]) -> None:
    """PLANS.md, section 3: the examples of its table, and each site's range."""
    ids = {code: _vlans(made) for code, made in sets.items()}
    assert ids["fr"]["FR-CTRL"] == 108
    assert ids["ep"]["EP-OPS"] == 306
    assert ids["ep"]["EP-CTRL-BH"] == 320
    assert ids["fc"]["FC-PLANT"] == 221
    assert ids["cs"]["CS-PLANT"] == 421
    own = {
        code: sorted(n for alias, n in pinned.items() if not alias.startswith("WAN-"))
        for code, pinned in ids.items()
    }
    assert {code: (found[0], found[-1]) for code, found in own.items()} == {
        "fr": (104, 117),
        "fc": (209, 221),
        "ep": (304, 324),
        "cs": (409, 421),
    }
    links = sorted(
        n for pinned in ids.values() for a, n in pinned.items() if a.startswith("WAN-")
    )
    assert links == list(range(500, 507))


def test_no_id_is_given_twice_in_the_fleet(sets: dict[str, SiteSet]) -> None:
    seen: dict[int, str] = {}
    for made in sets.values():
        for alias, number in _vlans(made).items():
            assert number not in seen, (alias, seen[number])
            seen[number] = alias
    assert len(seen) == 53


def test_the_wan_generator_and_plantgen_agree_on_every_shared_alias(
    sets: dict[str, SiteSet],
) -> None:
    """Both pin the access links and one zone of each site. The committed
    phenix-wan.yml is the WAN generator's word."""
    wan_ids: dict[str, int] = yaml.safe_load(
        (TOPOLOGY_DIR / "phenix-wan.yml").read_text(encoding="utf-8")
    )["spec"]["vlans"]
    site_ids = {a: n for made in sets.values() for a, n in _vlans(made).items()}
    shared = set(wan_ids) & set(site_ids)
    assert shared == SHARED_WITH_WAN
    assert {a: site_ids[a] for a in shared} == {a: wan_ids[a] for a in shared}


def test_a_workflow_is_the_sites_own(sets: dict[str, SiteSet]) -> None:
    bridges = set()
    for code, made in sets.items():
        spec: dict[str, Any] = yaml.safe_load(made.workflow)["spec"]
        assert spec["topology"] == spec["scenario"] == f"cherrymountain-site-{code}"
        assert spec["vlanRange"] == {"min": 100, "max": 599}
        assert len(spec["defaultBridge"]) <= workflow.MAX_BRIDGE_NAME
        bridges.add(spec["defaultBridge"])
    others = {"phenix", "cherrymtn", "cherrymtn-lite", "cherrymtn-ci", "cherrymtn-wan"}
    assert len(bridges) == len(sets) and not bridges & others


def test_a_vlan_that_is_no_zone_of_the_site_and_no_access_link_is_refused(
    specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    with pytest.raises(PlantError, match="MIRROR is not an access link"):
        workflow.vlan_ids(specs["fc"], {"MIRROR"}, sources.vlan_ids, sources.wan)
    with pytest.raises(PlantError, match="EP-OPS is not an access link"):
        workflow.vlan_ids(specs["fc"], {"EP-OPS"}, sources.vlan_ids, sources.wan)


def test_a_site_with_more_zones_of_its_own_than_ids_is_refused(
    specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    many = {f"FC-NEW{n}" for n in range(81)}
    with pytest.raises(PlantError, match="outside the site's 201 to 299"):
        workflow.vlan_ids(specs["fc"], many, sources.vlan_ids, sources.wan)


def test_two_zones_with_one_id_are_refused(
    specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    single = {**sources.vlan_ids, "NEW": 120}
    with pytest.raises(PlantError, match="both get the ID 220"):
        workflow.vlan_ids(specs["fc"], {"FC-NEW", "FC-OFFICE"}, single, sources.wan)


# --- the site's own copies of injected files ---


@pytest.mark.parametrize("code", CODES)
def test_the_committed_copies_are_current(code: str, sets: dict[str, SiteSet]) -> None:
    root = DEFAULT_PLANT.parent / "generated" / "fleet" / code / injects.DIRECTORY
    have = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    copies = _copies(sets[code])
    assert have == set(copies)
    for path, text in copies.items():
        assert (root / path).read_text(encoding="utf-8") == text, path
        assert bool((root / path).stat().st_mode & 0o111) == text.startswith("#!")


def test_a_site_copies_what_its_nodes_inject(sets: dict[str, SiteSet]) -> None:
    """The brewpub has one router and no ot-svc, BBMD, or jump host."""
    assert {code: sorted(_copies(made)) for code, made in sets.items()} == {
        "fr": sorted(injects.RENDERERS),
        "ep": sorted(injects.RENDERERS),
        "cs": sorted(p for p in injects.RENDERERS if not p.startswith("guacamole/")),
        "fc": ["vrouter/cm-vyos-config.sh"],
    }


@pytest.mark.parametrize("code", CODES)
def test_a_node_injects_the_sites_copy_and_every_other_file_as_it_is(
    code: str, sets: dict[str, SiteSet]
) -> None:
    own = f"{injects.PREFIX}{injects.SITE_DIRECTORY.format(code=code)}/"
    for node in yaml.safe_load(sets[code].topology)["spec"]["nodes"]:
        for injection in node.get("injections", []):
            source: str = injection["src"]
            assert source.startswith(injects.PREFIX)
            path = source.removeprefix(own).removeprefix(injects.PREFIX)
            assert (path in injects.RENDERERS) == source.startswith(own), source
            assert (INJECTS / path).is_file(), source


@pytest.mark.parametrize("code", ["ep", "cs", "fc"])
def test_no_copy_keeps_a_front_range_address(
    code: str, sets: dict[str, SiteSet]
) -> None:
    """A comment may name one. A line that a program reads may not, but for `KEPT`."""
    for path, text in _copies(sets[code]).items():
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            found = set(FRONT_RANGE.findall(line)) - set(KEPT.get(path, {}))
            assert not found, (path, line)


def test_front_range_keeps_its_addresses(sets: dict[str, SiteSet]) -> None:
    """Only the names change at `fr`: the hand-written lines that held an
    address hold the same address."""
    copies = _copies(sets["fr"])
    for path in ("bbmd/bas-bbmd.env", "bbmd/ot-svc.env", "ot-svc/dnsmasq-plant.conf"):
        hand_written = (INJECTS / path).read_text(encoding="utf-8").splitlines()
        assert [ln for ln in copies[path].splitlines() if "Generated by" not in ln] == (
            hand_written
        ), path
    assert "OT_SVC=10.20.30.13\n" in copies["vrouter/cm-vyos-config.sh"]
    assert "IP:10.10.20.11" in copies["guacamole/cm-guacamole-start"]


def test_the_copies_of_a_site_with_a_control_network_for_each_cell(
    sets: dict[str, SiteSet],
) -> None:
    copies = _copies(sets["ep"])
    script = copies["vrouter/cm-vyos-config.sh"]
    assert "      ep-fw-edge | ep-fw-ot)\n" in script
    assert "        host=$(hostname) && host=${host#ep-}\n" in script
    assert "OT_SVC=10.22.30.13\n" in script
    assert 'if [ "$host" = fw-ot ]; then' in script
    assert "CM_BBMD_ADDRESS=10.22.30.13/0:47808\n" in copies["bbmd/ot-svc.env"]
    assert "CM_BBMD_ADDRESS=10.32.10.5/24:47808\n" in copies["bbmd/bas-bbmd.env"]
    table = "CM_BBMD_BDT=10.22.30.13:47808,10.32.10.5:47808\n"
    assert table in copies["bbmd/ot-svc.env"] and table in copies["bbmd/bas-bbmd.env"]
    assert "listen-address=10.22.30.13\n" in copies["ot-svc/dnsmasq-plant.conf"]
    start = copies["guacamole/cm-guacamole-start"]
    assert '-subj "/CN=ep-dmz-jump.cherrymountain.test/' in start
    assert "DNS:ep-dmz-jump.cherrymountain.test,IP:10.12.20.11" in start
    served = "    server_name ep-dmz-jump.cherrymountain.test;\n"
    assert served in copies["guacamole/nginx.conf"]
    # chrony serves the networks of its clients: each cell's control network.
    hook = copies["ot-svc/60-ot-svc.sh"]
    allowed = re.search(r"for net in (.*); do", hook)
    assert allowed is not None
    networks = allowed.group(1).split()
    for cell in ("10.22.41.0/24", "10.22.42.0/24", "10.22.46.0/24", "10.32.10.0/24"):
        assert cell in networks
    assert not [n for n in networks if n.startswith("10.12.10.")]  # the office


def test_the_brewpubs_router_takes_its_time_from_the_sites_server(
    sets: dict[str, SiteSet], specs: dict[str, sites.SiteSpec]
) -> None:
    assert specs["fc"].time_server == "scada-fuxa"
    script = _copies(sets["fc"])["vrouter/cm-vyos-config.sh"]
    assert "      fc-fw-edge)\n" in script
    assert "OT_SVC=10.21.10.40\n" in script


@pytest.mark.parametrize("code", ["fr", "ep", "cs"])
def test_the_zone_file_has_every_address_of_every_node(
    code: str, sets: dict[str, SiteSet]
) -> None:
    lines = [
        line.split()
        for line in _copies(sets[code])["ot-svc/plant.hosts"].splitlines()
        if not line.startswith("#")
    ]
    names = [name for _, name in lines]
    assert len(names) == len(set(names))
    by_name = {name: ipaddress.ip_address(address) for address, name in lines}
    nodes = yaml.safe_load(sets[code].topology)["spec"]["nodes"]
    legs = sum(len(node["network"]["interfaces"]) for node in nodes)
    assert len(by_name) == legs
    for node in nodes:
        first = node["network"]["interfaces"][0]
        name = node["general"]["hostname"]
        assert name.startswith(f"{code}-")
        assert str(by_name[name]) == first["address"]
    edge = f"{code}-fw-edge"
    assert f"{edge}-eth1" in by_name


def _context(text: str, specs: dict[str, sites.SiteSpec]) -> injects.Context:
    return injects.Context(
        site=specs["fc"],
        plant=None,  # type: ignore[arg-type]
        nodes=[],
        path="some/file.sh",
        targets=["fc-fw-edge"],
        text=text,
    )


def test_an_edit_sets_one_line_and_says_who_wrote_the_file(
    specs: dict[str, sites.SiteSpec],
) -> None:
    text = "#!/bin/sh\n# A comment.\nif true; then\n  KEY=old\nfi\n"
    made = injects.edit(_context(text, specs), [injects.Line("KEY=", "KEY=new")])
    assert made.splitlines() == [
        "#!/bin/sh",
        "# Generated by plantgen for the site fc from phenix-injects/some/file.sh. "
        "Do not edit.",
        "# A comment.",
        "if true; then",
        "  KEY=new",
        "fi",
    ]
    plain = injects.edit(_context("KEY=old\n", specs), [injects.Line("KEY=", "KEY=n")])
    assert plain.splitlines()[0].startswith("# Generated by plantgen")


@pytest.mark.parametrize(
    ("text", "count"), [("OTHER=1\n", 0), ("KEY=1\nKEY=2\n", 2)], ids=["none", "two"]
)
def test_an_edit_needs_its_line_exactly_once(
    text: str, count: int, specs: dict[str, sites.SiteSpec]
) -> None:
    """When the hand-written file changes its shape, plantgen stops by name."""
    with pytest.raises(PlantError, match=f"some/file.sh: {count} lines start with"):
        injects.edit(_context(text, specs), [injects.Line("KEY=", "KEY=new")])


def test_a_hand_written_file_without_its_line_stops_the_generator(
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    sources: SiteSources,
    tmp_path: Path,
) -> None:
    changed = tmp_path / "phenix-injects"
    shutil.copytree(INJECTS / "vrouter", changed / "vrouter")
    script = changed / "vrouter" / "cm-vyos-config.sh"
    text = script.read_text(encoding="utf-8")
    script.write_text(text.replace("OT_SVC=", "TIME_SERVER="), encoding="utf-8")
    with pytest.raises(PlantError, match=r"site fc: injects: .*'OT_SVC='"):
        generate_site_set(specs["fc"], plant_file, sources._replace(injects=changed))


def test_check_fails_on_a_changed_copy_and_on_a_changed_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--check` covers the copies and the workflow file."""
    out = tmp_path / "out"
    assert main(["--site", "fc", "--out", str(out)]) == 0
    assert main(["--site", "fc", "--out", str(out), "--check"]) == 0
    copy = out / injects.DIRECTORY / "vrouter" / "cm-vyos-config.sh"
    assert copy.stat().st_mode & 0o111
    copy.write_text(copy.read_text(encoding="utf-8").replace("fc-fw-edge", "fw-edge"))
    assert main(["--site", "fc", "--out", str(out), "--check"]) == 1
    assert "stale: injects/vrouter/cm-vyos-config.sh" in capsys.readouterr().err
    assert main(["--site", "fc", "--out", str(out)]) == 0
    flow = out / workflow.FILE_NAME.format(code="fc")
    flow.write_text(flow.read_text(encoding="utf-8").replace("221", "222"))
    assert main(["--site", "fc", "--out", str(out), "--check"]) == 1
    assert "stale: phenix-site-fc.yml" in capsys.readouterr().err
