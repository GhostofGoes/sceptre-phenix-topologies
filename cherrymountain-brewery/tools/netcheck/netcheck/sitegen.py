"""Two apps of a site's scenario: the rulesets of its firewalls, and its SoH tests.

The rules are generated from the site's conduits (``netcheck.site``): for each
pair of hosts of an enforced row, one accept rule at each firewall on its path,
in the ruleset of the interface that the packet arrives on. Every ruleset
drops by default and starts with the stateful accept, as phenix's ``vrouter``
app renders it and as the ``rulesets`` check demands. A firewall that no
conduit crosses still gets a ruleset for each interface, with no rule to
accept anything new.

A conduit between two sites (``netcheck.fleet``) has one end here. Its rules
stand after those of the site's own conduits, at the firewalls of
``site.FleetPair.hops``, with the far host's address as it is at its own site.

This module computes the two apps and writes no file. ``sync.py`` writes
``phenix-configs/scenario-site-<code>.yml``: these two apps and the app blocks
that plantgen writes for the site. The site's run fails when the ``vrouter``
app of the committed file is not the one computed here, or when its ``soh``
app lacks a test computed here.
"""

from __future__ import annotations

from typing import Any

from netcheck import conduits, fleet
from netcheck.site import Site

#: SoH timing, as in the full scenario.
SOH_STARTUP_DELAY: str = "10m"
SOH_C2_TIMEOUT: str = "15m"
SOH_WAIT: str = "5m"
STATEFUL_ID: int = 5
RULE_STEP: int = 10
#: The cc test of a path that must stay blocked, as the full scenario has it.
BLOCKED_SCRIPT: str = """\
err=$(timeout 5 bash -c 'exec 3<>/dev/tcp/{address}/{port}' 2>&1); rc=$?
if [ "$rc" -eq 124 ] || printf '%s' "$err" | grep -qiE 'unreachable|no route'; then
  echo DROPPED
else
  echo "ALLOWED rc=$rc $err"
fi
"""


def file_name(code: str) -> str:
    return f"scenario-site-{code}.yml"


def ruleset_name(vlan: str) -> str:
    return f"FROM-{vlan}"


def _first_address(site: Site, host: str) -> str:
    return next(iter(site.addresses[host].values()))


def rulesets(site: Site) -> dict[str, dict[str, Any]]:
    """Router -> its ``acl`` block (``egress`` and ``rulesets``).

    A row whose sources are a whole zone gets one rule with the zone's subnet,
    when every host of that network at this site is a source. Any other
    source gets a rule of its own, with its address."""
    return _rulesets(site)[0]


def fleet_rules(site: Site) -> frozenset[str]:
    """The rules of the conduits between two sites, each as
    ``<router>:<ruleset>:<id>``. No flow inside the site's topology uses one:
    its other end is at another site (``checks.check_rule_coverage``)."""
    return _rulesets(site)[1]


def _rulesets(site: Site) -> tuple[dict[str, dict[str, Any]], frozenset[str]]:
    services: dict[str, tuple[conduits.Service, ...]] = {
        cd.id: cd.services for cd in conduits.CONDUITS
    }
    # (router, inbound VLAN) -> (row, destination, proto, port) -> sources.
    wanted: dict[tuple[str, str], dict[tuple[str, str, str, int], list[tuple[str, bool]]]] = {}
    for pair in site.pairs:
        for svc in services[pair.conduit]:
            for router, vlan in pair.hops:
                key = (pair.conduit, pair.dst, svc.proto, svc.port)
                wanted.setdefault((router, vlan), {}).setdefault(key, []).append(
                    (pair.src, pair.zone_src)
                )
    between_sites: dict[str, tuple[conduits.Service, ...]] = {
        row.id: row.services for row in fleet.FLEET_CONDUITS
    }
    far: set[str] = set()
    out: dict[str, dict[str, Any]] = {}
    for router in site.routers:
        egress: dict[str, str] = {}
        sets: list[dict[str, Any]] = []
        for number, vlan in enumerate(site.addresses[router]):
            rules: list[dict[str, Any]] = [
                {
                    "id": STATEFUL_ID,
                    "action": "accept",
                    "description": "Established and related connections",
                    "protocol": "all",
                    "stateful": True,
                }
            ]
            for (row, dst, proto, port), sources in wanted.get((router, vlan), {}).items():
                names: list[str] = list(dict.fromkeys(name for name, _ in sources))
                whole_zone: bool = (
                    all(zone for _, zone in sources)
                    and all(vlan in site.addresses[name] for name in names)
                    and set(names) == set(site.members.get(vlan, ()))
                )
                specs: list[tuple[str, str]] = (
                    [(vlan, site.reference.vlans[vlan].subnet)]
                    if whole_zone
                    else [(name, _source_address(site, name, vlan)) for name in names]
                )
                for label, address in specs:
                    rules.append(
                        {
                            "id": len(rules) * RULE_STEP,
                            "action": "accept",
                            "description": f"{row}: {label} to {dst} {proto}/{port}",
                            "protocol": proto,
                            "source": {"address": address},
                            "destination": {"address": _first_address(site, dst), "port": port},
                        }
                    )
            for pair in site.fleet:
                if (router, vlan) not in pair.hops:
                    continue
                for svc in between_sites[pair.conduit]:
                    far.add(f"{router}:{ruleset_name(vlan)}:{len(rules) * RULE_STEP}")
                    rules.append(
                        {
                            "id": len(rules) * RULE_STEP,
                            "action": "accept",
                            "description": f"{pair.conduit}: {pair.src} to {pair.dst} "
                            f"{svc.proto}/{svc.port}",
                            "protocol": svc.proto,
                            "source": {"address": pair.src_address},
                            "destination": {"address": pair.dst_address, "port": svc.port},
                        }
                    )
            egress[f"eth{number}"] = ruleset_name(vlan)
            sets.append(
                {
                    "name": ruleset_name(vlan),
                    "default": "drop",
                    "description": f"Traffic entering {router} from {vlan}: stateful, default deny",
                    "rules": rules,
                }
            )
        out[router] = {"egress": egress, "rulesets": sets}
    return out, frozenset(far)


def _source_address(site: Site, host: str, vlan: str) -> str:
    """The address that a packet of ``host`` carries when it arrives from ``vlan``:
    its address there, or its first address when a router is in between."""
    return site.addresses[host].get(vlan) or _first_address(site, host)


def soh(site: Site) -> dict[str, Any]:
    """The SoH tests that follow from the conduits: a probe for each one that
    answers, and a test for each path of the Front Range list that this site's
    firewalls block."""
    ref = site.reference
    first_nic: dict[str, str] = {n.name: n.nics[0].name for n in ref.nodes}
    probes: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for cd in ref.conduits:
        for probe in cd.probes:
            entry: dict[str, Any] = {
                "src": probe.src,
                "dst": f"{probe.dst}|{first_nic[probe.dst]}",
                "proto": probe.proto,
                "port": probe.port,
                "wait": SOH_WAIT,
            }
            if probe.packet:
                entry["udpPacketBase64"] = probe.packet
            probes.setdefault((probe.src, probe.dst, probe.proto, probe.port), entry)
    custom: dict[str, list[dict[str, Any]]] = {}
    for blocked in ref.blocked:
        custom.setdefault(blocked.src, []).append(
            {
                "name": f"blocked-{blocked.dst}-{blocked.port}",
                "executor": "bash",
                "testScript": BLOCKED_SCRIPT.format(
                    address=_first_address(site, blocked.dst), port=blocked.port
                ),
                "testStdout": "DROPPED",
            }
        )
    metadata: dict[str, Any] = {
        "startupDelay": SOH_STARTUP_DELAY,
        "c2Timeout": SOH_C2_TIMEOUT,
        "testReachability": "off",
        "skipHosts": list(site.routers),
        "testCustomReachability": list(probes.values()),
    }
    if custom:
        metadata["hostCustomTests"] = custom
    return {"name": "soh", "metadata": metadata}


def vrouter(site: Site) -> dict[str, Any]:
    """The ``vrouter`` app: the rulesets of every firewall of the site."""
    return {
        "name": "vrouter",
        "hosts": [
            {"hostname": router, "metadata": {"acl": acl}}
            for router, acl in rulesets(site).items()
        ],
    }


def apps(site: Site) -> dict[str, dict[str, Any]]:
    """The two apps of the site's scenario that follow from the conduits."""
    return {"vrouter": vrouter(site), "soh": soh(site)}


def rule_counts(site: Site) -> dict[str, int]:
    """Router -> the number of its explicit rules."""
    return {
        router: sum(len(rs["rules"]) - 1 for rs in acl["rulesets"])
        for router, acl in rulesets(site).items()
    }
