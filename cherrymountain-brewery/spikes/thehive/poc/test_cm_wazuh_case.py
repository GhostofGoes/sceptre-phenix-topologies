"""Offline checks of the Wazuh-to-case mapping (spikes/thehive/README.md).

The fixtures are real alerts from the spike's Wazuh 4.14.1 manager, made from the
lines of phenix-injects/wazuh/samples.txt (the manager's container name replaced by
"wazuh"). Run: python3 -m pytest spikes/thehive/poc
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

HERE: Path = Path(__file__).resolve().parent
FIXTURES: Path = HERE / "fixtures" / "wazuh-alerts.jsonl"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "cm_wazuh_case", HERE / "cm_wazuh_case.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules["cm_wazuh_case"] = module
    spec.loader.exec_module(module)
    return module


cwc: ModuleType = _load()


@pytest.fixture(scope="module")
def alerts() -> dict[str, Any]:
    by_rule: dict[str, Any] = {}
    for line in FIXTURES.read_text().splitlines():
        alert = cwc.WazuhAlert.model_validate_json(line)
        by_rule[alert.rule.id] = alert
    return by_rule


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (6, None),
        (7, "low"),
        (9, "low"),
        (10, "medium"),
        (11, "medium"),
        (12, "high"),
        (13, "high"),
        (14, "critical"),
        (15, "critical"),
    ],
)
def test_severity_bands(level: int, expected: str | None) -> None:
    assert cwc.severity_for(level) == expected


def test_baseline_vendor_tunnel_is_not_forwarded(alerts: dict[str, Any]) -> None:
    assert alerts["100201"].rule.level == 6
    assert cwc.to_case_alert(alerts["100201"]) is None


def test_plc_upload_maps_to_the_plc_change_playbook(alerts: dict[str, Any]) -> None:
    case = cwc.to_case_alert(alerts["100411"])
    assert case is not None
    assert case.severity == "low"
    assert case.playbook == "CM-IR-02 PLC program or mode change"
    assert case.source_ref == "wazuh-" + alerts["100411"].id
    found: set[tuple[str, str]] = {(o.data_type, o.value) for o in case.observables}
    assert {
        ("hostname", "plc-cellar"),
        ("hostname", "ews"),
        ("ip", "10.20.10.12"),
        ("other", "ridgeline"),
    } <= found
    body: dict[str, Any] = cwc.to_thehive(case)
    assert body["caseTemplate"] == case.playbook
    assert body["severity"] == 1
    assert body["date"] == int(alerts["100411"].timestamp.timestamp() * 1000)


def test_firewall_reporter_is_the_syslog_sender(alerts: dict[str, Any]) -> None:
    # The manager's own syslog listener is agent 000; the host is the sender.
    assert alerts["100101"].agent.id == "000"
    assert alerts["100101"].host == "fw-ot"


def test_firewall_drops_fold_into_one_alert_per_bucket(alerts: dict[str, Any]) -> None:
    first = alerts["100101"]
    same = first.model_copy(
        update={"id": "1.2", "timestamp": first.timestamp + timedelta(seconds=1)}
    )
    other_dst = first.model_copy(
        update={"id": "1.3", "data": {**first.data, "dstip": "10.20.10.13"}}
    )
    refs: list[str] = [cwc.source_ref(a) for a in (first, same, other_dst)]
    assert refs[0] == refs[1]
    assert refs[0] != refs[2]
    assert refs[0].startswith("wazuh-b-")


def test_vendor_alerts_keep_their_own_reference(alerts: dict[str, Any]) -> None:
    case = cwc.to_case_alert(alerts["100105"])
    assert case is not None
    assert case.severity == "high"
    assert case.source_ref == "wazuh-" + alerts["100105"].id
    assert case.playbook == "CM-IR-03 Blocked conduit attempt or lateral movement"


def test_iris_body_uses_the_ics_asset_type(alerts: dict[str, Any]) -> None:
    case = cwc.to_case_alert(alerts["100411"])
    body: dict[str, Any] = cwc.to_iris(case, customer_id=1, plc_asset_type_id=22)
    assets: dict[str, int] = {
        a["asset_name"]: a["asset_type_id"] for a in body["alert_assets"]
    }
    assert assets == {"ews": cwc.IRIS_ASSET_LINUX_SERVER, "plc-cellar": 22}
    iocs: dict[str, int] = {
        i["ioc_value"]: i["ioc_type_id"] for i in body["alert_iocs"]
    }
    assert iocs == {
        "10.20.10.12": cwc.IRIS_IOC_TYPE["dst"],
        "ridgeline": cwc.IRIS_IOC_TYPE["account"],
    }
    assert body["alert_severity_id"] == cwc.IRIS_SEVERITY["low"]
    json.dumps(body)  # serializable as sent


def test_iris_firewall_asset(alerts: dict[str, Any]) -> None:
    body: dict[str, Any] = cwc.to_iris(
        cwc.to_case_alert(alerts["100101"]), customer_id=1
    )
    assert {a["asset_name"]: a["asset_type_id"] for a in body["alert_assets"]} == {
        "fw-ot": cwc.IRIS_ASSET_FIREWALL
    }
