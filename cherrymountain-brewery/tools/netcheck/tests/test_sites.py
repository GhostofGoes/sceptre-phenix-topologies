"""netcheck on one site of the fleet: the committed site files, and copies
with one thing broken."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import ROOT, edit_yaml, node_of, ruleset, vrouter_host

from netcheck import checks, conduits, site, sitegen
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
def test_the_committed_scenario_is_what_the_generator_writes(
    code: str, derived: dict[str, site.Site]
) -> None:
    path: Path = ROOT / "phenix-configs" / sitegen.file_name(code)
    assert path.read_text(encoding="utf-8") == sitegen.render(derived[code])


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
    assert derived["fc"].reference.totals is None


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
    assert flows == {"fr": 143, "ep": 105, "cs": 38, "fc": 0}
    assert rules == {
        "fr": {"fr-fw-edge": 0, "fr-fw-ot": 87},
        "ep": {"ep-fw-edge": 0, "ep-fw-ot": 82},
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
