"""A router's routing lines as the `bgp` block of phenix's `vrouter` app.

`wangen/vyos.py` writes every router as VyOS `set` commands. The `vrouter` app
takes BGP, its BFD profiles, and its prefix lists, community lists, and route
maps as a `bgp` block whose keys do not depend on the kind of router
(GhostofGoes/sceptre-phenix#27). `split` moves those commands of a list into
the block, and returns the commands that stay in the `config` list: the
tunnels, the static and failover routes, the firewall, and NAT.

The block owns four parts of the configuration (`OWNED`). A command of one of
those parts that `split` does not know is an error, so that no command is
lost and none stays in `config` beside the block.
"""

from __future__ import annotations

import shlex
from typing import Any, Final

from wangen.model import WanError

#: The first words of the commands that the block holds.
OWNED: Final[tuple[tuple[str, ...], ...]] = (
    ("protocols", "bgp"),
    ("protocols", "bfd"),
    ("policy", "prefix-list"),
    ("policy", "community-list"),
    ("policy", "route-map"),
)

#: The neighbor's timers: the VyOS word, and the block's key.
TIMERS: Final[dict[str, str]] = {"keepalive": "keepalive", "holdtime": "holdtime", "connect": "connectRetry"}
#: A BFD profile's intervals: the VyOS word, and the block's key.
BFD_KEYS: Final[dict[str, str]] = {
    "transmit": "transmitInterval",
    "receive": "receiveInterval",
    "multiplier": "multiplier",
}


def owned(words: list[str]) -> bool:
    """Whether the block holds the command with these words after `set`."""
    return any(tuple(words[: len(prefix)]) == prefix for prefix in OWNED)


class _Block:
    """The block of one router while `split` reads its commands."""

    def __init__(self) -> None:
        self.speaker: dict[str, Any] = {}
        self.multipath: dict[str, Any] = {}
        self.networks: list[str] = []
        # Each by its address or its name, in the order of the first command.
        self.neighbors: dict[str, dict[str, Any]] = {}
        self.bfd_profiles: dict[str, dict[str, Any]] = {}
        # A list by its name, and a rule of the list by its number.
        self.lists: dict[str, dict[str, dict[int, dict[str, Any]]]] = {
            "prefix-list": {},
            "community-list": {},
            "route-map": {},
        }

    def take(self, words: list[str]) -> bool:
        """Put one command into the block. False for a command that the block does not know."""
        match words:
            case ["protocols", "bfd", "profile", name, "interval", key, value] if key in BFD_KEYS:
                self.bfd_profiles.setdefault(name, {"name": name})[BFD_KEYS[key]] = int(value)
            case ["protocols", "bgp", "system-as", value]:
                self.speaker["localAS"] = int(value)
            case ["protocols", "bgp", "parameters", "router-id", value]:
                self.speaker["routerID"] = value
            case ["protocols", "bgp", "parameters", "log-neighbor-changes"]:
                self.speaker["logNeighborChanges"] = True
            case ["protocols", "bgp", "parameters", "bestpath", "as-path", "multipath-relax"]:
                self.multipath["asPathRelax"] = True
            case ["protocols", "bgp", "address-family", "ipv4-unicast", "maximum-paths", kind, value] if kind in (
                "ebgp",
                "ibgp",
            ):
                self.multipath[kind] = int(value)
            case ["protocols", "bgp", "address-family", "ipv4-unicast", "network", prefix]:
                self.networks.append(prefix)
            case ["protocols", "bgp", "neighbor", address, *rest]:
                return self._neighbor(self.neighbors.setdefault(address, {"address": address}), rest)
            case ["policy", ("prefix-list" | "community-list" | "route-map") as kind, name, "rule", number, *rest]:
                rule = self.lists[kind].setdefault(name, {}).setdefault(int(number), {"rule": int(number)})
                return self._rule(kind, rule, rest)
            case _:
                return False
        return True

    @staticmethod
    def _neighbor(neighbor: dict[str, Any], words: list[str]) -> bool:
        family = ["address-family", "ipv4-unicast"]
        match words:
            case ["remote-as", value]:
                neighbor["remoteAS"] = int(value)
            case ["description", text]:
                neighbor["description"] = text
            case ["timers", key, value] if key in TIMERS:
                neighbor.setdefault("timers", {})[TIMERS[key]] = int(value)
            case ["bfd"]:
                neighbor["bfd"] = True
            case ["bfd", "profile", name]:
                neighbor["bfdProfile"] = name
            case [*head, "route-map", ("import" | "export") as direction, name] if head == family:
                neighbor.setdefault(direction, {})["routeMap"] = name
            case [*head, "prefix-list", ("import" | "export") as direction, name] if head == family:
                neighbor.setdefault(direction, {})["prefixList"] = name
            case [*head, "nexthop-self"] if head == family:
                neighbor["nexthopSelf"] = True
            case [*head, "soft-reconfiguration", "inbound"] if head == family:
                neighbor["softReconfigurationInbound"] = True
            case [*head, "default-originate"] if head == family:
                neighbor["defaultOriginate"] = True
            case [*head, "default-originate", "route-map", name] if head == family:
                neighbor["defaultOriginateRouteMap"] = name
            case _:
                return False
        return True

    @staticmethod
    def _rule(kind: str, rule: dict[str, Any], words: list[str]) -> bool:
        match (kind, words):
            case (_, ["action", action]):
                rule["action"] = action
            case ("prefix-list", ["prefix", prefix]):
                rule["prefix"] = prefix
            case ("prefix-list", [("ge" | "le") as key, value]):
                rule[key] = int(value)
            case ("community-list", ["regex", regex]):
                rule["regex"] = regex
            case ("route-map", ["match", "ip", "address", "prefix-list", name]):
                rule.setdefault("match", {})["prefixList"] = name
            case ("route-map", ["match", "community", "community-list", name]):
                rule.setdefault("match", {})["communityList"] = name
            case ("route-map", ["set", "local-preference", value]):
                rule.setdefault("set", {})["localPreference"] = int(value)
            case ("route-map", ["set", "community", "replace", community]):
                rule.setdefault("set", {})["community"] = community
            case ("route-map", ["set", "src", address]):
                rule.setdefault("set", {})["src"] = address
            case ("route-map", ["set", "metric", value]):
                rule.setdefault("set", {})["metric"] = int(value)
            case _:
                return False
        return True

    def _named(self, kind: str) -> list[dict[str, Any]]:
        return [
            {"name": name, "rules": [rules[number] for number in sorted(rules)]}
            for name, rules in self.lists[kind].items()
        ]

    def document(self) -> dict[str, Any]:
        """The block, with its keys in the order of phenix's documentation. No key is empty."""
        parts: dict[str, Any] = {
            "localAS": self.speaker.get("localAS"),
            "routerID": self.speaker.get("routerID"),
            "logNeighborChanges": self.speaker.get("logNeighborChanges"),
            "multipath": self.multipath,
            "networks": self.networks,
            "neighbors": list(self.neighbors.values()),
            "bfdProfiles": list(self.bfd_profiles.values()),
            "prefixLists": self._named("prefix-list"),
            "communityLists": self._named("community-list"),
            "routeMaps": self._named("route-map"),
        }
        return {key: value for key, value in parts.items() if value}


def split(lines: list[str]) -> tuple[dict[str, Any], list[str]]:
    """(The `bgp` block of a list of VyOS commands, the commands that stay in `config`).

    The block is empty for a list with no BGP. Raises WanError for a command of
    a part that the block owns when the block has no key for it.
    """
    block = _Block()
    rest: list[str] = []
    for line in lines:
        verb, *words = shlex.split(line)
        if not owned(words):
            rest.append(line)
        elif verb != "set" or not block.take(words):
            raise WanError(f"the bgp block of the vrouter app has no key for `{line}`")
    document = block.document()
    if document and "localAS" not in document:
        raise WanError("the commands configure BGP, a list, or a BFD profile, and give no `system-as`")
    return document, rest
