"""Validation of the cherrymountain app's metadata and of plant.yaml against the
topology.

Follows the sceptre app in sceptre-phenix-apps: problems are collected, not
raised one at a time, and reported as one block, fatal ones first. Unlike that
app, a message never echoes a value: it names the app, the host, and the field
(metadata can hold passwords, and the same rule keeps addresses out of logs).

Two severities:

    fatal    the experiment cannot be provisioned; configure fails.
    warning  provisioned anyway, but probably a mistake (an unknown key, a PLC
             in plant.yaml that the app does not provision).
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from typing import Any, ClassVar, Final

from phenix_apps.common.error import AppError
from phenix_apps.common.logger import logger
from pydantic import BaseModel, ConfigDict, ValidationError

from cherrymountain_apps.cherrymountain.metadata import (
    HOST_TYPES,
    AppHosts,
    AppMetadata,
    Block,
    HostBase,
)
from cherrymountain_apps.cherrymountain.render import NORTHBOUND_PLUGIN

APP: Final[str] = "cherrymountain"

#: plant.yaml role -> the host types the app may give it.
ROLE_TYPES: Final[dict[str, frozenset[str]]] = {
    "plc": frozenset({"openplc"}),
    "rio": frozenset({"rio"}),
    "field": frozenset({"rio"}),
    "bas": frozenset({"rio"}),
    "fep": frozenset({"rio"}),
    "dnp3-master": frozenset({"rio"}),
    "scada": frozenset({"fuxa"}),
    "sim": frozenset({"sim-ctl", "sim-federate"}),
    "historian": frozenset({"historian"}),
    "hmi": frozenset({"hmi"}),
    "jump": frozenset({"guacamole"}),
}
#: Roles whose hosts the app should provision; a missing one is a warning.
PROVISIONED_ROLES: Final[frozenset[str]] = frozenset(
    {"plc", "scada", "sim", "historian", "hmi"}
)


class Problem(BaseModel):
    """One thing wrong with the scenario or the topology."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    #: The host it belongs to, or None for the app block.
    hostname: str | None
    #: The field, as a metadata or topology path.
    field: str
    message: str
    hint: str = ""
    fatal: bool = True


def closest(name: str, candidates: Iterable[str]) -> str:
    """A did-you-mean hint, else the known names."""
    known: list[str] = sorted(candidates)
    if not known:
        return ""
    close: list[str] = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
    if close:
        return f"did you mean '{close[0]}'?"
    return "known: " + ", ".join(known)


def _path(loc: Iterable[str | int]) -> str:
    out: str = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def _known_keys(model: type[BaseModel]) -> list[str]:
    return [f.alias or name for name, f in model.model_fields.items()]


def unknown_keys(model: BaseModel, path: str) -> list[tuple[str, str]]:
    """(field, hint) for every unknown key in the open shapes below ``model``."""
    found: list[tuple[str, str]] = []
    if isinstance(model, Block):
        known: list[str] = _known_keys(type(model))
        for key in model.model_extra or {}:
            found.append((f"{path}.{key}", closest(key, known)))
    for name, info in type(model).model_fields.items():
        value: Any = getattr(model, name)
        sub: str = f"{path}.{info.alias or name}"
        if isinstance(value, BaseModel):
            found += unknown_keys(value, sub)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, BaseModel):
                    found += unknown_keys(item, f"{sub}[{index}]")
    return found


def _error_problem(err: Any, hostname: str | None, prefix: str, raw: Any) -> Problem:
    """One pydantic error as a Problem, without the offending value."""
    loc: list[str | int] = list(err["loc"])
    kind: str = err["type"]
    if kind == "unknown_host_type":
        given: Any = raw.get("type") if isinstance(raw, dict) else None
        hint: str = (
            closest(given, HOST_TYPES)
            if isinstance(given, str)
            else "known: " + ", ".join(sorted(HOST_TYPES))
        )
        return Problem(
            hostname=hostname,
            field=f"{prefix}.type",
            message="not a known host type",
            hint=hint,
        )
    # Drop the discriminator tag pydantic adds to the location.
    if loc and loc[0] in HOST_TYPES:
        loc = loc[1:]
    field: str = _path([prefix, *loc]) if loc else prefix
    if kind == "missing":
        return Problem(hostname=hostname, field=field, message="is required")
    if kind == "extra_forbidden":
        return Problem(
            hostname=hostname, field=field, message="unknown key", hint="remove it"
        )
    return Problem(hostname=hostname, field=field, message=str(err["msg"]))


class Parsed(BaseModel):
    """The validated metadata, or None where it failed to parse."""

    model_config: ClassVar[ConfigDict] = ConfigDict(arbitrary_types_allowed=True)

    app: AppMetadata | None
    hosts: dict[str, HostBase]


def parse(metadata: Any, hosts: list[Any], problems: list[Problem]) -> Parsed:
    """Validate the app block and every host's metadata in strict mode."""
    app: AppMetadata | None = None
    try:
        app = AppMetadata.model_validate(metadata or {}, strict=True)
    except ValidationError as ex:
        problems += [_error_problem(e, None, "metadata", metadata) for e in ex.errors()]
    else:
        problems += [
            Problem(
                hostname=None,
                field=f,
                message="unknown key, ignored",
                hint=h,
                fatal=False,
            )
            for f, h in unknown_keys(app, "metadata")
        ]

    parsed: dict[str, HostBase] = {}
    seen: set[str] = set()
    for index, raw in enumerate(hosts):
        name: str | None = raw.get("hostname") if isinstance(raw, dict) else None
        label: str | None = name if isinstance(name, str) else f"hosts[{index}]"
        try:
            entry = AppHosts.model_validate({"hosts": [raw]}, strict=True).hosts[0]
        except ValidationError as ex:
            for err in ex.errors():
                loc: list[str | int] = list(err["loc"])[2:]  # drop ("hosts", 0)
                if loc[:1] == ["metadata"]:
                    md: Any = raw.get("metadata") if isinstance(raw, dict) else None
                    problems.append(
                        _error_problem({**err, "loc": loc[1:]}, label, "metadata", md)
                    )
                else:
                    problems.append(
                        _error_problem({**err, "loc": loc}, label, "host", raw)
                    )
            continue
        if entry.hostname in seen:
            problems.append(
                Problem(
                    hostname=entry.hostname,
                    field="hostname",
                    message="listed more than once",
                )
            )
            continue
        seen.add(entry.hostname)
        parsed[entry.hostname] = entry.metadata
        problems += [
            Problem(
                hostname=entry.hostname,
                field=f,
                message="unknown key, ignored",
                hint=h,
                fatal=False,
            )
            for f, h in unknown_keys(entry.metadata, "metadata")
            if f != "metadata.sohProfile"
        ]
    return Parsed(app=app, hosts=parsed)


# ---------------------------------------------------------------- topology


def _interfaces(node: Any) -> dict[str, Any]:
    network: Any = node.get("network") or {}
    return {i.get("name"): i for i in network.get("interfaces") or [] if i.get("name")}


def check_topology(
    plant: Any,
    hosts: dict[str, HostBase],
    nodes: dict[str, Any],
    problems: list[Problem],
) -> None:
    """plant.yaml against the topology, and the app's hosts against both.

    ``plant`` is a plantgen ``Plant`` for the variant; ``nodes`` maps hostnames
    to topology nodes.
    """
    for name, phost in plant.hosts.items():
        node: Any = nodes.get(name)
        if node is None:
            problems.append(
                Problem(
                    hostname=name,
                    field=f"plant.yaml hosts.{name}",
                    message=f"{phost.role} host is missing from the topology",
                    hint=closest(name, nodes),
                )
            )
            continue
        ifaces: dict[str, Any] = _interfaces(node)
        for iname, spec in phost.interfaces.items():
            where: str = f"network.interfaces[{iname}]"
            iface: Any = ifaces.get(iname)
            if iface is None:
                problems.append(
                    Problem(
                        hostname=name,
                        field=where,
                        message=f"interface is missing; plant.yaml hosts.{name}.interfaces names it",
                        hint=closest(iname, ifaces),
                    )
                )
                continue
            address, _, prefix = spec.ip.partition("/")
            if iface.get("address") != address:
                problems.append(
                    Problem(
                        hostname=name,
                        field=f"{where}.address",
                        message=f"differs from plant.yaml hosts.{name}.interfaces.{iname}.ip",
                    )
                )
            if prefix and str(iface.get("mask")) != prefix:
                problems.append(
                    Problem(
                        hostname=name,
                        field=f"{where}.mask",
                        message=f"differs from the prefix length of plant.yaml hosts.{name}.interfaces.{iname}.ip",
                    )
                )
            if spec.vlan and iface.get("vlan") != spec.vlan:
                problems.append(
                    Problem(
                        hostname=name,
                        field=f"{where}.vlan",
                        message=f"differs from plant.yaml hosts.{name}.interfaces.{iname}.vlan",
                    )
                )

        allowed: frozenset[str] | None = ROLE_TYPES.get(phost.role)
        listed: HostBase | None = hosts.get(name)
        if listed is None and phost.role in PROVISIONED_ROLES:
            problems.append(
                Problem(
                    hostname=name,
                    field="hosts",
                    message=f"plant.yaml {phost.role} host is not in the app's hosts; the app will not provision it",
                    hint="add it with metadata.type "
                    + " or ".join(sorted(allowed or ())),
                    fatal=False,
                )
            )
        elif (
            allowed is not None
            and listed is not None
            and getattr(listed, "type", None) not in allowed
        ):
            problems.append(
                Problem(
                    hostname=name,
                    field="metadata.type",
                    message=f"does not fit the plant.yaml role {phost.role}",
                    hint="expected " + " or ".join(sorted(allowed)),
                )
            )

    for name, md in hosts.items():
        planned: Any = plant.hosts.get(name)
        nb: str | None = getattr(md, "northbound", None)
        if nb is not None and planned is not None:
            want: str = NORTHBOUND_PLUGIN.get(
                getattr(planned, "northbound", "modbus"), ""
            )
            if nb != want:
                problems.append(
                    Problem(
                        hostname=name,
                        field="metadata.northbound",
                        message=f"differs from plant.yaml hosts.{name}.northbound",
                        hint="leave it out to use plant.yaml's",
                    )
                )
    for name, md in hosts.items():
        if name not in nodes:
            problems.append(
                Problem(
                    hostname=name,
                    field="hostname",
                    message="listed in the cherrymountain app but missing from the topology",
                    hint=closest(name, nodes),
                )
            )
        kind: str = getattr(md, "type", "")
        if kind in {"openplc", "fuxa", "rio"} and name not in plant.hosts:
            problems.append(
                Problem(
                    hostname=name,
                    field="hostname",
                    message=f"{kind} host is not in plant.yaml variant {plant.variant}",
                    hint=closest(name, plant.hosts),
                )
            )
        federates: list[str] | None = getattr(md, "federates", None)
        for index, fed in enumerate(federates or []):
            if fed not in plant.federates and fed != "weather":
                problems.append(
                    Problem(
                        hostname=name,
                        field=f"metadata.federates[{index}]",
                        message=f"is not a federate of plant.yaml variant {plant.variant}",
                        hint=closest(fed, plant.federates),
                    )
                )

    for fed, spec in plant.federates.items():
        if spec.host not in nodes:
            problems.append(
                Problem(
                    hostname=spec.host,
                    field=f"plant.yaml federates.{fed}.host",
                    message="federate host is missing from the topology",
                )
            )


def check_references(
    app: AppMetadata,
    hosts: dict[str, HostBase],
    nodes: dict[str, Any],
    problems: list[Problem],
) -> None:
    """Hostnames that metadata names must exist."""

    def need(hostname: str | None, field: str, ref: str) -> None:
        if ref not in nodes:
            problems.append(
                Problem(
                    hostname=hostname,
                    field=field,
                    message="names a host that is not in the topology",
                    hint=closest(ref, nodes),
                )
            )

    need(None, "metadata.simCtl.hostname", app.sim_ctl.hostname)
    if app.wazuh is not None:
        need(None, "metadata.wazuh.manager", app.wazuh.manager)
        for index, agent in enumerate(app.wazuh.agents):
            need(None, f"metadata.wazuh.agents[{index}]", agent)
    for name, md in hosts.items():
        for field, ref in _host_refs(md):
            need(name, field, ref)


def _host_refs(md: HostBase) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    fuxa: Any = getattr(md, "fuxa", None)
    if isinstance(fuxa, str):
        refs.append(("metadata.fuxa", fuxa))
    for index, peer in enumerate(getattr(md, "peers", None) or []):
        refs.append((f"metadata.peers[{index}]", peer))
    for index, conn in enumerate(getattr(md, "connections", None) or []):
        refs.append((f"metadata.connections[{index}].hostname", conn.hostname))
    return refs


# ---------------------------------------------------------------- report


def report(problems: list[Problem]) -> str:
    """Problems as one readable block, fatal ones first."""
    fatal: list[Problem] = [p for p in problems if p.fatal]
    warnings: list[Problem] = [p for p in problems if not p.fatal]
    width: int = max((len(p.hostname or "app") for p in problems), default=0)

    def block(items: list[Problem], heading: str) -> list[str]:
        lines: list[str] = ["", heading]
        for p in sorted(items, key=lambda q: (q.hostname or "", q.field)):
            lines.append(
                f"  {(p.hostname or 'app').ljust(width)}  {p.field}: {p.message}"
            )
            if p.hint:
                lines.append(f"  {' ' * width}  -> {p.hint}")
        return lines

    counts: list[str] = []
    if fatal:
        counts.append(f"{len(fatal)} error{'s' if len(fatal) != 1 else ''}")
    if warnings:
        counts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
    out: list[str] = [f"{APP} app check: {' and '.join(counts)}"]
    if fatal:
        out += block(fatal, "ERRORS (the experiment cannot be provisioned):")
    if warnings:
        out += block(warnings, "WARNINGS (accepted, but probably not intended):")
    return "\n".join(out)


def enforce(problems: list[Problem]) -> None:
    """Log the report, then raise AppError if anything is fatal."""
    if not problems:
        return
    text: str = report(problems)
    fatal: int = sum(1 for p in problems if p.fatal)
    if not fatal:
        logger.warning(text)
        return
    logger.error(text)
    raise AppError(
        f"{APP} app check failed: {fatal} error{'s' if fatal != 1 else ''}; see the report above"
    )
