"""The Wazuh decoders and rules (DESIGN.md 14), checked without a manager.

There is no Wazuh in the repository's CI, so this is a structural test plus a
small evaluator of the rule options the rules use. It checks that every sample
line of phenix-injects/wazuh/samples.txt is decoded into the fields its rules
need and ends at the rule the line names. The evaluator knows only the options in
cherrymountain_rules.xml; a rule that uses a new option fails the test until the
evaluator learns it, which is intended.
"""

from __future__ import annotations

import ipaddress
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, Field

from conftest import INJECTS

WAZUH: Path = INJECTS / "wazuh"
#: Wazuh's own rules that ours extend.
BUILTIN_RULES: frozenset[int] = frozenset({553, 554, 5501, 5715})
#: Rule options the evaluator understands, and the ones that are always true here
#: (time and weekday depend on when the test runs).
KNOWN_OPTIONS: frozenset[str] = frozenset(
    {
        "if_sid", "if_matched_sid", "decoded_as", "hostname", "program_name", "dstport",
        "protocol", "action", "srcip", "user", "regex", "field", "time", "weekday",
        "same_source_ip", "description", "group",
    }
)
SYSLOG: re.Pattern[str] = re.compile(
    r"^(?P<month>\w{3}) +(?P<day>\d+) (?P<clock>[\d:]+) (?P<hostname>\S+) (?P<program>[^\s:\[]+)(?:\[\d+\])?: (?P<message>.*)$"
)


class Decoder(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    parent: str | None = None
    program_name: str | None = None
    prematch: str | None = None
    regex: str | None = None
    order: tuple[str, ...] = ()


class Rule(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    level: int = Field(ge=0, le=15)
    options: dict[str, list[str]]
    attrs: dict[str, dict[str, str]] = {}

    def values(self, option: str) -> list[str]:
        return self.options.get(option, [])


def load_decoders() -> list[Decoder]:
    root = ET.fromstring(f"<decoders>{(WAZUH / 'cherrymountain_decoders.xml.in').read_text()}</decoders>")
    out: list[Decoder] = []
    for node in root.findall("decoder"):
        order = node.findtext("order")
        out.append(
            Decoder(
                name=node.get("name", ""),
                parent=node.findtext("parent"),
                program_name=node.findtext("program_name"),
                prematch=node.findtext("prematch"),
                regex=node.findtext("regex"),
                order=tuple(x.strip() for x in order.split(",")) if order else (),
            )
        )
    return out


def load_rules() -> list[Rule]:
    root = ET.fromstring((WAZUH / "cherrymountain_rules.xml").read_text())
    out: list[Rule] = []
    for node in root.iter("rule"):
        options: dict[str, list[str]] = {}
        attrs: dict[str, dict[str, str]] = {}
        for child in node:
            options.setdefault(child.tag, []).append((child.text or "").strip())
            attrs.setdefault(child.tag, {}).update(child.attrib)
        out.append(Rule(id=int(node.get("id", "0")), level=int(node.get("level", "0")), options=options, attrs=attrs))
    return out


DECODERS: list[Decoder] = load_decoders()
RULES: list[Rule] = load_rules()
RULES_BY_ID: dict[int, Rule] = {r.id: r for r in RULES}


def test_decoders_are_well_formed() -> None:
    names = [d.name for d in DECODERS]
    assert len(names) == len(set(names)), "decoder names must be unique"
    for decoder in DECODERS:
        if decoder.parent:
            assert decoder.parent in names, f"{decoder.name}: unknown parent {decoder.parent}"
        for pattern in (decoder.program_name, decoder.prematch, decoder.regex):
            if pattern:
                re.compile(pattern)
        if decoder.regex:
            groups = re.compile(decoder.regex).groups
            assert groups == len(decoder.order), f"{decoder.name}: {groups} groups but {len(decoder.order)} fields in <order>"


def test_rules_are_well_formed() -> None:
    ids = [r.id for r in RULES]
    assert len(ids) == len(set(ids)), "rule ids must be unique"
    decoder_names = {d.name for d in DECODERS}
    for rule in RULES:
        assert 100100 <= rule.id <= 100499, f"rule {rule.id} is outside the range this file owns"
        assert set(rule.options) <= KNOWN_OPTIONS, f"rule {rule.id}: unknown options {set(rule.options) - KNOWN_OPTIONS}"
        assert rule.values("description"), f"rule {rule.id} has no description"
        for option in ("if_sid", "if_matched_sid"):
            for value in rule.values(option):
                parent = int(value)
                assert parent in RULES_BY_ID or parent in BUILTIN_RULES, f"rule {rule.id}: {option} {parent} is not defined"
        for name in rule.values("decoded_as"):
            assert name in decoder_names, f"rule {rule.id}: unknown decoder {name}"
        assert rule.options.keys() & {"if_sid", "decoded_as", "if_matched_sid"}, f"rule {rule.id} has no parent or decoder"
        for option in ("regex", "field"):
            for value in rule.values(option):
                if rule.attrs.get(option, {}).get("type") == "pcre2":
                    re.compile(value)
        for field in ("hostname", "program_name", "dstport", "protocol", "action", "user"):
            for value in rule.values(field):
                re.compile(value.replace("$|^", "|^"))  # alternations of anchored words


#: What Wazuh's own decoders and rules contribute for the log lines of the samples: the
#: pattern of the built-in parent rule, and the fields the built-in decoder extracts.
BUILTIN_PARENTS: dict[int, str] = {
    5715: r"^Accepted \w+ for \S+ from \S+ port \d+",
    5501: r"pam_unix\(\S+:session\): session opened for user \S+",
}
BUILTIN_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"^Accepted \w+ for (\S+) from (\S+) port", ("dstuser", "srcip")),
    (r"session opened for user ([^\s(]+)", ("dstuser",)),
)


class Event(BaseModel):
    """A log line after pre-decoding and decoding."""

    hostname: str
    program_name: str
    message: str
    fields: dict[str, str] = {}
    decoder: str | None = None


def decode(line: str) -> Event:
    match = SYSLOG.match(line)
    assert match, f"not a syslog line: {line}"
    event = Event(hostname=match["hostname"], program_name=match["program"], message=match["message"])
    for pattern, names in BUILTIN_FIELDS:
        found = re.search(pattern, event.message)
        if found:
            event.fields.update(dict(zip(names, found.groups(), strict=True)))
    for parent in (d for d in DECODERS if d.parent is None):
        if parent.program_name and re.search(parent.program_name, event.program_name):
            matched = True
        elif parent.prematch and re.search(parent.prematch, event.message):
            matched = True
        else:
            matched = False
        if not matched:
            continue
        event.decoder = parent.name
        for child in (d for d in DECODERS if d.parent == parent.name):
            if child.prematch and not re.search(child.prematch, event.message):
                continue
            found = re.search(child.regex, event.message) if child.regex else None
            if found:
                event.fields.update(dict(zip(child.order, found.groups(), strict=True)))
                break
        break
    return event


def option_matches(rule: Rule, event: Event, option: str, value: str) -> bool:
    """One option of a rule against a decoded event (time and weekday always pass)."""
    if option in ("time", "weekday", "description", "group", "same_source_ip"):
        return True
    if option == "decoded_as":
        return event.decoder == value
    if option == "hostname":
        return re.search(value, event.hostname) is not None
    if option == "program_name":
        return re.search(value, event.program_name) is not None
    if option == "regex":
        return re.search(value, event.message) is not None
    if option in ("dstport", "protocol", "action"):
        return re.search(value, event.fields.get(option, "")) is not None
    if option == "user":
        return re.search(value, event.fields.get("dstuser", "")) is not None
    if option == "srcip":
        address = event.fields.get("srcip")
        if address is None:
            return False
        negate = value.startswith("!")
        network = ipaddress.ip_network(value.lstrip("!"), strict=False)
        return (ipaddress.ip_address(address) in network) != negate
    if option == "field":
        return True  # file integrity fields: not exercised by log samples
    raise AssertionError(f"unhandled option {option} in rule {rule.id}")


def rule_matches(rule: Rule, event: Event) -> bool:
    return all(option_matches(rule, event, option, value) for option, values in rule.options.items() for value in values if option not in ("if_sid", "if_matched_sid"))


def evaluate(event: Event) -> int | None:
    """The deepest rule that matches: roots (decoded_as or a built-in parent) first, then children."""
    candidates = [r for r in RULES if "decoded_as" in r.options and rule_matches(r, event)]
    current: Rule | None = candidates[0] if candidates else None
    # Built-in parents (sshd, PAM) are assumed to have matched; our rules start below them.
    if current is None:
        roots = [
            r
            for r in RULES
            if r.values("if_sid")
            and all(int(v) in BUILTIN_PARENTS and re.search(BUILTIN_PARENTS[int(v)], event.message) for v in r.values("if_sid"))
            and rule_matches(r, event)
        ]
        current = roots[0] if roots else None
    while current is not None:
        children = [r for r in RULES if str(current.id) in r.values("if_sid") and rule_matches(r, event)]
        if not children:
            break
        current = children[0]
    return current.id if current else None


def samples() -> list[tuple[list[int], str]]:
    out: list[tuple[list[int], str]] = []
    for raw in (WAZUH / "samples.txt").read_text().splitlines():
        if not raw or raw.startswith("#"):
            continue
        rules, _, line = raw.partition("|")
        out.append(([int(x) for x in rules.split(",")], line))
    return out


@pytest.mark.parametrize(("wanted", "line"), samples(), ids=lambda v: v if isinstance(v, str) and len(v) < 12 else None)
def test_sample_fires_its_rule(wanted: list[int], line: str) -> None:
    event = decode(line)
    fired = evaluate(event)
    assert fired in wanted, f"{wanted} expected, {fired} fired; decoder {event.decoder}, fields {event.fields}"


def test_every_vendor_rule_has_a_sample() -> None:
    sampled = {rule for wanted, _ in samples() for rule in wanted}
    # Rules that need more than one log line, or a file integrity event, have no sample.
    exempt = {100100, 100104, 100200, 100204, 100205, 100300, 100304, 100305, 100306, 100310, 100313, 100314, 100410}
    missing = {r.id for r in RULES} - sampled - exempt
    assert not missing, f"no sample for rules {sorted(missing)}"


def test_agent_conf_files_are_well_formed() -> None:
    for path in (WAZUH / "agent-conf").glob("*.conf"):
        root = ET.fromstring(f"<root>{path.read_text()}</root>")
        assert root.findall("agent_config"), f"{path.name} has no <agent_config>"
    ET.fromstring(f"<root>{(WAZUH / 'ossec-extra.conf').read_text()}</root>")


def test_syslog_sources_cover_the_firewalls_and_ot_svc() -> None:
    text = (WAZUH / "ossec-extra.conf").read_text()
    for address in ("10.10.20.0/24", "10.40.10.1", "10.20.30.13"):
        assert address in text
