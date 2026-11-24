"""A walk over everything that `phenix workflow apply` takes for one site:
the workflow, the topology file, the scenario, and each injected file.

The walk proves four things. Every injected file exists. The scenario names
only hosts of the topology. The workflow gives an ID to every VLAN of the
topology. And at `fc`, `ep`, and `cs`, no file holds a Front Range address or
the plain name of a host where the site's own is needed. `KEPT` lists what the
last rule leaves alone, each with its reason.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml
from conftest import ROOT

from netcheck import plan

CODES: list[str] = ["fr", "ep", "cs", "fc"]
#: The sites that have no address of the Front Range site.
OTHER_SITES: list[str] = ["ep", "cs", "fc"]
PREFIX: str = "/phenix/injects/${BRANCH_NAME}/"
#: The private blocks of the Front Range site (wan/wan.yaml, `site_blocks`) and
#: its MGMT network.
FRONT_RANGE: re.Pattern[str] = re.compile(r"(?<![\d.])(?:10\.(?:10|20|30|40)|172\.16)\.\d+\.\d+")
#: The hosts that the fleet has once, on the internet. A site names them as they are.
SHARED_HOSTS: frozenset[str] = frozenset(
    {"isp-rtr", "inet-traffic", "util-rtr", "utility-derms", "vendor-ws"}
)
#: The plain names of the hosts that each site has its own of.
PLAIN_NAMES: list[str] = sorted(
    (n.name for n in plan.NODES if n.name not in SHARED_HOSTS), key=len, reverse=True
)
#: A plain name that stands alone. It does not match inside a longer name
#: (`ep-ot-svc`, `scada-fuxa-project.json`) or a path (`bbmd/ot-svc.env`).
PLAIN_NAME: re.Pattern[str] = re.compile(
    r"(?<![A-Za-z0-9_./-])(" + "|".join(re.escape(n) for n in PLAIN_NAMES) + r")(?![A-Za-z0-9_-])"
)


class Kept(NamedTuple):
    """Names or addresses that one file keeps at every site, and why."""

    #: The file: a path under phenix-injects, or `scenario` for the site's scenario.
    path: str
    #: The plain hostnames and Front Range addresses that the file keeps.
    what: frozenset[str]
    why: str


WAZUH: str = "10.40.10.11"
KEPT: tuple[Kept, ...] = (
    Kept(
        "vrouter/cm-vyos-config.sh",
        frozenset({"fw-edge", "fw-ot"}),
        "the script's names of the two firewall roles: the site's copy drops the site's "
        "prefix from the hostname before it compares",
    ),
    Kept(
        "vrouter/cm-vyos-config.sh",
        frozenset({WAZUH}),
        "the syslog target: the fleet has one Wazuh, at the head office",
    ),
    Kept(
        "ot-svc/rsyslog-relay.conf",
        frozenset({WAZUH}),
        "the relay's target: the fleet has one Wazuh, at the head office",
    ),
    Kept(
        "ot-svc/60-ot-svc.sh",
        frozenset({"ot-svc"}),
        "a marker text that the hook writes into chrony.conf, not a host",
    ),
    Kept(
        "bbmd/cm_bbmd.py",
        frozenset({"10.20.30.13", "10.30.10.5", "bas-bbmd", "fw-ot", "ot-svc"}),
        "the docstring's example: the service reads its addresses from "
        "/etc/default/cm-bbmd, which is the site's copy",
    ),
    Kept(
        "ews/cm_plc_tool.py",
        frozenset({"10.20.10.12", "ews", "plc-cellar"}),
        "the docstring's usage examples: the tool takes the PLC and its address as arguments",
    ),
    Kept(
        "openplc/full/plc-cellar.zip",
        frozenset({"10.20.12.10", "rio-cellar"}),
        "not right yet, and left for a later slice: conf/modbus_master.json in the ZIP names "
        "the Front Range remote I/O. plc/common/build_zip.sh builds the ZIP with the PLC "
        "toolchain, and a site needs a build of its own",
    ),
    Kept(
        "scenario",
        frozenset({"sim-ctl", "historian", "ews", "pv-inverter", "bess-pcs", "gen-ats"}),
        "a `type` of the cherrymountain app or of an ot-sim device, and the device names "
        "of the power model: these are kinds and HELICS names, which no site renames",
    ),
)
#: A time source that a site names although its topology does not have the host.
OUTSIDE_SOURCES: dict[str, str] = {
    "inet-traffic": "the edge and the office PC follow the internet's time server. The "
    "source carries the server's address in the fleet, which phenix's ntp app takes as it is",
}
#: The files that `fr` injects as they are, with the plain names of its hosts.
#: `fr` keeps the Front Range addresses, so only the names are wrong there.
FRONT_RANGE_LATER: dict[str, str] = {
    "malcolm/": "the Suricata rules and the Zeek script name hosts in messages and comments; "
    "the detection content is the head office's and moves with the fleet's SOC",
    "wazuh/": "the agent groups, decoders, and rules match on plain hostnames; they need "
    "the fleet's names of every site, not only fr's",
    "wireguard/": "the vendor's tunnel ends at dmz-vpn; its scripts name dmz-vpn and dmz-jump",
    "guacamole/cm_vendor_sql.py": "the docstring says where the script is installed",
}
#: What the scenario of `fr` keeps besides `KEPT`, and why.
FRONT_RANGE_KEPT: dict[str, str] = {
    "wazuh": "the `wazuh` key of the cherrymountain app's metadata, not a host",
}
#: The kinds of host entry in the soh app that are maps from a hostname.
SOH_HOST_MAPS: tuple[str, ...] = (
    "hostProcesses",
    "hostListeners",
    "hostServices",
    "hostFiles",
    "hostFilesAbsent",
    "dockerContainers",
    "hostCustomTests",
)


def _load(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc


def workflow(code: str, root: Path = ROOT) -> dict[str, Any]:
    spec: dict[str, Any] = _load(root / f"phenix-site-{code}.yml")["spec"]
    return spec


def topology(code: str, root: Path = ROOT) -> dict[str, Any]:
    return _load(root / "phenix-configs" / f"topology-site-{code}.yml")


def scenario(code: str, root: Path = ROOT) -> dict[str, Any]:
    return _load(root / "phenix-configs" / f"scenario-site-{code}.yml")


def hostnames(code: str, root: Path = ROOT) -> set[str]:
    return {node["general"]["hostname"] for node in topology(code, root)["spec"]["nodes"]}


def injected(code: str, root: Path = ROOT) -> dict[str, list[str]]:
    """Path under phenix-injects -> the nodes that inject it."""
    found: dict[str, list[str]] = {}
    for node in topology(code, root)["spec"]["nodes"]:
        for injection in node.get("injections", []):
            source: str = injection["src"]
            assert source.startswith(PREFIX), source
            found.setdefault(source.removeprefix(PREFIX), []).append(node["general"]["hostname"])
    return found


def texts(path: Path) -> dict[str, str]:
    """The text that a file holds: its own, or that of each member of a ZIP.
    A picture has none."""
    data: bytes = path.read_bytes()
    found: dict[str, str] = {}
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                try:
                    found[name] = archive.read(name).decode("utf-8")
                except UnicodeDecodeError:
                    continue
        return found
    try:
        return {"": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {}


def foreign(text: str) -> set[str]:
    """The Front Range addresses and plain hostnames in the lines of a file
    that a program reads. A comment line explains, and no program reads it."""
    found: set[str] = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        found.update(FRONT_RANGE.findall(line))
        found.update(PLAIN_NAME.findall(line))
    return found


def site_files(code: str, root: Path = ROOT) -> dict[str, Path]:
    """Every file of the site's set, by the name that `KEPT` uses."""
    files: dict[str, Path] = {
        "workflow": root / f"phenix-site-{code}.yml",
        "topology": root / "phenix-configs" / f"topology-site-{code}.yml",
        "scenario": root / "phenix-configs" / f"scenario-site-{code}.yml",
    }
    own: str = f"fleet/{code}/"
    for path in injected(code, root):
        files[path.removeprefix(own)] = root / "phenix-injects" / path
    return files


def findings(code: str, root: Path = ROOT) -> dict[str, set[str]]:
    """File -> what it holds that is not the site's own."""
    out: dict[str, set[str]] = {}
    for name, path in site_files(code, root).items():
        found: set[str] = set()
        for text in texts(path).values():
            found |= foreign(text)
        if found:
            out[name] = found
    return out


def kept(name: str) -> set[str]:
    return {item for entry in KEPT if entry.path == name for item in entry.what}


def named_hosts(doc: Any) -> set[str]:
    """Every value of a `hostname` key in a config, without its `|interface`."""
    found: set[str] = set()
    if isinstance(doc, dict):
        for key, value in doc.items():
            if key == "hostname" and isinstance(value, str):
                found.add(value.split("|")[0])
            else:
                found |= named_hosts(value)
    elif isinstance(doc, list):
        for item in doc:
            found |= named_hosts(item)
    return found


# --- the walk ------------------------------------------------------------------


@pytest.mark.parametrize("code", CODES)
def test_a_workflow_names_the_sites_topology_and_scenario(code: str) -> None:
    spec: dict[str, Any] = workflow(code)
    name: str = f"cherrymountain-site-{code}"
    assert spec["topology"] == topology(code)["metadata"]["name"] == name
    assert spec["scenario"] == scenario(code)["metadata"]["name"] == name
    assert scenario(code)["metadata"]["annotations"]["topology"] == name


@pytest.mark.parametrize("code", CODES)
def test_the_workflow_gives_an_id_to_every_vlan_of_the_topology(code: str) -> None:
    spec: dict[str, Any] = workflow(code)
    used: set[str] = {
        leg["vlan"]
        for node in topology(code)["spec"]["nodes"]
        for leg in node["network"]["interfaces"]
    }
    assert used and used == set(spec["vlans"])
    ids: list[int] = list(spec["vlans"].values())
    assert len(ids) == len(set(ids))
    assert all(spec["vlanRange"]["min"] <= n < spec["vlanRange"]["max"] for n in ids)


@pytest.mark.parametrize("code", CODES)
def test_every_injected_file_exists(code: str) -> None:
    sources: dict[str, list[str]] = injected(code)
    assert sources
    for path, nodes in sources.items():
        assert (ROOT / "phenix-injects" / path).is_file(), (path, nodes)


def test_the_sets_of_the_four_sites_differ_in_size() -> None:
    """Injected files: the pub has five, and the head office has them all."""
    assert {code: len(injected(code)) for code in CODES} == {
        "fr": 76,
        "ep": 29,
        "cs": 20,
        "fc": 5,
    }
    own: dict[str, int] = {
        code: sum(1 for path in injected(code) if path.startswith(f"fleet/{code}/"))
        for code in CODES
    }
    assert own == {"fr": 8, "ep": 8, "cs": 6, "fc": 1}


@pytest.mark.parametrize("code", CODES)
def test_the_scenario_names_only_hosts_of_the_topology(code: str) -> None:
    doc: dict[str, Any] = scenario(code)
    nodes: set[str] = hostnames(code)
    apps: dict[str, dict[str, Any]] = {app["name"]: app for app in doc["spec"]["apps"]}
    assert list(apps) == [
        "startup",
        "ot-sim",
        "cherrymountain",
        "helics",
        "ntp",
        "vrouter",
        "soh",
    ]
    named: set[str] = named_hosts(doc)
    soh: dict[str, Any] = apps["soh"]["metadata"]
    named |= set(soh["skipHosts"])
    for key in SOH_HOST_MAPS:
        named |= set(soh.get(key, {}))
    for probe in soh["testCustomReachability"]:
        named |= {probe["src"], probe["dst"].split("|")[0]}
    named.add(apps["helics"]["metadata"]["broker"]["root"].split("|")[0])
    assert len(named) > 10
    assert named - nodes == set(OUTSIDE_SOURCES)
    # The ntp app runs in app mode, so it lists every node.
    assert {host["hostname"] for host in apps["ntp"]["hosts"]} == nodes


@pytest.mark.parametrize("code", OTHER_SITES)
def test_no_file_holds_a_front_range_address_or_a_plain_hostname(code: str) -> None:
    left: dict[str, set[str]] = {
        name: found - kept(name) for name, found in findings(code).items() if found - kept(name)
    }
    assert left == {}


def test_every_kept_name_and_address_is_still_there() -> None:
    """An entry of `KEPT` that no file needs any more must go."""
    seen: dict[str, set[str]] = {}
    for code in OTHER_SITES:
        for name, found in findings(code).items():
            seen.setdefault(name, set()).update(found)
    for entry in KEPT:
        assert entry.why
        assert entry.what <= seen.get(entry.path, set()), entry.path


def test_front_range_keeps_plain_names_only_in_the_files_left_for_later() -> None:
    """`fr` keeps its addresses. Its own copies are clean, and the files that
    still name a host plainly are those of `FRONT_RANGE_LATER`."""
    plain: dict[str, set[str]] = {}
    for name, path in site_files("fr").items():
        names: set[str] = set()
        for text in texts(path).values():
            names |= {item for item in foreign(text) if not FRONT_RANGE.fullmatch(item)}
        names -= kept(name) | (set(FRONT_RANGE_KEPT) if name == "scenario" else set())
        if names:
            plain[name] = names
    later: set[str] = {
        prefix for prefix in FRONT_RANGE_LATER for name in plain if name.startswith(prefix)
    }
    assert later == set(FRONT_RANGE_LATER)
    assert [name for name in plain if not name.startswith(tuple(FRONT_RANGE_LATER))] == []


def test_the_walk_reads_what_a_program_reads() -> None:
    text: str = "\n".join(
        [
            "# ot-svc at 10.20.30.13 is a comment",
            "server 10.20.30.13 ep-ot-svc",
            "  ntp ot-svc  # the plain name",
            "path bbmd/ot-svc.env scada-fuxa-project.json 110.20.30.13 10.22.30.13",
            "mgmt 172.16.10.1 172.18.10.1 inet-traffic",
        ]
    )
    assert foreign(text) == {"10.20.30.13", "ot-svc", "172.16.10.1"}


def test_the_walk_finds_a_file_that_is_not_the_sites_own(tree: Path) -> None:
    """A node that injects the single site's file again is found by name."""
    path: Path = tree / "phenix-configs" / "topology-site-fc.yml"
    own: str = "}/fleet/fc/vrouter/cm-vyos-config.sh"
    text: str = path.read_text(encoding="utf-8")
    assert text.count(own) == 1
    path.write_text(text.replace(own, "}/vrouter/cm-vyos-config.sh"), encoding="utf-8")
    found: set[str] = findings("fc", tree)["vrouter/cm-vyos-config.sh"]
    assert found - kept("vrouter/cm-vyos-config.sh") == {"10.20.30.13"}
    zone: Path = tree / "phenix-injects" / "fleet" / "ep" / "ot-svc" / "plant.hosts"
    zone.write_text(zone.read_text(encoding="utf-8") + "10.22.30.13 ot-svc\n", encoding="utf-8")
    assert findings("ep", tree)["ot-svc/plant.hosts"] == {"ot-svc"}


# --- the vendor's access ---------------------------------------------------------

#: The sites with the `remote` module of plant/sites.yaml: where the vendor's
#: tunnel ends, and where the vendor's technicians have accounts.
VENDOR_SITES: list[str] = ["fr"]
#: The file with the technicians' accounts, passwords, and TOTP keys.
VENDOR_SECRETS: Path = ROOT / "phenix-injects" / "guacamole" / "ridgeline.env"
#: What makes an injection the vendor's: a word in its source, its target, or
#: its description.
VENDOR_WORDS: re.Pattern[str] = re.compile(r"vendor|ridgeline|wireguard", re.IGNORECASE)


def vendor_values() -> set[str]:
    """Every account name, password, and key of the vendor's technicians."""
    values: set[str] = set()
    for line in VENDOR_SECRETS.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#") and not value.isdigit():
            values.add(value.strip())
    return values


@pytest.mark.parametrize("code", [c for c in CODES if c not in VENDOR_SITES])
def test_no_vendor_file_account_or_secret_reaches_a_site_without_the_vendors_access(
    code: str,
) -> None:
    """PLANS.md 4.1 and 4.6: the vendor reaches the head office and no other
    site. A jump host at another site is the company's own."""
    values: set[str] = vendor_values()
    assert len(values) >= 8
    for node in topology(code)["spec"]["nodes"]:
        for injection in node.get("injections", []):
            words: str = " ".join(
                (injection["src"], injection["dst"], injection.get("description", ""))
            )
            assert not VENDOR_WORDS.search(words), (node["general"]["hostname"], words)
    for name, path in site_files(code).items():
        for text in texts(path).values():
            assert not [value for value in values if value in text], name


def test_the_head_office_keeps_the_vendors_access() -> None:
    sources: dict[str, list[str]] = injected("fr")
    assert sources["guacamole/ridgeline.env"] == ["fr-ews", "fr-dmz-jump"]
    assert sources["guacamole/cm_vendor_sql.py"] == ["fr-dmz-jump"]
    assert sources["ews/70-ews-vendor.sh"] == ["fr-ews"]
    # A site's own jump host is still a jump host: Guacamole, without the vendor.
    assert "guacamole/docker-compose.yml" in injected("ep")
    assert "ews/cm_plc_tool.py" in injected("ep") and "ews/cm_plc_tool.py" in injected("cs")


# --- the address that inet-traffic leaves -----------------------------------------

#: The single site's address of inet-traffic. In the fleet it is the uplink of
#: the pub's edge (PLANS.md, section 3): a reference that is left would send
#: traffic for the internet's server to the pub.
OLD_INTERNET_SERVER: re.Pattern[str] = re.compile(r"(?<![\d.])203\.0\.113\.10(?![\d])")
#: Where the address may stand: as the pub's own uplink.
PUB_UPLINK: dict[str, str] = {
    "topology": "the address of fc-fw-edge on its access link to ShadeCast",
    "scenario": "the same address, in the rulesets and tests of fc-fw-edge",
}


@pytest.mark.parametrize("code", CODES)
def test_no_file_keeps_the_old_address_of_the_internets_server(code: str) -> None:
    found: set[str] = set()
    for name, path in site_files(code).items():
        for text in texts(path).values():
            lines: list[str] = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
            if any(OLD_INTERNET_SERVER.search(line) for line in lines):
                found.add(name)
    later: set[str] = {name for name in found if name.startswith(tuple(FRONT_RANGE_LATER))}
    allowed: set[str] = set(PUB_UPLINK) if code == "fc" else set()
    assert found - later - allowed == set()
    if code == "fc":
        legs: list[str] = [
            leg["address"]
            for node in topology("fc")["spec"]["nodes"]
            for leg in node["network"]["interfaces"]
            if OLD_INTERNET_SERVER.fullmatch(leg["address"])
        ]
        assert legs == ["203.0.113.10"]
