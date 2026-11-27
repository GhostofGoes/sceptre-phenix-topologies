"""The conduits between two sites: a sensor's three paths to and from Malcolm
(docs/multisite/PLANS.md 4.6), as rules of each site's own firewalls."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import ROOT, edit_yaml, ruleset

from netcheck import checks, fleet, site, sitegen

CODES: list[str] = ["fr", "ep", "cs", "fc"]
SENSOR_SITES: list[str] = ["ep", "cs", "fc"]
INJECTS: Path = ROOT / "phenix-injects"


@pytest.fixture(scope="module")
def derived() -> dict[str, site.Site]:
    return {code: site.load(code) for code in CODES}


@pytest.fixture
def site_tree(tree: Path) -> Path:
    for rel in ("plant/plant.yaml", "plant/sites.yaml", "wan/wan.yaml"):
        (tree / rel).parent.mkdir(exist_ok=True)
        shutil.copy(ROOT / rel, tree / rel)
    return tree


def address(code: str, host: str) -> str:
    """The first address of a host in the committed topology of a site."""
    doc: dict[str, Any] = yaml.safe_load(
        (ROOT / "phenix-configs" / f"topology-site-{code}.yml").read_text(encoding="utf-8")
    )
    node = next(n for n in doc["spec"]["nodes"] if n["general"]["hostname"] == host)
    found: str = node["network"]["interfaces"][0]["address"]
    return found


def rules(code: str, router: str, ruleset: str) -> list[tuple[str, str, str, int]]:
    """(description, source, destination, port) of the fleet rules of one committed ruleset."""
    doc: dict[str, Any] = yaml.safe_load(
        (ROOT / "phenix-configs" / f"scenario-site-{code}.yml").read_text(encoding="utf-8")
    )
    vrouter = next(app for app in doc["spec"]["apps"] if app["name"] == "vrouter")
    host = next(h for h in vrouter["hosts"] if h["hostname"] == router)
    found = next(rs for rs in host["metadata"]["acl"]["rulesets"] if rs["name"] == ruleset)
    return [
        (r["description"], r["source"]["address"], r["destination"]["address"], r["destination"]["port"])
        for r in found["rules"]
        if r["description"].startswith("fleet-")
    ]


def test_the_table_has_the_three_conduits_of_a_sensor() -> None:
    rows = {row.id: (row.src, row.dst, [(s.proto, s.port) for s in row.services]) for row in fleet.FLEET_CONDUITS}
    assert rows == {
        "fleet-sensor-logs": ("sensor", "malcolm", [("tcp", 5044)]),
        "fleet-sensor-sessions": ("sensor", "malcolm", [("tcp", 9200)]),
        "fleet-sensor-pcap": ("malcolm", "sensor", [("tcp", 8005)]),
    }
    assert all(row.text for row in fleet.FLEET_CONDUITS)


def test_the_ports_are_those_of_the_sensors_scripts() -> None:
    provision: str = (INJECTS / "sensor" / "cm-sensor-provision").read_text(encoding="utf-8")
    assert f'LOGSTASH_HOST "${{malcolm_host}}:{fleet.LOGSTASH_PORT}"' in provision
    assert f'OPENSEARCH_URL "https://${{malcolm_host}}:{fleet.OPENSEARCH_PORT}"' in provision
    acl: str = (INJECTS / "sensor" / "cm-sensor-acl").read_text(encoding="utf-8")
    assert re.search(rf"^VIEWER_PORT={fleet.VIEWER_PORT}$", acl, re.MULTILINE)


def test_a_sensor_sends_to_the_address_that_the_rules_name() -> None:
    malcolm: str = address("fr", "fr-malcolm")
    for code in SENSOR_SITES:
        env: str = (INJECTS / "fleet" / code / "sensor" / "cm-sensor.env").read_text(encoding="utf-8")
        assert f"\nCM_MALCOLM_HOST={malcolm}\n" in env
        assert f"\nCM_SENSOR_ADDRESS={address(code, f'{code}-sensor')}\n" in env


def test_each_site_has_its_part_of_each_row(derived: dict[str, site.Site]) -> None:
    """A site with a sensor has three pairs. The head office has three for each sensor."""
    assert {code: len(found.fleet) for code, found in derived.items()} == {"fr": 9, "ep": 3, "cs": 3, "fc": 3}
    malcolm: str = address("fr", "fr-malcolm")
    for code in SENSOR_SITES:
        sensor: str = f"{code}-sensor"
        own: str = address(code, sensor)
        for at in (code, "fr"):
            ends = {
                p.conduit: (p.src, p.src_address, p.dst, p.dst_address)
                for p in derived[at].fleet
                if sensor in (p.src, p.dst)
            }
            assert ends == {
                "fleet-sensor-logs": (sensor, own, "fr-malcolm", malcolm),
                "fleet-sensor-sessions": (sensor, own, "fr-malcolm", malcolm),
                "fleet-sensor-pcap": ("fr-malcolm", malcolm, sensor, own),
            }, (code, at)


def test_the_firewalls_of_a_pair_are_those_between_its_host_and_the_far_side_of_the_edge(
    derived: dict[str, site.Site],
) -> None:
    hops = {
        code: {p.conduit: p.hops for p in found.fleet if code != "fr" or "ep-sensor" in (p.src, p.dst)}
        for code, found in derived.items()
    }
    # Out of a site: every firewall up to the edge, and the edge. Into a site: the
    # firewalls behind the edge. The edge takes the packet from a tunnel, which is
    # no interface of the topology and has no ruleset here.
    out_ep = (("ep-fw-ot", "EP-SOC"), ("ep-fw-edge", "EP-DMZ"))
    assert hops["ep"] == {
        "fleet-sensor-logs": out_ep,
        "fleet-sensor-sessions": out_ep,
        "fleet-sensor-pcap": (("ep-fw-ot", "EP-DMZ"),),
    }
    for code, vlan in (("cs", "CS-PLANT"), ("fc", "FC-PLANT")):
        out = ((f"{code}-fw-edge", vlan),)
        assert hops[code] == {"fleet-sensor-logs": out, "fleet-sensor-sessions": out, "fleet-sensor-pcap": ()}
    into_fr = (("fr-fw-ot", "FR-DMZ"),)
    assert hops["fr"] == {
        "fleet-sensor-logs": into_fr,
        "fleet-sensor-sessions": into_fr,
        "fleet-sensor-pcap": (("fr-fw-ot", "FR-SOC"), ("fr-fw-edge", "FR-DMZ")),
    }


def test_the_committed_rules_of_a_site_with_two_firewalls() -> None:
    malcolm: str = address("fr", "fr-malcolm")
    sensor: str = address("ep", "ep-sensor")
    up = [
        ("fleet-sensor-logs: ep-sensor to fr-malcolm tcp/5044", sensor, malcolm, 5044),
        ("fleet-sensor-sessions: ep-sensor to fr-malcolm tcp/9200", sensor, malcolm, 9200),
    ]
    assert rules("ep", "ep-fw-ot", "FROM-EP-SOC") == up
    assert rules("ep", "ep-fw-edge", "FROM-EP-DMZ") == up
    assert rules("ep", "ep-fw-ot", "FROM-EP-DMZ") == [
        ("fleet-sensor-pcap: fr-malcolm to ep-sensor tcp/8005", malcolm, sensor, 8005)
    ]


@pytest.mark.parametrize(("code", "vlan"), [("cs", "CS-PLANT"), ("fc", "FC-PLANT")])
def test_the_committed_rules_of_a_site_with_one_firewall(code: str, vlan: str) -> None:
    malcolm: str = address("fr", "fr-malcolm")
    sensor: str = address(code, f"{code}-sensor")
    assert rules(code, f"{code}-fw-edge", f"FROM-{vlan}") == [
        (f"fleet-sensor-logs: {code}-sensor to fr-malcolm tcp/5044", sensor, malcolm, 5044),
        (f"fleet-sensor-sessions: {code}-sensor to fr-malcolm tcp/9200", sensor, malcolm, 9200),
    ]
    # Malcolm's request comes out of a tunnel and meets no ruleset of the topology:
    # the sensor's own firewall lets only Malcolm reach the viewer (cm-sensor-acl).
    doc: dict[str, Any] = yaml.safe_load(
        (ROOT / "phenix-configs" / f"scenario-site-{code}.yml").read_text(encoding="utf-8")
    )
    assert "tcp/8005" not in yaml.safe_dump(doc)
    assert "counter drop" in (INJECTS / "sensor" / "cm-sensor-acl").read_text(encoding="utf-8")


def test_the_committed_rules_of_the_head_office() -> None:
    malcolm: str = address("fr", "fr-malcolm")
    sensors = {code: address(code, f"{code}-sensor") for code in SENSOR_SITES}
    assert rules("fr", "fr-fw-ot", "FROM-FR-DMZ") == [
        (f"{row}: {code}-sensor to fr-malcolm tcp/{port}", sensors[code], malcolm, port)
        for row, port in (("fleet-sensor-logs", 5044), ("fleet-sensor-sessions", 9200))
        for code in SENSOR_SITES
    ]
    back = [
        (f"fleet-sensor-pcap: fr-malcolm to {code}-sensor tcp/8005", malcolm, sensors[code], 8005)
        for code in SENSOR_SITES
    ]
    assert rules("fr", "fr-fw-ot", "FROM-FR-SOC") == back
    assert rules("fr", "fr-fw-edge", "FROM-FR-DMZ") == back


def test_the_two_ends_of_a_row_have_the_same_rule() -> None:
    """What a sensor's site lets out is what the head office lets in, and the reverse."""
    for code in SENSOR_SITES:
        out = {r[1:] for name in checks_rulesets(code) for r in rules(code, *name) if "to fr-malcolm" in r[0]}
        into = {
            r[1:]
            for name in checks_rulesets("fr")
            for r in rules("fr", *name)
            if f"{code}-sensor to fr-malcolm" in r[0]
        }
        assert out == into and len(out) == 2
        back_out = {
            r[1:]
            for name in checks_rulesets("fr")
            for r in rules("fr", *name)
            if f"fr-malcolm to {code}-sensor" in r[0]
        }
        back_into = {r[1:] for name in checks_rulesets(code) for r in rules(code, *name) if "fr-malcolm to" in r[0]}
        assert len(back_out) == 1 and back_into <= back_out


def checks_rulesets(code: str) -> list[tuple[str, str]]:
    """(router, ruleset) of every ruleset of a site's committed scenario."""
    doc: dict[str, Any] = yaml.safe_load(
        (ROOT / "phenix-configs" / f"scenario-site-{code}.yml").read_text(encoding="utf-8")
    )
    vrouter = next(app for app in doc["spec"]["apps"] if app["name"] == "vrouter")
    return [(h["hostname"], rs["name"]) for h in vrouter["hosts"] for rs in h["metadata"]["acl"]["rulesets"]]


def test_no_flow_inside_a_site_uses_a_port_of_the_fleet_rows(derived: dict[str, site.Site]) -> None:
    """The sweep of `flows` tries the three ports between every two hosts of a site."""
    swept = {(s.proto, s.port) for s in checks._service_set()}
    assert {(s.proto, s.port) for s in fleet.SERVICES} <= swept
    for code in CODES:
        assert not [f for f in checks.run_site(code) if f.severity == "error"], code


def test_a_fleet_rule_is_known_to_the_coverage_check(derived: dict[str, site.Site]) -> None:
    known = sitegen.fleet_rules(derived["ep"])
    assert known == {
        "ep-fw-ot:FROM-EP-SOC:" + str(n) for n in fleet_ids("ep", "ep-fw-ot", "FROM-EP-SOC")
    } | {"ep-fw-edge:FROM-EP-DMZ:" + str(n) for n in fleet_ids("ep", "ep-fw-edge", "FROM-EP-DMZ")} | {
        "ep-fw-ot:FROM-EP-DMZ:" + str(n) for n in fleet_ids("ep", "ep-fw-ot", "FROM-EP-DMZ")
    }
    assert len(known) == 5


def fleet_ids(code: str, router: str, ruleset: str) -> list[int]:
    doc: dict[str, Any] = yaml.safe_load(
        (ROOT / "phenix-configs" / f"scenario-site-{code}.yml").read_text(encoding="utf-8")
    )
    vrouter = next(app for app in doc["spec"]["apps"] if app["name"] == "vrouter")
    host = next(h for h in vrouter["hosts"] if h["hostname"] == router)
    found = next(rs for rs in host["metadata"]["acl"]["rulesets"] if rs["name"] == ruleset)
    return [r["id"] for r in found["rules"] if r["description"].startswith("fleet-")]


def test_a_rule_to_another_site_that_no_row_has_is_found(site_tree: Path) -> None:
    """A rule with a far end is no free pass: only the computed ones are known."""

    def add(doc: dict[str, Any]) -> None:
        ruleset(doc, "cs-fw-edge", "FROM-CS-PLANT")["rules"].append(
            {
                "id": 9000,
                "action": "accept",
                "description": "fleet-sensor-logs: cs-sensor to fr-wazuh tcp/5044",
                "protocol": "tcp",
                "source": {"address": address("cs", "cs-sensor")},
                "destination": {"address": "10.40.10.11", "port": 5044},
            }
        )

    edit_yaml(site_tree / "phenix-configs" / "scenario-site-cs.yml", add)
    found = [f"[{f.check}] {f.message}" for f in checks.run_site("cs", site_tree) if f.severity == "error"]
    assert any("cs-fw-edge FROM-CS-PLANT rule 9000" in line and "matches no conduit flow" in line for line in found)
