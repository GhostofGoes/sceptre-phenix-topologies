"""The Suricata rules and the Zeek script of Malcolm agree with the topology (DESIGN.md 10.2 and 14)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from soc_paths import INJECTS, node_ip, repo, topology_nodes  # noqa: F401 (the fixtures are used by name)

MALCOLM: Path = INJECTS / "malcolm"
RULES: Path = MALCOLM / "cherrymountain.rules"
ZEEK: Path = MALCOLM / "zeek" / "cherrymountain.zeek"
SID: re.Pattern[str] = re.compile(r"sid:\s*(\d+)")


def rule_lines() -> list[str]:
    return [line for line in RULES.read_text().splitlines() if line.startswith(("alert", "drop"))]


def addresses(text: str) -> set[str]:
    return set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text))


def field_inventory(nodes: dict[str, dict]) -> set[str]:
    """Every address on a FIELD-* or DERLINK VLAN in the topology (DESIGN.md 10.2)."""
    out: set[str] = set()
    for node in nodes.values():
        for iface in node.get("network", {}).get("interfaces", []):
            if str(iface.get("vlan", "")).startswith("FIELD-") or iface.get("vlan") == "DERLINK":
                out.add(str(iface["address"]))
    return out


def test_every_rule_has_a_unique_custom_sid() -> None:
    sids = [int(m.group(1)) for line in rule_lines() for m in [SID.search(line)] if m]
    assert len(sids) == len(rule_lines()), "a rule has no sid"
    assert len(sids) == len(set(sids)), "sids must be unique"
    # The malcolm spike found that sids from 1000001 up clash with Malcolm's own OT rules, and
    # that 9000101 to 9000103 are its own test rules.
    assert min(sids) >= 9000110
    assert all(sid < 9000200 for sid in sids)


def test_every_rule_has_a_message_and_a_use_case() -> None:
    for line in rule_lines():
        assert 'msg:"CM ' in line
        assert "metadata:cm_usecase" in line, line[:80]


def test_the_field_inventory_matches_the_topology(topology_nodes: dict[str, dict]) -> None:
    expected = field_inventory(topology_nodes)
    assert len(expected) == 17, expected
    rule = next(line for line in rule_lines() if "sid:9000140;" in line)
    negated = set(re.findall(r"!(\d+\.\d+\.\d+\.\d+)", rule))
    assert negated == expected
    zeek = ZEEK.read_text()
    block = zeek[zeek.index("const field_inventory"):]
    block = block[: block.index("} &redef;")]
    assert addresses(block) == expected


def test_the_bbmds_match_the_topology(topology_nodes: dict[str, dict]) -> None:
    ot_svc = node_ip(topology_nodes, "ot-svc")
    bas_bbmd = node_ip(topology_nodes, "bas-bbmd")
    for line in rule_lines():
        if 'msg:"CM BACnet' in line and "from a host other than the BBMDs" in line:
            assert f"![{ot_svc},{bas_bbmd}]" in line, line[:90]
    zeek = ZEEK.read_text()
    assert f"const bbmd_hosts: set[addr] = {{ {ot_svc}, {bas_bbmd} }}" in zeek


def test_the_clients_match_the_topology(topology_nodes: dict[str, dict]) -> None:
    scada = node_ip(topology_nodes, "scada-fuxa")
    ews = node_ip(topology_nodes, "ews")
    bas_fuxa = node_ip(topology_nodes, "bas-fuxa")
    modbus = next(line for line in rule_lines() if "sid:9000110;" in line)
    assert scada in modbus and ews in modbus
    s7 = next(line for line in rule_lines() if "sid:9000111;" in line)
    assert f"![{scada},{ews}]" in s7
    bacnet = next(line for line in rule_lines() if "sid:9000113;" in line)
    assert f"![{bas_fuxa},{ews}]" in bacnet
    assert scada not in bacnet.split("->")[0]
    zeek = ZEEK.read_text()
    assert f"const bacnet_writers: set[addr] = {{ {bas_fuxa}, {ews} }}" in zeek
    assert f"const ews_host: addr = {ews}" in zeek


def test_the_vendor_path_matches_the_topology(topology_nodes: dict[str, dict]) -> None:
    jump = node_ip(topology_nodes, "dmz-jump")
    vpn = node_ip(topology_nodes, "dmz-vpn")
    text = RULES.read_text()
    assert f"-> {vpn} 51820" in text
    assert f"-> {jump} 443" in text
    assert f"10.99.0.0/24 any -> !{jump} any" in text


def test_life_safety_addresses_match_plantgen(repo: Path) -> None:
    """The coil numbers the life-safety rules and notices name (DESIGN.md 6.3, 6.7)
    are plantgen's, and the power-state bit Zeek watches is still load shed."""
    regs = json.loads((repo / "plant" / "generated" / "full" / "registers.json").read_text())
    by_tag = {r["tag"]: r for r in regs["registers"]}
    valve = by_tag["UTL.CO2.XV01.CMD"]
    assert (valve["rio"], valve["otsim_register"], valve["slave_pdu_address"]) == ("rio-utilities", 4, 804)
    assert by_tag["UTL.CO2.XC01.RESET"]["slave_pdu_address"] == 11
    assert by_tag["UTL.CO2.XS02.SHUTOFF"]["slave_pdu_address"] == 9
    assert by_tag["PWR.DMD.XS02.SHED"]["slave_pdu_address"] == 10
    permits = [by_tag[f"CEL.ENTRY.HS0{n}.ENTRY"]["slave_pdu_address"] for n in range(1, 7)]
    assert permits == list(range(35, 41))
    zeek = ZEEK.read_text()
    for n in range(1, 7):
        address = by_tag[f"CEL.ENTRY.XA0{n}.PV"]["slave_pdu_address"]
        assert f"$server=10.20.10.12, $fc=1, $address={address}," in zeek
    assert '$server=10.20.10.15, $fc=1, $address=10, $name="load_shed"' in zeek
    assert '$server=10.20.10.14, $fc=1, $address=9, $name="co2_shutoff"' in zeek
    assert "$first=35, $last=40" in zeek and "$first=11, $last=11" in zeek
    rules = RULES.read_text()
    assert 'content:"|05 00 0b|"' in rules  # coil 11
    assert "byte_test:1,>=,35,9; byte_test:1,<=,40,9;" in rules


def test_the_dnp3_master_is_the_utility_derms_address(repo: Path) -> None:
    text = (repo / "plc" / "../phenix-configs/topology-core.yml").resolve().read_text()
    assert "192.0.2.10" in text
    assert text.count("![192.0.2.10]") == 0
    assert RULES.read_text().count("![192.0.2.10]") == 4


@pytest.mark.skipif(shutil.which("suricata") is None, reason="suricata is not installed")
def test_suricata_loads_the_rules(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "suricata", "-T", "-c", "/etc/suricata/suricata.yaml", "-S", str(RULES), "-l", str(tmp_path),
            "--set", "app-layer.protocols.modbus.enabled=yes", "--set", "app-layer.protocols.dnp3.enabled=yes",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    if "Unable to open" in output and "suricata.yaml" in output:
        pytest.skip("no suricata.yaml")
    assert result.returncode == 0, output
    assert "Duplicate signature" not in output
    assert "<Error>" not in output
