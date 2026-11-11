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

from soc_paths import INJECTS  # noqa: F401 (the fixtures are used by name)

WAZUH: Path = INJECTS / "wazuh"
#: Wazuh's own rules that ours extend.
BUILTIN_RULES: frozenset[int] = frozenset({553, 554, 5501, 5715})
#: Rule options the evaluator understands, and the ones that are always true here
#: (time and weekday depend on when the test runs).
KNOWN_OPTIONS: frozenset[str] = frozenset(
    {
        "if_sid", "if_matched_sid", "if_matched_group", "decoded_as", "hostname", "program_name", "dstport",
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
    #: Set on a rule that counts earlier events (with if_matched_sid or if_matched_group).
    frequency: int | None = None

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
        frequency = node.get("frequency")
        out.append(
            Rule(
                id=int(node.get("id", "0")), level=int(node.get("level", "0")), options=options, attrs=attrs,
                frequency=int(frequency) if frequency else None,
            )
        )
    return out


DECODERS: list[Decoder] = load_decoders()
RULES: list[Rule] = load_rules()
RULES_BY_ID: dict[int, Rule] = {r.id: r for r in RULES}


#: Wazuh's own parent decoders that ours extend, with their program_name pattern.
#: A child of one is reported (and matched by decoded_as) under the parent's name.
BUILTIN_DECODERS: dict[str, str] = {"kernel": r"^kernel"}
#: The children Wazuh itself has under `kernel` (ruleset/decoders/0140-kernel_decoders.xml
#: of 4.14.10), in file order: name, the type it gives the event, and its prematch as a
#: Python pattern (OS_Regex: `.` is a literal dot, `\.` any character, `\p` punctuation).
#: Wazuh loads its own decoders first, so one of these takes a kernel line before the
#: range's cm-fw-drop does. An event of type firewall is then only offered to rules of
#: the firewall category (analysisd.c: "Categories must match"), which the range's
#: rules are not, and whichever child takes the line, cm-fw-drop's fields are never
#: extracted. No kernel line of the range may match any of them.
BUILTIN_KERNEL_CHILDREN: tuple[tuple[str, str, str], ...] = (
    ("iptables-1", "firewall", r"^\[\d+\.\d+\] \S+ IN="),
    ("iptables-2", "firewall", r"^\S+ IN="),
    ("shorewall-1", "firewall", r"^Shorewall:\S+:"),
    ("shorewall-2", "firewall", r"^[^\w\s]\S+[^\w\s] Shorewall:\S+:"),
    ("iptables-OpenWRT", "firewall", r"^\[\d+\.\d+\] \S*\(.*IN="),
    ("apparmor", "syslog", r" apparmor="),
    ("mptscsih", "syslog", r"^\[\s\d+\.\d+\] mptscsih: "),
    ("mptbase", "syslog", r"^\[\s\d+\.\d+\] mptbase: "),
    ("usb", "syslog", r"^usb|^\[\s*\S+\] usb"),
    ("ufw", "firewall", r"^\[\s*\d+\.\d+\] \[.*\] IN="),
)


def test_decoders_are_well_formed() -> None:
    names = [d.name for d in DECODERS]
    # One name may repeat only as sibling decoders (the same parent, in a row).
    for name in set(names):
        parents = {d.parent for d in DECODERS if d.name == name}
        assert len(parents) == 1, f"decoder {name} is defined under different parents"
    for decoder in DECODERS:
        if decoder.parent:
            assert decoder.parent in names or decoder.parent in BUILTIN_DECODERS, f"{decoder.name}: unknown parent {decoder.parent}"
        for pattern in (decoder.program_name, decoder.prematch, decoder.regex):
            if pattern:
                re.compile(pattern)
        if decoder.regex:
            groups = re.compile(decoder.regex).groups
            assert groups == len(decoder.order), f"{decoder.name}: {groups} groups but {len(decoder.order)} fields in <order>"


def test_rules_are_well_formed() -> None:
    ids = [r.id for r in RULES]
    assert len(ids) == len(set(ids)), "rule ids must be unique"
    decoder_names = {d.name for d in DECODERS if d.parent is None} | set(BUILTIN_DECODERS)
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
        assert rule.options.keys() & {"if_sid", "decoded_as", "if_matched_sid", "if_matched_group"}, f"rule {rule.id} has no parent or decoder"
        for group in rule.values("if_matched_group"):
            assert any(group in "".join(r.values("group")) for r in RULES if r.id != rule.id), f"rule {rule.id}: no rule is in group {group}"
        counts = bool(rule.options.keys() & {"if_matched_sid", "if_matched_group"})
        assert counts == (rule.frequency is not None), f"rule {rule.id}: frequency goes with if_matched_sid or if_matched_group"
        for option in ("regex", "field"):
            for value in rule.values(option):
                if rule.attrs.get(option, {}).get("type") == "pcre2":
                    re.compile(value)
        for field in ("hostname", "program_name", "dstport", "protocol", "user"):
            for value in rule.values(field):
                re.compile(value.replace("$|^", "|^"))  # alternations of anchored words
        # Wazuh compares <action> as a plain string unless it has a type: anchors
        # there never match (found with wazuh-logtest on 4.14.1).
        for value in rule.values("action"):
            if rule.attrs.get("action", {}).get("type") == "pcre2":
                re.compile(value)
            else:
                assert not set(value) & set("^$|*"), f"rule {rule.id}: <action> {value} is not a plain string"
        # Wazuh 4 negates an address with negate="yes"; a leading ! does not work.
        for value in rule.values("srcip"):
            assert not value.startswith("!"), f"rule {rule.id}: use negate=\"yes\", not !"


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
    #: The built-in child that took the line before the range's own (None: none did).
    taken_by: str | None = None
    category: str = "syslog"


def decode(line: str) -> Event:
    match = SYSLOG.match(line)
    assert match, f"not a syslog line: {line}"
    event = Event(hostname=match["hostname"], program_name=match["program"], message=match["message"])
    for pattern, names in BUILTIN_FIELDS:
        found = re.search(pattern, event.message)
        if found:
            event.fields.update(dict(zip(names, found.groups(), strict=True)))
    parents: list[tuple[str, str | None, str | None]] = [
        (name, pattern, None) for name, pattern in BUILTIN_DECODERS.items()
    ] + [(d.name, d.program_name, d.prematch) for d in DECODERS if d.parent is None]
    for name, program, prematch in parents:
        if program and re.search(program, event.program_name):
            matched = True
        elif prematch and re.search(prematch, event.message):
            matched = True
        else:
            matched = False
        if not matched:
            continue
        # The event carries the parent's name. Children are tried in order; the
        # first whose prematch matches wins, and its later siblings (the same
        # name, no prematch) add their fields.
        event.decoder = name
        if name == "kernel":
            for child_name, category, pattern in BUILTIN_KERNEL_CHILDREN:
                if re.search(pattern, event.message):
                    event.taken_by, event.category = child_name, category
                    break
            if event.taken_by:
                break
        children = [d for d in DECODERS if d.parent == name]
        for i, child in enumerate(children):
            if child.prematch and not re.search(child.prematch, event.message):
                continue
            for sibling in [child, *(c for c in children[i + 1 :] if c.name == child.name and not c.prematch)]:
                found = re.search(sibling.regex, event.message) if sibling.regex else None
                if found:
                    event.fields.update(
                        {k: v for k, v in zip(sibling.order, found.groups(), strict=True) if v is not None}
                    )
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
    if option in ("dstport", "protocol"):
        return re.search(value, event.fields.get(option, "")) is not None
    if option == "action":
        if rule.attrs.get("action", {}).get("type") == "pcre2":
            return re.search(value, event.fields.get("action", "")) is not None
        return event.fields.get("action") == value
    if option == "user":
        return re.search(value, event.fields.get("dstuser", "")) is not None
    if option == "srcip":
        address = event.fields.get("srcip")
        if address is None:
            return False
        negate = rule.attrs.get("srcip", {}).get("negate") == "yes"
        network = ipaddress.ip_network(value, strict=False)
        return (ipaddress.ip_address(address) in network) != negate
    if option == "field":
        return True  # file integrity fields: not exercised by log samples
    raise AssertionError(f"unhandled option {option} in rule {rule.id}")


def rule_matches(rule: Rule, event: Event) -> bool:
    return all(option_matches(rule, event, option, value) for option, values in rule.options.items() for value in values if option not in ("if_sid", "if_matched_sid", "if_matched_group"))


def evaluate(event: Event) -> int | None:
    """The deepest rule that matches: roots (decoded_as or a built-in parent) first, then children.
    A frequency rule (if_matched_sid, if_matched_group) needs earlier events and never ends a single line."""
    if event.category != "syslog":
        return None  # the range's rules have no <category>, so they are syslog rules
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
        # Wazuh tries the children of a rule highest level first and stops at the
        # first that matches (checked with wazuh-logtest on 4.14.1).
        children = sorted(
            (r for r in RULES if str(current.id) in r.values("if_sid") and r.frequency is None and rule_matches(r, event)),
            key=lambda r: -r.level,
        )
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


def test_no_builtin_decoder_takes_a_firewall_line() -> None:
    """Every kernel line of the samples reaches cm-fw-drop, at any uptime: the
    kernel pads its timestamp with blanks under 10,000 s and not above."""
    kernel_lines = [line for _wanted, line in samples() if " kernel: " in line]
    assert any(re.search(r"kernel: \[\d{5}\.", line) for line in kernel_lines), "no sample with a five-digit uptime"
    for line in kernel_lines:
        event = decode(line)
        assert event.taken_by is None, f"Wazuh's {event.taken_by} decoder takes: {line}"
        assert {"srcip", "dstip", "protocol"} <= set(event.fields), line


def test_a_prefix_that_ends_in_a_space_is_lost_to_wazuhs_iptables_decoder() -> None:
    """Why dmz-vpn's nftables prefix is "[cm-wg-drop]" with no space after it: the
    plain word and a space is what Wazuh's iptables decoder looks for, once the
    uptime has five digits, and rule 100105 then never sees the line."""
    line = next(line for wanted, line in samples() if wanted == [100105] and re.search(r"\[\d{5}\.", line))
    spaced = line.replace("[cm-wg-drop]IN=", "cm-wg-drop IN=")
    assert spaced != line
    event = decode(spaced)
    assert (event.taken_by, event.category) == ("iptables-1", "firewall")
    assert evaluate(event) is None
    early = re.sub(r"\[\d{5}\.", "[   55.", spaced)
    assert evaluate(decode(early)) == 100105  # which is why a sample from the first minutes passed
    nft = (INJECTS / "wireguard" / "nftables-wireguard.nft").read_text()
    assert 'log prefix "[cm-wg-drop]"' in nft


def test_the_scan_rule_counts_every_firewall_drop() -> None:
    """Rule 100104 counts by group: if_matched_sid 100100 counts only the events
    whose last rule is 100100, and a drop at fw-edge or on a control-system port
    ends at a child of it. Wazuh hangs a rule with if_matched_group and no if_sid
    under every rule of the group that is defined before it."""
    scan = RULES_BY_ID[100104]
    assert scan.values("if_matched_group") == ["cm_firewall"]
    assert not scan.values("if_matched_sid") and not scan.values("if_sid")
    assert scan.frequency == 10 and "same_source_ip" in scan.options
    order = [r.id for r in RULES]
    counted = [r for r in RULES if "cm_firewall" in "".join(r.values("group")) and r.id != 100104]
    assert {r.id for r in counted} >= {100100, 100101, 100102, 100103}
    assert all(order.index(r.id) < order.index(100104) for r in counted), "a cm_firewall rule is defined after 100104"


AGENT_GROUPS: dict[str, tuple[str, ...]] = {
    "ops": ("scada-fuxa", "historian", "ews", "ot-svc"),
    "hmi": ("hmi-brewhouse", "hmi-packaging"),
    "bas": ("bas-fuxa",),
    "dmz": ("dmz-vpn", "dmz-jump"),
    "soc": ("malcolm", "soc-analyst"),
}


def agent_localfiles(group: str, agent: str) -> set[str]:
    """The log files the group's agent.conf gives one agent (a block with no name is for all)."""
    root = ET.fromstring(f"<root>{(WAZUH / 'agent-conf' / f'{group}.conf').read_text()}</root>")
    return {
        (lf.findtext("location") or "").strip()
        for block in root.findall("agent_config")
        if block.get("name") in (None, agent)
        for lf in block.findall("localfile")
    }


def test_agent_conf_files_are_well_formed() -> None:
    for path in (WAZUH / "agent-conf").glob("*.conf"):
        root = ET.fromstring(f"<root>{path.read_text()}</root>")
        assert root.findall("agent_config"), f"{path.name} has no <agent_config>"
    ET.fromstring(f"<root>{(WAZUH / 'ossec-extra.conf').read_text()}</root>")
    assert {p.stem for p in (WAZUH / "agent-conf").glob("*.conf")} == set(AGENT_GROUPS)


def test_every_agent_sends_its_authentication_log() -> None:
    """The packaged agent configuration reads neither auth.log nor the journal, so
    without this no sign-in on a host reaches the manager."""
    for group, agents in AGENT_GROUPS.items():
        for agent in agents:
            assert "/var/log/auth.log" in agent_localfiles(group, agent), agent
    # dmz-vpn's kernel log is in syslog too: both would alert twice for one drop.
    assert "/var/log/syslog" not in agent_localfiles("dmz", "dmz-vpn")
    assert "/var/log/kern.log" in agent_localfiles("dmz", "dmz-vpn")
    assert "/var/log/syslog" in agent_localfiles("ops", "ews")


def test_syslog_is_taken_from_the_three_senders_only() -> None:
    """fw-edge's DMZ address, fw-ot's SOC address, and ot-svc: no network."""
    root = ET.fromstring(f"<root>{(WAZUH / 'ossec-extra.conf').read_text()}</root>")
    allowed = {e.text for e in root.iter("allowed-ips")}
    assert allowed == {"10.10.20.1", "10.40.10.1", "10.20.30.13"}
