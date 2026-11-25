"""What a site needs besides its plant files: its workflow and its own copies
of the injected files that hold a name or an address of the plant."""

from __future__ import annotations

import copy
import ipaddress
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from plantgen import checks, fleet, injects, render, sites, topology, workflow
from plantgen.allocate import allocate
from plantgen.cli import (
    DEFAULT_PLANT,
    TOPOLOGY_DIR,
    SiteSet,
    SiteSources,
    generate_site_set,
    main,
)
from plantgen.model import PlantError, PlantFile, expand, load_file

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
        wan_ids=workflow.load_wan_ids(TOPOLOGY_DIR / workflow.WAN_FILE),
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


def test_a_zone_keeps_its_id_when_another_zone_comes_or_goes(
    specs: dict[str, sites.SiteSpec], sources: SiteSources, sets: dict[str, SiteSet]
) -> None:
    """The ID of a zone that the single site does not have comes from a table
    by the zone's name, not from its place among the site's aliases."""
    for code, made in sets.items():
        today = _vlans(made)
        for gone in today:
            fewer = set(today) - {gone}
            ids = workflow.vlan_ids(specs[code], fewer, sources.vlan_ids, sources.wan)
            assert ids == {a: n for a, n in today.items() if a != gone}, (code, gone)
    # The reviewer's case: the pub's office moves into the plant network.
    alone = workflow.vlan_ids(specs["fc"], {"FC-PLANT"}, sources.vlan_ids, sources.wan)
    assert alone == {"FC-PLANT": 221}
    # A zone that comes: `cs` gets a control network for one cell.
    more = {*_vlans(sets["cs"]), "CS-CTRL-PKG"}
    ids = workflow.vlan_ids(specs["cs"], more, sources.vlan_ids, sources.wan)
    assert ids == dict(
        sorted({**_vlans(sets["cs"]), "CS-CTRL-PKG": 422}.items(), key=lambda i: i[1])
    )


def test_a_new_zone_needs_a_number_of_its_own(
    specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    with pytest.raises(PlantError, match=r"FC-GUEST .* OWN_ZONES"):
        workflow.vlan_ids(specs["fc"], {"FC-GUEST"}, sources.vlan_ids, sources.wan)


def test_the_id_plan_holds_four_sites(
    specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    """The hundreds of the sites end where the access links start. A fifth
    site would get the IDs of the internet's own links (510 and up)."""
    assert sites.MAX_INDEX == 3 == max(site.index for site in specs.values())
    fifth = specs["cs"].model_copy(update={"index": 4})
    with pytest.raises(PlantError, match=r"index 4.*500"):
        workflow.vlan_ids(fifth, {"CS-PLANT"}, sources.vlan_ids, sources.wan)
    data = specs["cs"].model_dump()
    with pytest.raises(ValueError, match="less than or equal to 3"):
        sites.SiteSpec.model_validate({**data, "index": 4})


def test_the_fleet_has_no_id_twice_with_the_wan(sets: dict[str, SiteSet]) -> None:
    wan_ids: dict[str, int] = yaml.safe_load(
        (TOPOLOGY_DIR / "phenix-wan.yml").read_text(encoding="utf-8")
    )["spec"]["vlans"]
    by_site = {code: _vlans(made) for code, made in sets.items()}
    assert workflow.fleet_problems(by_site, wan_ids) == []
    # An ID of the internet's own links, given to a zone of a site.
    clash = {**by_site, "cs": {**by_site["cs"], "CS-PLANT": wan_ids["INET-CBW-CBS"]}}
    found = workflow.fleet_problems(clash, wan_ids)
    assert sorted(found) == [
        "CS-PLANT is 510 in phenix-site-cs.yml and 421 in phenix-wan.yml",
        "the ID 510 is CS-PLANT in phenix-site-cs.yml and INET-CBW-CBS in "
        "phenix-wan.yml",
    ]
    twice = {**by_site, "fc": {**by_site["fc"], "FC-OFFICE": by_site["ep"]["EP-OPS"]}}
    assert workflow.fleet_problems(twice, wan_ids) == [
        "the ID 306 is EP-OPS in phenix-site-ep.yml and FC-OFFICE in phenix-site-fc.yml"
    ]
    outside = {**by_site, "fc": {**by_site["fc"], "FC-OFFICE": 600}}
    assert workflow.fleet_problems(outside, wan_ids) == [
        "FC-OFFICE is 600 in phenix-site-fc.yml, outside the fleet's 100 to 599"
    ]


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
        # The detection content of the SOC is the head office's.
        "ep": sorted(p for p in injects.RENDERERS if not p.startswith("malcolm/")),
        "cs": sorted(
            p for p in injects.RENDERERS if not p.startswith(("guacamole/", "malcolm/"))
        ),
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


# --- which site injects what ---


def _sources(made: SiteSet) -> dict[str, set[str]]:
    """Plain hostname -> the paths under phenix-injects that its node injects."""
    code = yaml.safe_load(made.topology)["metadata"]["name"].rsplit("-", 1)[1]
    own = f"{injects.SITE_DIRECTORY.format(code=code)}/"
    found: dict[str, set[str]] = {}
    for node in yaml.safe_load(made.topology)["spec"]["nodes"]:
        name = node["general"]["hostname"].removeprefix(f"{code}-")
        found[name] = {
            injection["src"].removeprefix(injects.PREFIX).removeprefix(own)
            for injection in node.get("injections", [])
        }
    return found


def test_every_injection_of_todays_nodes_has_a_decision(sources: SiteSources) -> None:
    """`SCOPES` decides each one, with a reason, and no entry is left unused."""
    used = set()
    for node in sources.nodes.values():
        for injection in node.get("injections", []):
            entry = injects.scope(injection["src"])
            assert entry.why
            used.add(entry.path)
    assert used == {entry.path for entry in injects.SCOPES}
    modules = {entry.module for entry in injects.SCOPES}
    assert modules == {None, injects.INTERNET, *sites.MODULES}


def test_the_vendors_accounts_stay_at_the_site_with_the_remote_module(
    sets: dict[str, SiteSet], specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    """`ep` has a jump host of its own and a workstation, and `cs` has a
    workstation. Neither gets the files that create the vendor's accounts."""
    vendor = {entry.path for entry in injects.SCOPES if entry.module == injects.REMOTE}
    assert {
        "guacamole/ridgeline.env",
        "guacamole/cm_vendor_sql.py",
        "ews/70-ews-vendor.sh",
    } < vendor
    today = {
        name: {i["src"].removeprefix(injects.PREFIX) for i in node["injections"]}
        for name, node in sources.nodes.items()
        if "injections" in node
    }
    for code, made in sets.items():
        found = _sources(made)
        if injects.REMOTE in specs[code].modules:
            assert found["dmz-jump"] == today["dmz-jump"]
            assert found["ews"] == today["ews"]
            continue
        for name, paths in found.items():
            assert not paths & vendor, (code, name)
            assert not [p for p in paths if p.startswith("wireguard/")], (code, name)
            # Nothing else is lost: only the vendor's files are gone.
            assert paths == {p for p in today.get(name, set()) if p not in vendor}
    assert "guacamole/cm-guacamole-start" in _sources(sets["ep"])["dmz-jump"]
    assert "ews/cm_plc_tool.py" in _sources(sets["cs"])["ews"]


def test_the_header_says_that_the_jump_hosts_accounts_are_not_designed(
    sets: dict[str, SiteSet],
) -> None:
    said = "company's own accounts are still to be designed."
    assert {code: said in made.topology for code, made in sets.items()} == {
        "fr": False,
        "ep": True,
        "cs": False,
        "fc": False,
    }


def test_an_injection_without_a_decision_stops_the_generator(
    plant_file: PlantFile, specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    nodes = copy.deepcopy(sources.nodes)
    nodes["ews"]["injections"].append(
        {"src": f"{injects.PREFIX}tools/new-tool.sh", "dst": "/usr/local/bin/new-tool"}
    )
    with pytest.raises(PlantError, match=r"no entry of SCOPES decides .*new-tool\.sh"):
        generate_site_set(specs["cs"], plant_file, sources._replace(nodes=nodes))


# --- the steps of the VyOS script that a site's copy leaves out ---

#: What the two steps of the utility's IPsec look like in the script.
UTILITY_STEPS: tuple[str, ...] = (
    "ike-group",
    "ikev2",
    "match-ipsec-in",
    "UTILITY_DERMS",
    "fw-edge | util-rtr)",
)


@pytest.mark.parametrize("code", CODES)
def test_a_sites_script_has_no_step_of_the_edges_wan_side(
    code: str, sets: dict[str, SiteSet]
) -> None:
    """No site's files give the edge the utility's IPsec yet, so a step that
    changes its IKE group would fail on the router. The other steps stay."""
    script = _copies(sets[code])["vrouter/cm-vyos-config.sh"]
    program = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    for words in UTILITY_STEPS:
        assert not [ln for ln in program if words in ln], words
    hand_written = (INJECTS / "vrouter" / "cm-vyos-config.sh").read_text("utf-8")
    assert all(words in hand_written for words in UTILITY_STEPS)
    assert 'try set firewall ipv4 name "$ruleset" default-log' in script
    assert 'try set system syslog remote "$WAZUH" facility all level warning' in script
    assert 'try set service ntp server "$OT_SVC" prefer' in script
    assert script.count("# Left out of this copy: ") == 2
    assert "leave 0\n" in script and script.rstrip().endswith("leave 0")


def test_a_cut_takes_one_whole_step_and_its_comment(
    specs: dict[str, sites.SiteSpec],
) -> None:
    text = "\n".join(
        [
            "#!/bin/sh",
            "A=1",
            "",
            "# About the step.",
            'case "$host" in',
            "  one | two)",
            "    run it",
            "    ;;",
            "esac",
            "",
            "last",
            "",
        ]
    )
    cut = injects.Cut("the step", "one | two)", "esac", "the site has no such part")
    made = injects.edit(_context(text, specs), [], [cut])
    assert made.splitlines()[2:] == [
        "# Left out of this copy: the step. the site has no such part",
        "A=1",
        "",
        "last",
    ]
    for inside, last in (("three)", "esac"), ("one | two)", "done")):
        with pytest.raises(PlantError, match="some/file.sh: the step"):
            injects.edit(
                _context(text, specs), [], [injects.Cut("the step", inside, last, "")]
            )


# --- time: the internet's server, and the sensor ---


def test_an_outside_time_source_carries_the_address_that_the_fleet_gives_it(
    sets: dict[str, SiteSet], sources: SiteSources
) -> None:
    """phenix's ntp app takes an address as it is, and falls back to the
    default source for a hostname that the topology does not have. The
    address is the one that the WAN topology gives the internet's host."""
    wan_nodes = yaml.safe_load(
        (CONFIGS / "topology-wan.yml").read_text(encoding="utf-8")
    )["spec"]["nodes"]
    on_internet = [
        node["network"]["interfaces"][0]["address"]
        for node in wan_nodes
        if node["type"] == "VirtualMachine"
        and node["network"]["interfaces"][0]["vlan"] == "INET-LAN"
    ]
    assert (
        on_internet == [sites.internet_host_address(sources.wan)] == ["198.18.110.11"]
    )
    for code, made in sets.items():
        block = yaml.safe_load(made.files["phenix/ntp.yaml"])
        outside = {
            host["hostname"]: host["metadata"]["source"]
            for host in block["hosts"]
            if host["metadata"].get("source", {}).get("hostname") == "inet-traffic"
        }
        assert set(outside) == {f"{code}-corp-ws", f"{code}-fw-edge"}
        for source in outside.values():
            assert source == {
                "hostname": "inet-traffic",
                "interface": "IF0",
                "address": "198.18.110.11",
            }
        assert "address" not in block["metadata"]["defaultSource"]


def test_a_variant_keeps_its_time_sources_as_they_are() -> None:
    for name in ("ci", "full", "lite"):
        path = DEFAULT_PLANT.parent / "generated" / name / "phenix" / "ntp.yaml"
        assert "address" not in path.read_text(encoding="utf-8")


def test_an_outside_source_without_an_address_is_refused(
    plant_file: PlantFile, specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    expanded = fleet.expand_site(specs["fc"], plant_file, sources.wan)
    entry = expanded.spec.ntp.hosts["fc-corp-ws"]
    assert entry.source is not None and entry.source.address == "198.18.110.11"
    entry.source.address = None
    found = checks.ntp_hosts(expanded, allocate(expanded))
    assert found == [
        "ntp: fc-corp-ws names inet-traffic, which is outside the variant, with "
        "no address: phenix would give the host the default source"
    ]
    two = {"lans": [{"net": "198.18.110.0/24", "advertise_public": True}] * 2}
    with pytest.raises(PlantError, match="2 LANs with advertise_public"):
        sites.internet_host_address(two)


def test_chrony_serves_the_network_of_the_sensor(sets: dict[str, SiteSet]) -> None:
    """The sensor is no host of the plant. At `ep` it stands in the SOC zone,
    which no other client of ep-ot-svc is on."""
    nodes = yaml.safe_load(sets["ep"].topology)["spec"]["nodes"]
    leg = next(n for n in nodes if n["general"]["hostname"] == "ep-sensor")["network"][
        "interfaces"
    ][0]
    assert (leg["vlan"], leg["address"]) == ("EP-SOC", "10.42.10.60")
    hook = _copies(sets["ep"])["ot-svc/60-ot-svc.sh"]
    allowed = re.search(r"for net in (.*); do", hook)
    assert allowed is not None and "10.42.10.0/24" in allowed.group(1).split()
    # At `cs` the sensor stands in the plant network, which is served already.
    cs = re.search(r"for net in (.*); do", _copies(sets["cs"])["ot-svc/60-ot-svc.sh"])
    assert cs is not None
    assert cs.group(1).split() == ["192.168.20.0/24", "192.168.30.0/24"]


# --- the floor of a layout ---


def test_the_office_cannot_move_into_a_network_with_a_plc(
    plant_file: PlantFile, specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    """The reviewer's case: the pub's office in its plant network. A site may
    merge its networks, but not the office with a PLC's."""
    pub = specs["fc"]
    plant_move = pub.moves["CTRL"]
    merged = pub.model_copy(
        update={
            "moves": {
                **pub.moves,
                "CORP": plant_move.model_copy(update={"shift": 100}),
            }
        }
    )
    with pytest.raises(PlantError) as caught:
        fleet.site_plant(merged, plant_file, sources.wan)
    said = str(caught.value)
    assert "site fc: the office host corp-ws and the PLC plc-" in said
    assert "share the network PLANT" in said and "PLANS.md 4.1" in said
    for site in specs.values():
        fleet.site_plant(site, plant_file, sources.wan)


def test_the_office_network_needs_a_firewall(
    plant_file: PlantFile, specs: dict[str, sites.SiteSpec], sources: SiteSources
) -> None:
    pub = specs["cs"]
    office = pub.moves["CORP"].model_copy(update={"gateway": None})
    unrouted = pub.model_copy(update={"moves": {**pub.moves, "CORP": office}})
    with pytest.raises(
        PlantError, match="office network OFFICE of corp-ws has no firewall"
    ):
        fleet.site_plant(unrouted, plant_file, sources.wan)


# --- who may write BACnet, and the app's site key ---

#: The writers that the single site's detection content has by hand
#: (phenix-injects/malcolm): bas-fuxa and the engineering workstation.
SINGLE_SITE_WRITERS: list[str] = ["10.30.10.10", "10.20.30.12"]


def _writers(made: SiteSet) -> list[tuple[str, str]]:
    doc = json.loads(made.files[render.BACNET_WRITERS_FILE])
    return [(writer["host"], writer["address"]) for writer in doc["writers"]]


def test_the_bacnet_writers_follow_the_hosts_of_a_site(
    sets: dict[str, SiteSet],
) -> None:
    """Decision K4a. Where a site has a building front end, that host and the
    workstation write BACnet. At the pub, the one SCADA host operates the
    building too, so it is the writer, and no other host is."""
    assert {code: _writers(made) for code, made in sets.items()} == {
        "fr": [("fr-bas-fuxa", "10.30.10.10"), ("fr-ews", "10.20.30.12")],
        "ep": [("ep-bas-fuxa", "10.32.10.10"), ("ep-ews", "10.22.30.12")],
        "cs": [("cs-bas-fuxa", "192.168.30.10"), ("cs-ews", "192.168.20.42")],
        "fc": [("fc-scada-fuxa", "10.21.10.40")],
    }
    # A SCADA host of a site with a building front end only reads.
    for code in ("fr", "ep", "cs"):
        assert f"{code}-scada-fuxa" not in dict(_writers(sets[code]))


def test_the_writers_of_the_single_site_are_those_of_its_detection_content(
    plant_file: PlantFile,
) -> None:
    """The same rule, on the full variant, gives the two addresses that a
    person wrote into the Suricata rules and the Zeek script. The variants
    get no file: their detection content is the hand-written one."""
    full = expand(plant_file, "full")
    found = [writer["address"] for writer in render.bacnet_writers(full)]
    assert found == SINGLE_SITE_WRITERS
    rules = (INJECTS / "malcolm" / "cherrymountain.rules").read_text("utf-8")
    assert rules.count(f"alert udp ![{','.join(found)}] any -> any 47808") == 2
    zeek = (INJECTS / "malcolm" / "zeek" / "cherrymountain.zeek").read_text("utf-8")
    assert f"const bacnet_writers: set[addr] = {{ {', '.join(found)} }}" in zeek
    for name in ("ci", "full", "lite"):
        generated = DEFAULT_PLANT.parent / "generated" / name
        assert not (generated / render.BACNET_WRITERS_FILE).exists()


def test_the_detection_copies_of_the_soc_hold_the_sites_writers(
    sets: dict[str, SiteSet],
    plant_file: PlantFile,
    specs: dict[str, sites.SiteSpec],
    sources: SiteSources,
) -> None:
    """`fr` has the SOC. Its copies of the rules and the script get the
    writers from the list, which there are the hand-written two."""
    copies = _copies(sets["fr"])
    for path in ("malcolm/cherrymountain.rules", "malcolm/zeek/cherrymountain.zeek"):
        hand_written = (INJECTS / path).read_text(encoding="utf-8").splitlines()
        assert [ln for ln in copies[path].splitlines() if "Generated by" not in ln] == (
            hand_written
        ), path
    # The same renderers on the pub's hosts: the alert follows the site.
    pub = fleet.expand_site(specs["fc"], plant_file, sources.wan)

    def rendered(path: str) -> str:
        context = injects.Context(
            site=specs["fc"],
            plant=pub,
            nodes=[],
            path=path,
            targets=["fc-malcolm"],
            text=(INJECTS / path).read_text(encoding="utf-8"),
        )
        return injects.RENDERERS[path](context)

    zeek = rendered("malcolm/zeek/cherrymountain.zeek")
    assert "    const bacnet_writers: set[addr] = { 10.21.10.40 } &redef;\n" in zeek
    rules = rendered("malcolm/cherrymountain.rules")
    assert rules.count("alert udp ![10.21.10.40] any -> any 47808 (msg:") == 2
    assert "10.30.10.10" not in "".join(
        ln for ln in rules.splitlines() if "BACnet Write" in ln
    )


def test_the_app_block_of_a_site_names_the_site_and_a_site_has_no_helics_block(
    sets: dict[str, SiteSet],
) -> None:
    """Decision K3a. The cherrymountain app reads `site`: it then takes the
    site's files and names, and starts the site's own HELICS broker. So the
    scenario of a site has no helics app, which has one root broker."""
    for code, made in sets.items():
        block = yaml.safe_load(made.files["phenix/cherrymountain.yaml"])
        assert list(block["metadata"])[:2] == ["variant", "site"]
        assert block["metadata"]["variant"] == block["metadata"]["site"] == code
        assert "phenix/helics.yaml" not in made.files
        # The broker's host and address are still in the site's helics.json.
        broker = json.loads(made.files["helics.json"])["broker"]
        assert broker["host"] == f"{code}-sim-ctl"
    for name in ("ci", "full", "lite"):
        blocks = DEFAULT_PLANT.parent / "generated" / name / "phenix"
        block = yaml.safe_load((blocks / "cherrymountain.yaml").read_text("utf-8"))
        assert "site" not in block["metadata"]
    full = DEFAULT_PLANT.parent / "generated" / "full" / "phenix" / "helics.yaml"
    assert full.is_file()
