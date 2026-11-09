#!/var/ossec/framework/python/bin/python3
"""Forward a Wazuh alert to a case-management tool (TheHive 5 or DFIR-IRIS 2.4).

Spike proof of concept (spikes/thehive/README.md), not production code. Wazuh's
integratord runs it as ``/var/ossec/integrations/custom-cm-case`` (the shell
wrapper puts the pinned Pydantic on ``PYTHONPATH`` and runs this file with Wazuh's
embedded Python) with these arguments:

    <alert file> <api key> <hook url> [debug] [<options file>]

The options file holds the ``<options>`` JSON of the ``<integration>`` block, for
example ``{"backend": "thehive"}`` or ``{"backend": "iris", "customer_id": 1}``.

Mapping (README "Integration design"):

* Severity from the Wazuh rule level: 7-9 low, 10-11 medium, 12-13 high, 14-16
  critical. Levels under 7 are not forwarded (the ``<level>`` filter of the
  integration block should already have dropped them).
* Dedup: the reference sent to the tool is the Wazuh alert id, so a retried
  delivery is refused by the tool as a duplicate (TheHive: type + source +
  sourceRef; IRIS: the script checks ``alert_source_ref`` first). Alerts of the
  groups in ``BUCKETED_GROUPS`` (firewall drops) get a reference made of the rule,
  the source, the destination, and a 15-minute bucket instead, so a scan becomes
  one alert per bucket and not one per packet.
* Observables (IRIS: IOCs and assets): source and destination addresses, the
  reporting host, the PLC named by cm-plc-tool, and the account names.
* Playbook: the rule groups pick the case template that the analyst gets on
  promotion (``PLAYBOOKS``).

Run ``cm_wazuh_case.py --dry-run --backend thehive <alert file>`` to print the
request body without sending it. Standard library only besides Pydantic, so it
runs in Wazuh's embedded Python 3.10.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Rule groups whose alerts are folded into one per (rule, source, destination, bucket).
BUCKETED_GROUPS: Final[frozenset[str]] = frozenset({"cm_firewall"})
#: Width of a dedup bucket in seconds.
BUCKET_SECONDS: Final[int] = 900

#: First matching rule group -> case template (TheHive) or case template name (IRIS).
PLAYBOOKS: Final[tuple[tuple[str, str], ...]] = (
    ("cm_tamper", "CM-IR-04 Evidence tampering on the vendor path"),
    ("cm_program_change", "CM-IR-02 PLC program or mode change"),
    ("cm_vendor_outside_window", "CM-IR-01 Vendor session outside the approved window"),
    ("cm_blocked_conduit", "CM-IR-03 Blocked conduit attempt or lateral movement"),
    ("cm_scan", "CM-IR-03 Blocked conduit attempt or lateral movement"),
    ("cm_vendor", "CM-IR-01 Vendor session outside the approved window"),
)

#: Wazuh's integrations log, relative to /var/ossec.
LOG_FILE: Final[Path] = (
    Path(__file__).resolve().parent.parent / "logs" / "integrations.log"
)

Severity = Literal["low", "medium", "high", "critical"]
#: TheHive 5 severities.
THEHIVE_SEVERITY: Final[dict[str, int]] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}
#: DFIR-IRIS 2.4.29 default ids, read from a fresh install (GET /manage/severities/list,
#: /manage/ioc-types/list, /manage/asset-type/list, /manage/alert-status/list).
IRIS_SEVERITY: Final[dict[str, int]] = {"low": 4, "medium": 1, "high": 5, "critical": 6}
IRIS_IOC_TYPE: Final[dict[str, int]] = {"src": 79, "dst": 77, "account": 3, "other": 96}
IRIS_ASSET_FIREWALL: Final[int] = 2
IRIS_ASSET_LINUX_SERVER: Final[int] = 3
IRIS_STATUS_NEW: Final[int] = 2


class WazuhRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    level: int
    description: str
    groups: list[str] = Field(default_factory=list)
    mitre: dict[str, Any] = Field(default_factory=dict)


class WazuhAgent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = "000"
    name: str = ""
    ip: str | None = None


class WazuhAlert(BaseModel):
    """The fields of a Wazuh 4.x JSON alert (alerts.json) the mapping reads."""

    model_config = ConfigDict(extra="ignore")

    id: str
    timestamp: datetime
    rule: WazuhRule
    agent: WazuhAgent = Field(default_factory=WazuhAgent)
    location: str = ""
    full_log: str = ""
    predecoder: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _colon_offset(cls, value: Any) -> Any:
        # Wazuh writes "2026-11-09T18:40:01.123+0000"; ISO 8601 parsers want "+00:00".
        if isinstance(value, str):
            return re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
        return value

    @property
    def host(self) -> str:
        """The host the event is about: the syslog sender for the manager's own
        listener (agent 000), else the agent."""
        if self.agent.id == "000" and self.predecoder.get("hostname"):
            return str(self.predecoder["hostname"])
        return self.agent.name


class Observable(BaseModel):
    data_type: str
    value: str
    tags: list[str] = Field(default_factory=list)
    message: str = ""


class CaseAlert(BaseModel):
    """Backend-neutral alert, rendered by to_thehive() or to_iris()."""

    title: str
    description: str
    severity: Severity
    source_ref: str
    event_time: datetime
    tags: list[str]
    observables: list[Observable]
    playbook: str | None
    raw: dict[str, Any]


class Options(BaseModel):
    model_config = ConfigDict(extra="ignore")

    backend: Literal["thehive", "iris"] = "thehive"
    #: IRIS customer that owns the alerts, and the range's "PLC" asset type, if provisioned.
    customer_id: int = 1
    plc_asset_type_id: int | None = None
    #: CA that signed the case tool's certificate (the per-experiment range CA); None
    #: uses the system store. Verification is never turned off.
    ca_file: Path | None = None
    timeout_s: float = 10.0


def severity_for(level: int) -> Severity | None:
    if level >= 14:
        return "critical"
    if level >= 12:
        return "high"
    if level >= 10:
        return "medium"
    if level >= 7:
        return "low"
    return None


def source_ref(alert: WazuhAlert) -> str:
    if BUCKETED_GROUPS.intersection(alert.rule.groups):
        bucket: int = int(alert.timestamp.timestamp()) // BUCKET_SECONDS
        key: str = "|".join(
            [
                alert.rule.id,
                str(alert.data.get("srcip", "")),
                str(alert.data.get("dstip", "")),
                str(alert.data.get("dstport", "")),
                str(bucket),
            ]
        )
        return "wazuh-b-" + hashlib.sha256(key.encode()).hexdigest()[:16]
    return "wazuh-" + alert.id


def observables_for(alert: WazuhAlert) -> list[Observable]:
    found: list[Observable] = []
    seen: set[tuple[str, str]] = set()

    def add(data_type: str, value: Any, *tags: str, message: str = "") -> None:
        text: str = str(value or "").strip()
        if text and (data_type, text) not in seen:
            seen.add((data_type, text))
            found.append(
                Observable(
                    data_type=data_type, value=text, tags=list(tags), message=message
                )
            )

    add("ip", alert.data.get("srcip"), "src")
    add("ip", alert.data.get("dstip"), "dst")
    add("hostname", alert.host, "reporter", message="host that logged the event")
    add(
        "hostname",
        alert.data.get("plc"),
        "plc",
        "ics-asset",
        message="PLC named by cm-plc-tool",
    )
    for field in ("srcuser", "dstuser"):
        add("other", alert.data.get(field), "account", field)
    add("other", alert.data.get("peer"), "wireguard-peer")
    return found


def playbook_for(groups: list[str]) -> str | None:
    for group, template in PLAYBOOKS:
        if group in groups:
            return template
    return None


def to_case_alert(alert: WazuhAlert) -> CaseAlert | None:
    severity: Severity | None = severity_for(alert.rule.level)
    if severity is None:
        return None
    fields: str = "\n".join(f"| {k} | `{v}` |" for k, v in sorted(alert.data.items()))
    description: str = (
        f"**Wazuh rule {alert.rule.id}** (level {alert.rule.level}): {alert.rule.description}\n\n"
        f"- Host: `{alert.host}` (agent {alert.agent.id} {alert.agent.name})\n"
        f"- Location: `{alert.location}`\n- Wazuh alert id: `{alert.id}`\n\n"
        f"```\n{alert.full_log}\n```\n\n| Field | Value |\n|---|---|\n{fields}\n"
    )
    tags: list[str] = ["wazuh", f"wazuh:rule={alert.rule.id}", f"host={alert.host}"]
    tags += [g for g in alert.rule.groups if g.startswith("cm_")]
    tags += [f"mitre:{t}" for t in alert.rule.mitre.get("id", [])]
    return CaseAlert(
        title=f"[{alert.host}] {alert.rule.description}"[:250],
        description=description,
        severity=severity,
        source_ref=source_ref(alert),
        event_time=alert.timestamp,
        tags=tags,
        observables=observables_for(alert),
        playbook=playbook_for(alert.rule.groups),
        raw=alert.model_dump(mode="json"),
    )


def to_thehive(case: CaseAlert) -> dict[str, Any]:
    """POST /api/v1/alert body (TheHive 5)."""
    body: dict[str, Any] = {
        "type": "wazuh",
        "source": "wazuh-cherrymountain",
        "sourceRef": case.source_ref,
        "title": case.title,
        "description": case.description,
        "severity": THEHIVE_SEVERITY[case.severity],
        "date": int(case.event_time.timestamp() * 1000),
        "tags": case.tags,
        "tlp": 2,
        "pap": 2,
        "observables": [
            {
                "dataType": o.data_type,
                "data": o.value,
                "tags": o.tags,
                "message": o.message,
            }
            for o in case.observables
        ],
    }
    if case.playbook:
        body["caseTemplate"] = case.playbook
    return body


def _iris_ioc_type(o: Observable) -> int:
    if o.data_type == "ip":
        return IRIS_IOC_TYPE["src"] if "src" in o.tags else IRIS_IOC_TYPE["dst"]
    return IRIS_IOC_TYPE["account"] if "account" in o.tags else IRIS_IOC_TYPE["other"]


def to_iris(
    case: CaseAlert, customer_id: int, plc_asset_type_id: int | None = None
) -> dict[str, Any]:
    """POST /alerts/add body (DFIR-IRIS 2.4). Hosts become assets: the firewalls as
    Firewall, a PLC as the range's own "PLC" asset type when one is provisioned
    (``plc_asset_type_id``), everything else as Linux - Server."""
    iocs: list[dict[str, Any]] = [
        {
            "ioc_value": o.value,
            "ioc_type_id": _iris_ioc_type(o),
            "ioc_tlp_id": 2,
            "ioc_description": o.message or ",".join(o.tags),
            "ioc_tags": ",".join(o.tags),
        }
        for o in case.observables
        if o.data_type in ("ip", "other")
    ]
    assets: list[dict[str, Any]] = []
    for o in case.observables:
        if o.data_type != "hostname":
            continue
        type_id: int = IRIS_ASSET_LINUX_SERVER
        if o.value.startswith("fw-"):
            type_id = IRIS_ASSET_FIREWALL
        elif "plc" in o.tags and plc_asset_type_id is not None:
            type_id = plc_asset_type_id
        assets.append(
            {
                "asset_name": o.value,
                "asset_type_id": type_id,
                "asset_description": o.message,
                "asset_tags": ",".join(o.tags),
            }
        )
    return {
        "alert_title": case.title,
        "alert_description": case.description,
        "alert_source": "wazuh-cherrymountain",
        "alert_source_ref": case.source_ref,
        "alert_source_event_time": case.event_time.astimezone(timezone.utc).isoformat(),
        "alert_severity_id": IRIS_SEVERITY[case.severity],
        "alert_status_id": IRIS_STATUS_NEW,
        "alert_customer_id": customer_id,
        "alert_tags": ",".join(case.tags),
        "alert_note": f"Playbook: {case.playbook}" if case.playbook else "",
        "alert_iocs": iocs,
        "alert_assets": assets,
        "alert_source_content": case.raw,
    }


def _request(
    url: str, key: str, body: dict[str, Any] | None, opts: Options
) -> tuple[int, str]:
    data: bytes | None = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    ctx: ssl.SSLContext = ssl.create_default_context(
        cafile=str(opts.ca_file) if opts.ca_file else None
    )
    try:
        with urllib.request.urlopen(req, timeout=opts.timeout_s, context=ctx) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors="replace")


def send(case: CaseAlert, key: str, hook: str, opts: Options) -> str:
    """Send one alert; returns "created", "duplicate", or raises RuntimeError."""
    base: str = hook.rstrip("/")
    if opts.backend == "thehive":
        status, text = _request(f"{base}/api/v1/alert", key, to_thehive(case), opts)
        if status in (200, 201):
            return "created"
        if status == 400 and "already exist" in text.lower():
            return "duplicate"
        raise RuntimeError(f"TheHive answered HTTP {status}: {text[:300]}")
    # IRIS has no uniqueness on alert_source_ref, so look it up first.
    query: str = (
        f"{base}/alerts/filter?source_reference={urllib.parse.quote(case.source_ref)}"
    )
    status, text = _request(query, key, None, opts)
    if status == 200 and json.loads(text).get("data", {}).get("total", 0) > 0:
        return "duplicate"
    body: dict[str, Any] = to_iris(case, opts.customer_id, opts.plc_asset_type_id)
    status, text = _request(f"{base}/alerts/add", key, body, opts)
    if status in (200, 201):
        return "created"
    raise RuntimeError(f"IRIS answered HTTP {status}: {text[:300]}")


def _log(message: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{datetime.now(timezone.utc).isoformat()} custom-cm-case: {message}\n"
            )
    except OSError:
        print(message, file=sys.stderr)


def integratord_main(argv: list[str]) -> int:
    alert_file: Path = Path(argv[1])
    key: str = argv[2]
    hook: str = argv[3]
    opts_file: str | None = next((a for a in argv[4:] if a.endswith("options")), None)
    opts: Options = (
        Options.model_validate_json(Path(opts_file).read_text())
        if opts_file
        else Options()
    )
    alert: WazuhAlert = WazuhAlert.model_validate_json(alert_file.read_text())
    case: CaseAlert | None = to_case_alert(alert)
    if case is None:
        _log(
            f"rule {alert.rule.id} level {alert.rule.level} below the forwarding threshold"
        )
        return 0
    try:
        result: str = send(case, key, hook, opts)
    except (RuntimeError, OSError) as err:
        _log(f"FAIL {opts.backend} rule {alert.rule.id} ref {case.source_ref}: {err}")
        return 1
    _log(
        f"{result} {opts.backend} rule {alert.rule.id} severity {case.severity} ref {case.source_ref}"
    )
    return 0


def main() -> int:
    if len(sys.argv) >= 4 and not sys.argv[1].startswith("-"):
        return integratord_main(sys.argv)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--backend", choices=["thehive", "iris"], default="thehive")
    parser.add_argument("alert", type=Path)
    args = parser.parse_args()
    case: CaseAlert | None = to_case_alert(
        WazuhAlert.model_validate_json(args.alert.read_text())
    )
    if case is None:
        print("not forwarded (level below 7)")
        return 0
    body: dict[str, Any] = (
        to_thehive(case) if args.backend == "thehive" else to_iris(case, 1)
    )
    print(json.dumps(body, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
