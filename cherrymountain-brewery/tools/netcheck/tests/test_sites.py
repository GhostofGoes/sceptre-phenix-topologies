"""netcheck on one site of the fleet: the committed site files, and copies
with one thing broken."""

from __future__ import annotations

import base64
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import ROOT, edit_yaml, node_of, ruleset, vrouter_host

from netcheck import checks, conduits, plan, site, sitegen
from netcheck import model as m
from netcheck.cli import main
from netcheck.sim import Net, flow

CODES: list[str] = ["fr", "ep", "cs", "fc"]


@pytest.fixture(scope="module")
def derived() -> dict[str, site.Site]:
    return {code: site.load(code) for code in CODES}


@pytest.fixture
def site_tree(tree: Path) -> Path:
    """The copy of the configs, with what a site's reference is derived from."""
    for rel in ("plant/plant.yaml", "plant/sites.yaml", "wan/wan.yaml"):
        (tree / rel).parent.mkdir(exist_ok=True)
        shutil.copy(ROOT / rel, tree / rel)
    return tree


def errors(findings: list[checks.Finding]) -> list[str]:
    return [f"[{f.check}] {f.message}" for f in findings if f.severity == "error"]


def run(tree: Path, code: str) -> list[str]:
    """The errors of a site's run, without the one that says the scenario was edited."""
    return [e for e in errors(checks.run_site(code, tree)) if "/generated]" not in e]


def has(found: list[str], *words: str) -> bool:
    return any(all(w in line for w in words) for line in found)


def scenario(tree: Path, code: str) -> Path:
    return tree / "phenix-configs" / f"scenario-site-{code}.yml"


def topology(tree: Path, code: str) -> Path:
    return tree / "phenix-configs" / f"topology-site-{code}.yml"


# --- the committed files -------------------------------------------------------


def test_sites_yaml_has_the_sites_that_this_file_tests() -> None:
    assert site.codes() == CODES


@pytest.mark.parametrize("code", CODES)
def test_committed_site_is_clean(code: str) -> None:
    assert errors(checks.run_site(code)) == []


@pytest.mark.parametrize("code", CODES)
def test_the_committed_scenario_holds_the_computed_apps(
    code: str, derived: dict[str, site.Site]
) -> None:
    """sync.py writes the file. Its vrouter app is the computed one, and its
    soh app holds every computed test."""
    computed: dict[str, dict[str, Any]] = sitegen.apps(derived[code])
    assert checks.check_generated(code, ROOT, computed) == []
    doc: dict[str, Any] = m.read_yaml(ROOT / "phenix-configs" / sitegen.file_name(code))
    apps: dict[str, Any] = {app["name"]: app for app in doc["spec"]["apps"]}
    assert apps["vrouter"] == computed["vrouter"]
    assert list(computed) == ["vrouter", "soh"]


def test_a_scenario_without_a_computed_test_is_reported(site_tree: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        soh: dict[str, Any] = next(a for a in doc["spec"]["apps"] if a["name"] == "soh")
        probes: list[dict[str, Any]] = soh["metadata"]["testCustomReachability"]
        hit: list[dict[str, Any]] = [p for p in probes if p["dst"].startswith("ep-plc-cellar|")]
        assert hit
        probes.remove(hit[0])

    edit_yaml(scenario(site_tree, "ep"), edit)
    found: list[str] = errors(checks.run_site("ep", site_tree))
    assert has(found, "ep/generated", "lacks the computed testCustomReachability", "sync.py")
    scenario(site_tree, "ep").unlink()
    assert has(errors(checks.run_site("ep", site_tree)), "ep/generated", "does not exist")


def test_an_edited_scenario_is_reported_as_stale(site_tree: Path) -> None:
    path: Path = scenario(site_tree, "ep")
    path.write_text(path.read_text().replace("default deny", "default drop", 1))
    assert has(errors(checks.run_site("ep", site_tree)), "ep/generated", "scenario-site-ep.yml")


def test_the_command_checks_one_site(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--site", "fc"]) == 0
    noted: str = capsys.readouterr().err
    assert "[fc/separation]" in noted and "[full/" not in noted
    assert main(["--site", "zz"]) == 2
    assert "no site 'zz'" in capsys.readouterr().err


# --- what a site's reference is derived from -----------------------------------


def test_a_site_has_the_nodes_of_the_plan_under_its_names(derived: dict[str, site.Site]) -> None:
    counts: dict[str, int] = {code: len(s.reference.nodes) for code, s in derived.items()}
    assert counts == {"fr": 39, "ep": 35, "cs": 26, "fc": 19}
    pub: dict[str, Any] = {n.name: n for n in derived["fc"].reference.nodes}
    plc = pub["fc-plc-cellar"]
    # The image and the size are the plan's. The address is the site's.
    assert (plc.image, plc.vcpus, plc.mem_gb) == ("cm-openplc.qc2", 1, 1)
    assert (plc.nics[0].vlan, plc.nics[0].ip, plc.nics[0].gateway) == (
        "FC-PLANT",
        "10.21.10.12",
        "10.21.10.1",
    )
    assert plc.zone == "plant" and plc.delay_c2 == ("fc-rio-cellar",)
    assert pub["fc-scada-fuxa"].delay_c2 == (
        "fc-plc-brewhouse",
        "fc-plc-cellar",
        "fc-plc-utilities",
    )
    assert pub["fc-sensor"].image == "cm-sensor.qc2"
    assert derived["fc"].reference.totals == (19, 27, 55)


def test_the_firewalls_of_a_conduit_follow_the_sites_networks(
    derived: dict[str, site.Site],
) -> None:
    """One row of the matrix, four layouts: SCADA reads the cellar PLC through
    the OT firewall behind two firewalls, and through none on a flat network."""

    def via(code: str, row: str, src: str, dst: str) -> tuple[str, ...]:
        (part,) = (
            cd
            for cd in derived[code].reference.conduits
            if cd.id == row and f"{code}-{src}" in cd.srcs and f"{code}-{dst}" in cd.dsts
        )
        assert part.enforced == bool(part.via)
        return part.via

    assert via("fr", "fuxa-modbus", "scada-fuxa", "plc-cellar") == ("fr-fw-ot",)
    assert via("ep", "fuxa-modbus", "scada-fuxa", "plc-cellar") == ("ep-fw-ot",)
    assert via("cs", "fuxa-modbus", "scada-fuxa", "plc-cellar") == ()
    assert via("fc", "fuxa-modbus", "scada-fuxa", "plc-cellar") == ()
    # The building is behind the one firewall at cs, and on the plant network at fc.
    assert via("cs", "fuxa-bacnet", "scada-fuxa", "bas-vent") == ("cs-fw-edge",)
    assert via("fc", "fuxa-bacnet", "scada-fuxa", "bas-vent") == ()
    # The peer read crosses the OT firewall where each cell has its network.
    assert via("fr", "plc-peer", "plc-utilities", "plc-cellar") == ()
    assert via("ep", "plc-peer", "plc-utilities", "plc-cellar") == ("ep-fw-ot",)
    # One row has two parts at cs: the building asks ot-svc through the firewall.
    parts = {cd.via for cd in derived["cs"].reference.conduits if cd.id == "ot-svc-services"}
    assert parts == {(), ("cs-fw-edge",)}


def test_a_row_with_an_end_outside_the_site_is_left_for_a_later_stage(
    derived: dict[str, site.Site],
) -> None:
    outside: tuple[str, ...] = (
        "corp-inet",
        "edge-ntp",
        "inet-wireguard",
        "vendor-jump",
        "ipsec",
        "dnp3",
    )
    for code in CODES:
        assert derived[code].left_out["outside"] == outside
        ids: set[str] = {cd.id for cd in derived[code].reference.conduits}
        assert not ids & set(outside)
        assert ids | set(outside) | set(derived[code].left_out["absent"]) == {
            cd.id for cd in conduits.CONDUITS
        }
    assert derived["fr"].left_out["absent"] == ()
    # The agents of ep report to the wazuh of fr: a conduit between two sites.
    assert {"ops-wazuh", "dmz-wazuh", "syslog-relay"} <= set(derived["ep"].left_out["absent"])


def test_the_counts_of_each_site(derived: dict[str, site.Site]) -> None:
    """Flows that cross a firewall, and the rules that carry them."""
    flows: dict[str, int] = {c: len(s.reference.expected_flows()) for c, s in derived.items()}
    rules: dict[str, dict[str, int]] = {c: sitegen.rule_counts(s) for c, s in derived.items()}
    # `ep` has four more than its hosts of the Front Range: the sensor's time,
    # names (UDP and TCP), and syslog at ep-ot-svc.
    assert flows == {"fr": 143, "ep": 109, "cs": 38, "fc": 0}
    assert rules == {
        "fr": {"fr-fw-edge": 0, "fr-fw-ot": 87},
        "ep": {"ep-fw-edge": 0, "ep-fw-ot": 86},
        "cs": {"cs-fw-edge": 22},
        "fc": {"fc-fw-edge": 0},
    }


def test_every_site_flow_is_traced_both_ways() -> None:
    for code in ("fr", "ep", "cs"):
        derived: site.Site = site.load(code)
        net: Net = Net(m.load_site(code).topology)
        seen: int = 0
        for cd in derived.reference.conduits:
            if not cd.enforced:
                continue
            for src, dst, proto, port in derived.reference.expand(cd):
                fwd, rev = flow(net, src, dst, proto, port)
                assert fwd.result == "allowed", (cd.id, src, dst, fwd)
                assert set(fwd.firewalls) == set(cd.via), (cd.id, src, dst)
                assert rev is not None and rev.result in ("allowed", "local"), (cd.id, rev)
                seen += 1
        assert seen == len(derived.reference.expected_flows()) > 30


# --- what no firewall separates ------------------------------------------------


def test_a_flat_network_is_reported_and_is_no_error(derived: dict[str, site.Site]) -> None:
    """Hosts on one network reach each other with no firewall. A site's run
    says which conduit rows and how many host pairs that is."""
    pairs: dict[str, int] = {code: s.same_network_pairs for code, s in derived.items()}
    assert pairs == {"fr": 34, "ep": 17, "cs": 65, "fc": 45}
    assert derived["fr"].flat_rows == derived["ep"].flat_rows == ()
    assert derived["fc"].flat_rows == (
        "hmi-fuxa",
        "hmi-historian",
        "fuxa-opcua",
        "fuxa-modbus",
        "fuxa-bacnet",
        "bbmd-2",
    )
    assert "ews-plc" in derived["cs"].flat_rows
    open_paths: set[tuple[str, str, int]] = {
        (p.src, p.dst, p.port) for p in derived["fc"].open_paths
    }
    assert ("fc-hmi-brewhouse", "fc-plc-cellar", 502) in open_paths
    assert len(open_paths) == 6 and len(derived["cs"].open_paths) == 8
    assert not derived["ep"].open_paths
    findings: list[checks.Finding] = checks.run_site("fc")
    notes: list[str] = [f.message for f in findings if f.check == "fc/separation"]
    assert all(f.severity == "note" for f in findings)
    assert any("45 pairs of hosts share a zone network" in n for n in notes)
    assert any("fuxa-modbus" in n and "Front Range layout puts a firewall" in n for n in notes)
    assert any("fc-hmi-brewhouse -> fc-plc-cellar tcp/502" in n for n in notes)
    skipped: str = next(f.message for f in findings if f.check == "fc/skipped")
    assert all(name in skipped for name in checks.SITE_SKIPS)
    # A path that the pub leaves open is not among its blocked-path tests.
    blocked: set[tuple[str, str, int]] = {
        (b.src, b.dst, b.port) for b in derived["fc"].reference.blocked
    }
    assert not blocked & open_paths
    assert ("fc-scada-fuxa", "fc-rio-cellar", 502) in blocked


# --- broken copies: the site checks can fail -----------------------------------


def workflow_file(tree: Path, code: str) -> Path:
    return tree / f"phenix-site-{code}.yml"


def test_a_sites_vlan_ids_and_bridge_come_from_this_tools_own_plan(
    derived: dict[str, site.Site],
) -> None:
    """The ID rule is plantgen's. The single-site IDs under it are netcheck's
    plan, so a workflow that follows another base is found."""
    ep = derived["ep"].reference
    assert ep.vlans["EP-OPS"].vid == 300 + plan.VLANS["OPS"].vid % 100 == 306
    assert (ep.vlans["EP-CTRL-BH"].vid, ep.vlans["WAN-TW-EP"].vid) == (320, 504)
    assert derived["fc"].reference.vlans["FC-PLANT"].vid == 221
    assert {code: s.reference.bridge for code, s in derived.items()} == {
        "fr": "cherrymtn-fr",
        "ep": "cherrymtn-ep",
        "cs": "cherrymtn-cs",
        "fc": "cherrymtn-fc",
    }
    assert "MIRROR" not in ep.vlans


def test_a_wrong_vlan_id_in_a_sites_workflow_is_found(site_tree: Path) -> None:
    edit_yaml(workflow_file(site_tree, "ep"), lambda d: d["spec"]["vlans"].update({"EP-OPS": 307}))
    found: list[str] = run(site_tree, "ep")
    assert has(found, "ep/workflow", "VLAN EP-OPS is 307 in phenix-site-ep.yml, plan says 306")
    assert has(found, "ep/workflow", "two VLANs share an id")


def test_an_unpinned_or_unknown_vlan_in_a_sites_workflow_is_found(site_tree: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        del doc["spec"]["vlans"]["FC-PLANT"]
        doc["spec"]["vlans"]["FC-NEW"] = 250
        doc["spec"]["vlans"]["FC-OFFICE"] = 600

    edit_yaml(workflow_file(site_tree, "fc"), edit)
    found: list[str] = run(site_tree, "fc")
    assert has(found, "fc/workflow", "VLAN FC-PLANT is used but not pinned in phenix-site-fc.yml")
    assert has(found, "fc/workflow", "VLAN FC-NEW is pinned but not in the plan")
    assert has(found, "fc/workflow", "VLAN FC-OFFICE id 600 is outside vlanRange 100-599")


def test_a_site_needs_its_own_bridge(site_tree: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        doc["spec"]["defaultBridge"] = "cherrymtn"

    edit_yaml(workflow_file(site_tree, "fc"), edit)
    assert has(run(site_tree, "fc"), "fc/workflow", "defaultBridge must be cherrymtn-fc")
    workflow_file(site_tree, "cs").unlink()
    assert has(errors(checks.run_site("cs", site_tree)), "cs/load", "phenix-site-cs.yml")


def _app(doc: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = next(a for a in doc["spec"]["apps"] if a["name"] == name)
    return found


def test_the_ntp_app_of_a_site_lists_every_node(site_tree: Path) -> None:
    """The sensor is a node that the plant does not describe. The ntp app runs
    in app mode, so its block must list the sensor too."""

    def edit(doc: dict[str, Any]) -> None:
        ntp: dict[str, Any] = _app(doc, "ntp")
        ntp["hosts"] = [h for h in ntp["hosts"] if h["hostname"] != "fc-sensor"]

    edit_yaml(scenario(site_tree, "fc"), edit)
    assert has(run(site_tree, "fc"), "fc/apps", "ntp app mode needs every host: fc-sensor")


def test_a_time_source_outside_the_site_is_a_note_and_an_unknown_one_is_an_error(
    site_tree: Path,
) -> None:
    findings: list[checks.Finding] = checks.run_site("fc", site_tree)
    notes: list[str] = [f.message for f in findings if f.check == "fc/apps"]
    assert len(notes) == 2 and all(f.severity == "note" for f in findings)
    assert all("follows inet-traffic at 198.18.110.11, a host of the internet" in n for n in notes)
    assert {n.split()[1] for n in notes} == {"fc-corp-ws", "fc-fw-edge"}

    def edit(doc: dict[str, Any]) -> None:
        for host in _app(doc, "ntp")["hosts"]:
            if host["hostname"] == "fc-corp-ws":
                host["metadata"]["source"]["hostname"] = "fc-nowhere"

    edit_yaml(scenario(site_tree, "fc"), edit)
    assert has(run(site_tree, "fc"), "fc/apps", "fc-corp-ws source", "fc-nowhere", "is not a")


def test_an_app_of_a_site_names_only_nodes_of_the_site(site_tree: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        _app(doc, "cherrymountain")["hosts"][0]["hostname"] = "scada-fuxa"
        doc["spec"]["apps"] = [a for a in doc["spec"]["apps"] if a["name"] != "startup"]

    edit_yaml(scenario(site_tree, "ep"), edit)
    found: list[str] = run(site_tree, "ep")
    assert has(found, "ep/apps", "app cherrymountain: host scada-fuxa is not in the topology")
    assert has(found, "ep/apps", "scenario has no startup app")


def test_a_sites_zone_file_is_checked_against_its_nodes(site_tree: Path) -> None:
    zone: Path = site_tree / "phenix-injects" / "fleet" / "ep" / "ot-svc" / "plant.hosts"
    text: str = zone.read_text(encoding="utf-8")
    assert "10.22.42.12 ep-plc-cellar\n" in text
    zone.write_text(text.replace("10.22.42.12 ep-plc-cellar\n", "10.20.10.12 ep-plc-cellar\n"))
    found: list[str] = run(site_tree, "ep")
    assert has(found, "ep/services", "ep-plc-cellar should be 10.22.42.12, found 10.20.10.12")
    zone.unlink()
    found = run(site_tree, "ep")
    assert has(found, "ep/injects", "missing phenix-injects/fleet/ep/ot-svc/plant.hosts")
    notes: list[str] = [f.message for f in checks.run_site("fc") if f.check == "fc/services"]
    assert notes == ["no node of the site injects a plant.hosts (it has no ot-svc)"]


def test_a_site_run_leaves_out_three_checks() -> None:
    assert set(checks.SITE_SKIPS) == {"app-hosts", "nat-ipsec", "scorch"}


def test_netcheck_writes_no_scenario(capsys: pytest.CaptureFixture[str]) -> None:
    """sync.py owns scenario-site-<code>.yml. netcheck computes two of its
    apps and checks the file."""
    with pytest.raises(SystemExit):
        main(["--write-sites"])
    assert "unrecognized arguments: --write-sites" in capsys.readouterr().err
    assert not hasattr(sitegen, "write") and not hasattr(sitegen, "render")


def _drop_rule(description: str, router: str) -> Any:
    def edit(doc: dict[str, Any]) -> None:
        hit: int = 0
        for rs in vrouter_host(doc, router)["acl"]["rulesets"]:
            keep = [r for r in rs["rules"] if r["description"] != description]
            hit += len(rs["rules"]) - len(keep)
            rs["rules"] = keep
        assert hit == 1, description

    return edit


@pytest.mark.parametrize(
    ("code", "router", "description", "named"),
    [
        (
            "ep",
            "ep-fw-ot",
            "plc-peer: ep-plc-utilities to ep-plc-cellar tcp/502",
            "(plc-peer)",
        ),
        (
            "ep",
            "ep-fw-ot",
            "fuxa-modbus: ep-scada-fuxa to ep-plc-cellar tcp/502",
            "(fuxa-modbus)",
        ),
        (
            "cs",
            "cs-fw-edge",
            "fuxa-bacnet: cs-scada-fuxa to cs-bas-vent udp/47808",
            "(fuxa-bacnet)",
        ),
    ],
)
def test_a_removed_rule_is_a_conduit_that_is_not_delivered(
    site_tree: Path, code: str, router: str, description: str, named: str
) -> None:
    edit_yaml(scenario(site_tree, code), _drop_rule(description, router))
    found: list[str] = run(site_tree, code)
    assert has(found, f"{code}/flows", named, "not delivered", router)
    if "tcp" in description:
        assert has(found, f"{code}/soh", "is blocked by the firewall rules")


def test_the_pub_has_no_rule_to_remove_and_an_open_firewall_is_found(site_tree: Path) -> None:
    """No conduit inside fc crosses its firewall, so its rulesets accept
    nothing new. What the check must find there is a firewall that lets the
    office through to the plant network."""
    doc_rules: int = sum(sitegen.rule_counts(site.load("fc")).values())
    assert doc_rules == 0

    def edit(doc: dict[str, Any]) -> None:
        ruleset(doc, "fc-fw-edge", "FROM-FC-OFFICE")["default"] = "accept"

    edit_yaml(scenario(site_tree, "fc"), edit)
    found: list[str] = run(site_tree, "fc")
    assert has(found, "fc/rulesets", "FROM-FC-OFFICE", "must be drop")
    assert has(found, "fc/flows", "fc-corp-ws -> fc-plc-cellar tcp/502", "no conduit covers it")


@pytest.mark.parametrize(
    ("code", "router", "name", "src", "dst"),
    [
        ("ep", "ep-fw-ot", "FROM-EP-HMI", "10.22.20.10", "10.22.42.12"),
        ("fc", "fc-fw-edge", "FROM-FC-OFFICE", "10.11.10.10", "10.21.10.12"),
    ],
)
def test_an_extra_rule_is_found(
    site_tree: Path, code: str, router: str, name: str, src: str, dst: str
) -> None:
    def edit(doc: dict[str, Any]) -> None:
        ruleset(doc, router, name)["rules"].append(
            {
                "id": 9000,
                "action": "accept",
                "description": "A way to the cellar PLC that no conduit has",
                "protocol": "tcp",
                "source": {"address": src},
                "destination": {"address": dst, "port": 502},
            }
        )

    edit_yaml(scenario(site_tree, code), edit)
    found: list[str] = run(site_tree, code)
    assert has(found, f"{code}/rule-coverage", router, name, "rule 9000", "matches no conduit flow")
    assert has(found, f"{code}/flows", f"{code}-plc-cellar tcp/502", "no conduit covers it")
    assert has(found, f"{code}/soh", "is not blocked")


@pytest.mark.parametrize(
    ("code", "host", "wrong", "right"),
    [
        ("ep", "ep-plc-cellar", "10.22.42.99", "10.22.42.12"),
        ("fc", "fc-historian", "10.21.10.12", "10.21.10.41"),
    ],
)
def test_a_host_at_a_wrong_address_is_found(
    site_tree: Path, code: str, host: str, wrong: str, right: str
) -> None:
    def edit(doc: dict[str, Any]) -> None:
        leg: dict[str, Any] = node_of(doc, host)["network"]["interfaces"][0]
        assert leg["address"] == right
        leg["address"] = wrong

    edit_yaml(topology(site_tree, code), edit)
    found: list[str] = run(site_tree, code)
    assert has(found, f"{code}/plan", host, "interfaces", wrong)
    if code == "fc":
        # The wrong address is the cellar PLC's.
        assert has(found, "fc/addresses", wrong, "on both")
    else:
        # The rule still names the right address: the conduit is not delivered.
        assert has(found, "ep/flows", "(fuxa-modbus)", "not delivered")


def test_a_site_host_on_a_network_of_another_site_is_found(site_tree: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        leg: dict[str, Any] = node_of(doc, "fc-corp-ws")["network"]["interfaces"][0]
        leg["address"], leg["gateway"] = "10.10.10.10", "10.10.10.1"

    edit_yaml(topology(site_tree, "fc"), edit)
    found: list[str] = run(site_tree, "fc")
    assert has(found, "fc/addresses", "fc-corp-ws", "is outside 10.11.10.0/24")
    assert has(found, "fc/addresses", "gateway 10.10.10.1 is off-link") or has(
        found, "fc/addresses", "gateway 10.10.10.1 is not a router address"
    )


def test_a_site_without_a_scenario_is_an_error(site_tree: Path) -> None:
    scenario(site_tree, "ep").unlink()
    found: list[str] = errors(checks.run_site("ep", site_tree))
    assert has(found, "ep/generated") and has(found, "ep/load", "no scenario named")


def test_a_site_needs_its_description(tree: Path) -> None:
    """Without plant/sites.yaml in the tree, a site has nothing to be checked against."""
    assert has(errors(checks.run_site("ep", tree)), "ep/load")
    assert main(["--root", str(tree), "--variant", "ci"]) == 0


#: A sites.yaml that is not YAML, and one that is not a list of sites.
BAD_SITES: dict[str, str] = {
    "not-yaml": "sites: [\n",
    "not-sites": "sites:\n- code: fc\n  index: 99\n  colour: red\n",
}


@pytest.mark.parametrize("bad", list(BAD_SITES.values()), ids=list(BAD_SITES))
def test_a_variant_run_does_not_read_sites_yaml_and_a_bad_file_is_one_line(
    site_tree: Path, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    (site_tree / "plant" / "sites.yaml").write_text(bad, encoding="utf-8")
    assert main(["--root", str(site_tree), "--variant", "ci", "-q"]) == 0
    assert "sites" not in capsys.readouterr().err
    assert main(["--root", str(site_tree), "-q"]) == 1
    said: list[str] = capsys.readouterr().err.splitlines()
    assert len(said) == 2 and said[1] == "netcheck: 1 error(s)", said
    assert said[0].startswith("ERROR [sites/load] plant/sites.yaml: ")
    assert main(["--root", str(site_tree), "--site", "fc", "-q"]) == 1
    assert capsys.readouterr().err.splitlines()[0].startswith("ERROR [sites/load] ")


def test_the_fleet_has_no_vlan_id_twice(site_tree: Path) -> None:
    """The four site workflows and phenix-wan.yml are one set of IDs. A site
    with an ID of the internet's own links, or of another site, is found."""
    assert errors(checks.check_fleet_vlans(ROOT, CODES)) == []

    def edit(doc: dict[str, Any]) -> None:
        doc["spec"]["vlans"]["CS-BAS"] = 510
        doc["spec"]["vlans"]["CS-OFFICE"] = 306
        doc["spec"]["vlans"]["WAN-BC-CS"] = 600

    edit_yaml(workflow_file(site_tree, "cs"), edit)
    found: list[str] = errors(checks.check_fleet_vlans(site_tree, CODES))
    assert has(found, "fleet/vlan-ids", "the ID 510 is CS-BAS", "INET-CBW-CBS in phenix-wan.yml")
    assert has(found, "fleet/vlan-ids", "the ID 306 is EP-OPS", "CS-OFFICE in phenix-site-cs.yml")
    assert has(found, "fleet/vlan-ids", "WAN-BC-CS is 600 in phenix-site-cs.yml, outside")
    assert has(found, "fleet/vlan-ids", "WAN-BC-CS is 600", "and 506 in phenix-wan.yml")
    # The command runs the check with the sites.
    assert main(["--root", str(site_tree), "--site", "fc", "-q"]) == 1


# --- the servers that a node's files name ---------------------------------------


def test_the_sensor_reaches_its_time_server_through_the_rules(
    derived: dict[str, site.Site],
) -> None:
    """The sensor is a host that only a site has. It is a device of the SOC, so
    the row from `zone:SOC` to ot-svc carries it, and at `ep` that row crosses
    the OT firewall."""
    pairs: dict[str, list[tuple[str, str, str, tuple[str, ...]]]] = {
        code: [
            (p.conduit, p.src, p.dst, tuple(router for router, _ in p.hops))
            for p in s.pairs
            if p.src.endswith("-sensor") or p.dst.endswith("-sensor")
        ]
        for code, s in derived.items()
    }
    assert pairs == {
        "fr": [],
        "ep": [("ot-svc-services", "ep-sensor", "ep-ot-svc", ("ep-fw-ot",))],
        # One network holds both: no firewall, and no rule.
        "cs": [("ot-svc-services", "cs-sensor", "cs-ot-svc", ())],
        # The pub has no ot-svc. Its sensor follows fc-scada-fuxa, on its own network.
        "fc": [],
    }
    net = Net(m.load_site("ep").topology)
    for proto, port in (("udp", 123), ("udp", 53), ("tcp", 53), ("udp", 514)):
        fwd, rev = flow(net, "ep-sensor", "ep-ot-svc", proto, port)
        assert fwd.result == "allowed" and rev is not None and rev.result == "allowed"
    # The sensor is no HMI and no PLC: nothing else opens for it.
    assert flow(net, "ep-sensor", "ep-scada-fuxa", "tcp", 1881)[0].result == "dropped"
    assert flow(net, "ep-sensor", "ep-plc-cellar", "tcp", 502)[0].result == "dropped"


def test_a_node_that_cannot_reach_its_time_or_name_server_is_found(site_tree: Path) -> None:
    """Before the sensor had its rules, the run of `ep` passed. Now the run
    fails when a node's time, name, or syslog server is behind a rule that
    drops the node."""

    def no_rules_for_the_sensor(doc: dict[str, Any]) -> None:
        rules: dict[str, Any] = ruleset(doc, "ep-fw-ot", "FROM-EP-SOC")
        rules["rules"] = [r for r in rules["rules"] if "ot-svc-services" not in r["description"]]

    edit_yaml(scenario(site_tree, "ep"), no_rules_for_the_sensor)
    found: list[str] = run(site_tree, "ep")
    assert has(found, "ep/servers", "ep-sensor: its time server ep-ot-svc (10.22.30.13)")
    assert has(found, "ep/servers", "udp/123 is dropped", "FROM-EP-SOC")
    assert has(found, "ep/flows", "ep-sensor -> ep-ot-svc udp/123", "not delivered")


def test_a_name_server_behind_a_closed_rule_is_found(site_tree: Path) -> None:
    def no_dns_from_the_cellar(doc: dict[str, Any]) -> None:
        rules: dict[str, Any] = ruleset(doc, "ep-fw-ot", "FROM-EP-CTRL-CEL")
        rules["rules"] = [
            r for r in rules["rules"] if r.get("destination", {}).get("port") != 53
        ]

    edit_yaml(scenario(site_tree, "ep"), no_dns_from_the_cellar)
    found: list[str] = run(site_tree, "ep")
    assert has(found, "ep/servers", "ep-plc-cellar: its name server ep-ot-svc", "udp/53 is dropped")


def test_a_server_outside_the_site_is_a_note(site_tree: Path) -> None:
    """The fleet has one Wazuh and one time server on the internet. At a site
    alone nothing answers at those addresses, and the run says who asks."""
    notes: list[str] = [
        f.message for f in checks.run_site("ep", site_tree) if f.check == "ep/servers"
    ]
    assert any(
        n.startswith("3 node(s) name 10.40.10.11 as syslog server")
        and n.endswith("ep-fw-edge, ep-fw-ot, ep-ot-svc")
        for n in notes
    )
    assert any(
        n.startswith("2 node(s) name 198.18.110.11 as time server")
        and n.endswith("ep-corp-ws, ep-fw-edge")
        for n in notes
    )
    pub: list[str] = [f.message for f in checks.run_site("fc") if f.check == "fc/servers"]
    assert any("name the router fc-fw-edge (10.21.10.1) as name server" in n for n in pub)


def test_an_outside_time_source_needs_the_address_that_the_fleet_gives_it(
    site_tree: Path,
) -> None:
    """phenix gives a client the default source when it cannot find the source
    by its hostname. So a source outside the site carries its address."""
    assert checks.internet_servers(ROOT) == {"inet-traffic": "198.18.110.11"}

    def edit(address: str | None) -> Any:
        def change(doc: dict[str, Any]) -> None:
            for host in _app(doc, "ntp")["hosts"]:
                if host["hostname"] == "fc-corp-ws":
                    if address is None:
                        del host["metadata"]["source"]["address"]
                    else:
                        host["metadata"]["source"]["address"] = address

        return change

    edit_yaml(scenario(site_tree, "fc"), edit(None))
    found: list[str] = run(site_tree, "fc")
    assert has(found, "fc/apps", "fc-corp-ws names inet-traffic", "without its address")
    # Without the address, phenix gives the office PC the plant's server.
    assert has(found, "fc/servers", "fc-corp-ws: its time server fc-scada-fuxa", "dropped")
    # The single site's address of inet-traffic is the uplink of the pub in the fleet.
    edit_yaml(scenario(site_tree, "fc"), edit("203.0.113.10"))
    assert has(run(site_tree, "fc"), "fc/apps", "without its address 198.18.110.11")


@pytest.mark.parametrize("plain", ["sim-ctl", "ot-svc", "scada-fuxa"])
def test_a_time_source_with_the_plain_name_of_a_site_host_is_an_error(
    site_tree: Path, plain: str
) -> None:
    """Only a server of the internet may be outside the topology. The plain
    name of a host that the site has under its own name is a mistake."""

    def edit(doc: dict[str, Any]) -> None:
        for host in _app(doc, "ntp")["hosts"]:
            if host["hostname"] == "ep-historian":
                host["metadata"]["source"] = {"hostname": plain, "interface": "IF0"}

    edit_yaml(scenario(site_tree, "ep"), edit)
    found: list[str] = run(site_tree, "ep")
    assert has(found, "ep/apps", "ep-historian source", plain, "is not a topology host")


def test_a_site_that_injects_the_front_range_zone_file_is_an_error(site_tree: Path) -> None:
    path: Path = topology(site_tree, "ep")
    text: str = path.read_text(encoding="utf-8")
    own: str = "}/fleet/ep/ot-svc/plant.hosts"
    assert text.count(own) == 1
    path.write_text(text.replace(own, "}/ot-svc/plant.hosts"), encoding="utf-8")
    findings: list[checks.Finding] = checks.run_site("ep", site_tree)
    found: list[str] = errors(findings)
    assert has(found, "ep/services", "ep-ot-svc injects", "}/ot-svc/plant.hosts")
    assert has(found, "ep/services", "not the zone file of this site")
    assert not [f for f in findings if f.check == "ep/services" and f.severity == "note"]


def test_a_dns_probe_asks_for_a_name_of_the_sites_zone() -> None:
    """The zone of a site has `<code>-ot-svc`, not `ot-svc`."""
    asked: dict[str, set[str]] = {}
    for code in CODES:
        doc: dict[str, Any] = m.read_yaml(ROOT / "phenix-configs" / sitegen.file_name(code))
        soh: dict[str, Any] = _app(doc, "soh")["metadata"]
        zone: Path = ROOT / "phenix-injects" / "fleet" / code / "ot-svc" / "plant.hosts"
        names: set[str] = (
            {line.split()[1] for line in zone.read_text().splitlines() if line[:1].isdigit()}
            if zone.is_file()
            else set()
        )
        for probe in soh["testCustomReachability"]:
            if probe["proto"] == "udp" and probe["port"] == 53:
                query: bytes = base64.b64decode(probe["udpPacketBase64"])
                labels: list[str] = []
                at: int = 12
                while query[at]:
                    labels.append(query[at + 1 : at + 1 + query[at]].decode())
                    at += 1 + query[at]
                assert labels[1:] == ["cherrymountain", "test"]
                assert labels[0] in names, (code, labels)
                asked.setdefault(code, set()).add(labels[0])
    assert asked == {"fr": {"fr-ot-svc"}, "ep": {"ep-ot-svc"}, "cs": {"cs-ot-svc"}}


# --- rules that a person wrote down, not the generator --------------------------

#: (site, protocol, destination, port) -> every rule that lets a packet through
#: to it: (firewall, ruleset, source). A person worked these out from the
#: conduit matrix and the plan of the addresses (PLANS.md, section 3, and
#: plant/sites.yaml), without the generator.
Opening = tuple[str, str, str]
EXPECTED_RULES: dict[tuple[str, str, str, int], list[Opening]] = {
    # jump-ews: only the site's jump host reaches the workstation, RDP and SSH.
    ("ep", "tcp", "10.22.30.12", 3389): [("ep-fw-ot", "FROM-EP-DMZ", "10.12.20.11")],
    ("ep", "tcp", "10.22.30.12", 22): [("ep-fw-ot", "FROM-EP-DMZ", "10.12.20.11")],
    # Modbus at the cellar PLC: the SCADA (fuxa-modbus), the workstation
    # (ews-plc), and the utilities PLC from its own cell network (plc-peer).
    ("ep", "tcp", "10.22.42.12", 502): [
        ("ep-fw-ot", "FROM-EP-OPS", "10.22.30.10"),
        ("ep-fw-ot", "FROM-EP-OPS", "10.22.30.12"),
        ("ep-fw-ot", "FROM-EP-CTRL-UTL", "10.22.44.14"),
    ],
    # Time at ep-ot-svc: the DMZ and each routed OT zone, the SOC zone with
    # the sensor among them, each as its own network.
    ("ep", "udp", "10.22.30.13", 123): [
        ("ep-fw-ot", "FROM-EP-DMZ", "10.12.20.0/24"),
        ("ep-fw-ot", "FROM-EP-HMI", "10.22.20.0/24"),
        ("ep-fw-ot", "FROM-EP-DERLINK", "10.22.15.0/24"),
        ("ep-fw-ot", "FROM-EP-BAS", "10.32.10.0/24"),
        ("ep-fw-ot", "FROM-EP-SOC", "10.42.10.0/24"),
        ("ep-fw-ot", "FROM-EP-CTRL-BH", "10.22.41.0/24"),
        ("ep-fw-ot", "FROM-EP-CTRL-CEL", "10.22.42.0/24"),
        ("ep-fw-ot", "FROM-EP-CTRL-PKG", "10.22.43.0/24"),
        ("ep-fw-ot", "FROM-EP-CTRL-UTL", "10.22.44.0/24"),
        ("ep-fw-ot", "FROM-EP-CTRL-PWR", "10.22.46.0/24"),
    ],
    # BACnet at a building controller: the SCADA and the workstation.
    ("cs", "udp", "192.168.30.22", 47808): [
        ("cs-fw-edge", "FROM-CS-PLANT", "192.168.20.40"),
        ("cs-fw-edge", "FROM-CS-PLANT", "192.168.20.42"),
    ],
    # The building's FUXA: the two HMIs and the workstation.
    ("cs", "tcp", "192.168.30.10", 1881): [
        ("cs-fw-edge", "FROM-CS-PLANT", "192.168.20.30"),
        ("cs-fw-edge", "FROM-CS-PLANT", "192.168.20.31"),
        ("cs-fw-edge", "FROM-CS-PLANT", "192.168.20.42"),
    ],
    # bas-historian: the building's FUXA writes to the historian.
    ("cs", "tcp", "192.168.20.41", 8086): [("cs-fw-edge", "FROM-CS-BAS", "192.168.30.10")],
}


@pytest.mark.parametrize(
    "key", list(EXPECTED_RULES), ids=[f"{k[0]}-{k[2]}-{k[1]}-{k[3]}" for k in EXPECTED_RULES]
)
def test_a_destination_has_exactly_the_rules_that_a_person_expects(
    key: tuple[str, str, str, int],
) -> None:
    """`generated` compares the generator with itself. This table does not: a
    rule with a wider source, a rule on another firewall, or one rule more for
    the same destination fails here."""
    code, proto, dst, port = key
    doc: dict[str, Any] = m.read_yaml(ROOT / "phenix-configs" / sitegen.file_name(code))
    accepts: list[Opening] = [
        (host["hostname"], rules["name"], rule.get("source", {}).get("address", "any"))
        for host in _app(doc, "vrouter")["hosts"]
        for rules in host["metadata"]["acl"]["rulesets"]
        for rule in rules["rules"]
        if rule["action"] == "accept"
        and rule.get("protocol") == proto
        and rule.get("destination", {}).get("port") == port
        and rule.get("destination", {}).get("address") == dst
    ]
    assert sorted(accepts) == sorted(EXPECTED_RULES[key])


def test_no_rule_accepts_a_destination_wider_than_one_host() -> None:
    """Every generated rule names one destination host and one port."""
    for code in CODES:
        doc: dict[str, Any] = m.read_yaml(ROOT / "phenix-configs" / sitegen.file_name(code))
        for host in _app(doc, "vrouter")["hosts"]:
            for rules in host["metadata"]["acl"]["rulesets"]:
                for rule in rules["rules"]:
                    if rule.get("description", "").startswith("Established"):
                        continue
                    target: dict[str, Any] = rule["destination"]
                    assert "/" not in target["address"] and isinstance(target["port"], int)
                    assert rule["source"]["address"]


def test_the_rulesets_drop_by_default_whatever_the_table_says() -> None:
    for code in CODES:
        doc: dict[str, Any] = m.read_yaml(ROOT / "phenix-configs" / sitegen.file_name(code))
        for host in _app(doc, "vrouter")["hosts"]:
            for rules in host["metadata"]["acl"]["rulesets"]:
                assert rules["default"] == "drop", (host["hostname"], rules["name"])


# --- the floor of a layout, and the size of a site ------------------------------


def test_every_site_keeps_the_office_apart_from_the_plcs(derived: dict[str, site.Site]) -> None:
    assert {code: (s.office, len(s.plcs)) for code, s in derived.items()} == {
        "fr": (("fr-corp-ws",), 5),
        "ep": (("ep-corp-ws",), 5),
        "cs": (("cs-corp-ws",), 4),
        "fc": (("fc-corp-ws",), 3),
    }
    for code, s in derived.items():
        assert checks.check_layout(m.load_site(code), s.office, s.plcs) == []


def test_an_office_pc_in_the_plant_network_is_found(site_tree: Path) -> None:
    """The reviewer's case: the pub's office moves into the plant network."""

    def edit(doc: dict[str, Any]) -> None:
        leg: dict[str, Any] = node_of(doc, "fc-corp-ws")["network"]["interfaces"][0]
        leg.update({"vlan": "FC-PLANT", "address": "10.21.10.200", "gateway": "10.21.10.1"})

    edit_yaml(topology(site_tree, "fc"), edit)
    found: list[str] = run(site_tree, "fc")
    assert has(found, "fc/layout", "the office host fc-corp-ws and the PLC fc-plc-cellar share")


def test_an_open_firewall_between_the_office_and_a_plc_is_found(site_tree: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        ruleset(doc, "fc-fw-edge", "FROM-FC-OFFICE")["default"] = "accept"

    edit_yaml(scenario(site_tree, "fc"), edit)
    found: list[str] = run(site_tree, "fc")
    assert has(found, "fc/layout", "no firewall stops fc-corp-ws on its way to the PLC")


def test_the_totals_of_a_site_are_those_of_the_plan(site_tree: Path) -> None:
    """PLANS.md 4.1, "The size of the range": VMs, vCPUs, and GB with the sensor."""
    assert site.TOTALS == {
        "fr": (39, 62, 151),
        "ep": (35, 47, 82),
        "cs": (26, 37, 69),
        "fc": (19, 27, 55),
    }

    def edit(doc: dict[str, Any]) -> None:
        node_of(doc, "fc-historian")["hardware"]["memory"] += 2048

    edit_yaml(topology(site_tree, "fc"), edit)
    assert has(run(site_tree, "fc"), "fc/plan", "totals", "(19, 27, 57) differ from PLANS.md 4.1")
