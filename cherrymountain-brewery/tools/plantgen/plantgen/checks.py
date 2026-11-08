"""Consistency checks (DESIGN.md §12.5, §17).

The address checks re-derive each convention from the *rendered* files
(the ot-sim app block, the modbus_master JSON, the FUXA tag list) rather
than from the allocator, so a bug in one renderer cannot hide behind the
same bug in another.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any

import yaml

from plantgen.allocate import (
    FUXA_MEMADDRESS,
    MASTER_FC,
    OTSIM_BASE,
    SPACE_FOR_TYPE,
    Allocation,
    IecAddress,
    iec_to_slave,
)
from plantgen.model import (
    ALARM_KINDS,
    MAX_REGISTER,
    PRIORITIES,
    RATIONALE_FIELDS,
    Plant,
    is_analog,
)

#: modbus_master function code -> ot-sim space.
FC_SPACE: dict[int, str] = {fc: space for space, fc in MASTER_FC.items()}


def plant_checks(plant: Plant, alloc: Allocation) -> list[str]:
    """Checks on the database and allocation (run before rendering)."""
    errors: list[str] = []
    for check in (
        unique_names,
        owners,
        unbound_points,
        ranges_fit,
        alarms_rationalized,
        no_gaps,
        ntp_hosts,
    ):
        errors += check(plant, alloc)
    return errors


def file_checks(plant: Plant, alloc: Allocation, files: dict[str, str]) -> list[str]:
    """Cross-checks of the rendered files against each other."""
    errors = otsim_agrees(plant, alloc, files)
    errors += master_agrees(plant, alloc, files)
    errors += fuxa_agrees(plant, alloc, files)
    errors += helics_closed(plant, files)
    return errors


def unique_names(plant: Plant, alloc: Allocation) -> list[str]:
    """Tag names are unique."""
    seen: set[str] = set()
    errs = []
    for t in plant.tags:
        if t.name in seen:
            errs.append(f"duplicate tag {t.name}")
        seen.add(t.name)
    return errs


def owners(plant: Plant, alloc: Allocation) -> list[str]:
    """Every tag has an owning PLC; I/O tags also a RIO and a federate."""
    errs = []
    for t in plant.tags:
        plc = plant.hosts.get(t.plc)
        if plc is None or plc.role != "plc":
            errs.append(
                f"{t.name}: owner PLC {t.plc!r} is not a plc host in the variant"
            )
        if t.device:
            rio = plant.hosts.get(t.rio or "")
            if rio is None or rio.role != "rio":
                errs.append(f"{t.name}: RIO {t.rio!r} is not a rio host in the variant")
            if t.federate not in plant.federates:
                errs.append(f"{t.name}: federate {t.federate!r} is not in the variant")
        if t.name not in alloc.tags:
            errs.append(f"{t.name}: no address assigned")
    return errs


def unbound_points(plant: Plant, alloc: Allocation) -> list[str]:
    """Every RIO register belongs to exactly one tag."""
    errs = []
    count: dict[tuple[str, str], int] = defaultdict(int)
    for t in plant.tags:
        if t.device:
            count[(t.device.name, t.point or "")] += 1
    for regs in alloc.rio_registers.values():
        for r in regs:
            n = count[(r.device.name, r.point)]
            if n != 1:
                errs.append(f"{r.rio} {r.device.name}.{r.point}: bound to {n} tags")
    return errs


def ranges_fit(plant: Plant, alloc: Allocation) -> list[str]:
    """Analog ranges fit an unsigned 16-bit register after scaling."""
    errs = []
    for t in plant.tags:
        if not is_analog(t.type):
            continue
        lo, hi = t.range or (math.nan, math.nan)
        if not (lo >= 0 and hi * 10**t.scaling <= MAX_REGISTER and lo < hi):
            errs.append(
                f"{t.name}: range {t.range} x10^{t.scaling} does not fit uint16"
            )
    return errs


def alarms_rationalized(plant: Plant, alloc: Allocation) -> list[str]:
    """Every alarm has kind, limit, priority, cause, consequence, action, time."""
    errs = []
    names = {t.name for t in plant.tags}
    for a in plant.alarms:
        where = f"alarm {a.name}"
        if a.kind not in ALARM_KINDS:
            errs.append(f"{where}: kind {a.kind!r} not in {ALARM_KINDS}")
        if a.priority not in PRIORITIES:
            errs.append(f"{where}: priority {a.priority!r} not in {PRIORITIES}")
        for f in RATIONALE_FIELDS:
            value = getattr(a, "response_time_s" if f == "response_time" else f)
            if not value:
                errs.append(f"{where}: missing rationalization field {f}")
        if math.isnan(a.limit):
            errs.append(f"{where}: missing limit")
        if a.kind == "deviation":
            if not a.setpoint or a.setpoint not in names:
                errs.append(f"{where}: deviation needs an existing setpoint tag")
            elif a.limit <= 0:
                errs.append(f"{where}: deviation limit must be positive")
        else:
            src = plant.tag(a.source)
            if src.range and not src.range[0] <= a.limit <= src.range[1]:
                errs.append(f"{where}: limit {a.limit} outside range {src.range}")
    return errs


def _contiguous(values: list[int]) -> bool:
    return sorted(values) == list(range(min(values), min(values) + len(values)))


def no_gaps(plant: Plant, alloc: Allocation) -> list[str]:
    """RIO, PLC, slave, and FUXA addresses are contiguous within each block."""
    errs = []
    for rio, regs in alloc.rio_registers.items():
        for space, base in OTSIM_BASE.items():
            addrs = [r.address for r in regs if r.space == space]
            if addrs and (min(addrs) != base or not _contiguous(addrs)):
                errs.append(f"{rio} {space}: registers not contiguous from {base}")
    blocks: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for a in alloc.tags.values():
        block = "io" if a.tag.device else "mem"
        blocks[(a.tag.plc, f"{a.plc_iec.area}{a.plc_iec.size}", block)].append(
            a.plc_iec.index
        )
        blocks[(a.tag.plc, f"slave-{a.slave_space}", block)].append(a.slave_address)
        blocks[(a.tag.plc, f"fuxa-{a.fuxa_memaddress}", block)].append(a.fuxa_address)
    for (plc, kind, block), idx in blocks.items():
        if not _contiguous(idx):
            errs.append(f"{plc} {kind} ({block}): addresses have gaps or duplicates")
    return errs


def ntp_hosts(plant: Plant, alloc: Allocation) -> list[str]:
    """Every variant host is listed; client sources are reachable servers."""
    errs = []
    ntp = plant.spec.ntp
    listed = ntp.hosts
    for h in plant.hosts:
        if h not in listed:
            errs.append(f"ntp: host {h} not listed")
    for h, md in listed.items():
        if h not in plant.hosts:
            errs.append(f"ntp: {h} is not a variant host")
            continue
        if md.server:
            if md.client or md.source:
                errs.append(f"ntp: {h} is both server and client")
            continue
        if not md.client:
            errs.append(f"ntp: {h} is neither server nor client")
            continue
        src = md.source or ntp.default_source
        server = plant.hosts.get(src.hostname)
        if (
            server is None
            or src.hostname not in listed
            or not listed[src.hostname].server
        ):
            errs.append(f"ntp: {h} source {src.hostname} is not a server")
            continue
        if src.interface not in server.interfaces:
            errs.append(f"ntp: {src.hostname} has no interface {src.interface}")
            continue
        vlan = server.interfaces[src.interface].vlan
        if vlan not in plant.hosts[h].vlans():
            errs.append(f"ntp: {h} shares no VLAN with {src.hostname} ({vlan})")
    return errs


def otsim_agrees(plant: Plant, alloc: Allocation, files: dict[str, str]) -> list[str]:
    """Replay the phenix ot-sim app's numbering on the rendered app block."""
    app = yaml.safe_load(files["phenix/ot-sim.yaml"])
    infra = app["metadata"]["infrastructures"][app["metadata"]["infrastructure"]]
    expected = {
        (t.device.name, t.point): alloc.tags[t.name] for t in plant.tags if t.device
    }
    errs = []
    for host in app["hosts"]:
        counters = dict(OTSIM_BASE)
        for dev in host["metadata"]["modbus"]["devices"]:
            name = dev["name"].split("/")[-1]
            for var, spec in infra[dev["type"]].items():
                space = SPACE_FOR_TYPE[spec["type"] if isinstance(spec, dict) else spec]
                addr = counters[space]
                counters[space] += 1
                got = expected.get((name, var))
                if got is None:
                    errs.append(f"ot-sim {host['hostname']} {name}.{var}: no tag")
                elif (got.rio_space, got.rio_address) != (space, addr):
                    errs.append(
                        f"ot-sim {name}.{var}: app assigns {space} {addr}, "
                        f"map says {got.rio_space} {got.rio_address}"
                    )
    return errs


def master_agrees(plant: Plant, alloc: Allocation, files: dict[str, str]) -> list[str]:
    """Expand modbus_master blocks and compare register-by-register."""
    errs = []
    by_rio = {
        (a.tag.rio, a.rio_space, a.rio_address): a
        for a in alloc.tags.values()
        if a.tag.device
    }
    for plc in alloc.plc_rios:
        cfg = json.loads(files[f"openplc/{plc}/modbus_master.json"])
        covered = set()
        for dev in cfg:
            for p in dev["config"]["io_points"]:
                space = FC_SPACE[p["fc"]]
                start = IecAddress.parse(p["iec_location"])
                for i in range(p["len"]):
                    key = (dev["name"], space, int(p["offset"]) + i)
                    covered.add(key)
                    a = by_rio.get(key)
                    iec = start.model_copy(update={"index": start.index + i})
                    if a is None:
                        errs.append(
                            f"modbus_master {key}: reads an unconfigured register"
                        )
                    elif a.plc_iec != iec:
                        errs.append(
                            f"modbus_master {key}: {iec} but map says {a.plc_iec}"
                        )
        missing = {k for k, a in by_rio.items() if a.tag.plc == plc} - covered
        errs += [f"modbus_master: {k} not polled" for k in sorted(missing)]
    return errs


def fuxa_agrees(plant: Plant, alloc: Allocation, files: dict[str, str]) -> list[str]:
    """FUXA (memaddress, address - 1) matches the slave map of the IEC address."""
    errs = []
    for name, text in files.items():
        if not name.startswith("fuxa/"):
            continue
        ids = set()
        for dev in json.loads(text)["devices"]:
            for tag in dev["tags"]:
                if tag["id"] in ids:
                    errs.append(f"FUXA duplicate id {tag['id']}")
                ids.add(tag["id"])
                a = alloc.tags[tag["name"]]
                space, pdu = iec_to_slave(a.plc_iec)
                want = (FUXA_MEMADDRESS[space], pdu + 1)
                if (tag["memaddress"], tag["address"]) != want:
                    errs.append(
                        f"FUXA {tag['name']}: {tag['memaddress']}/{tag['address']} "
                        f"but {a.plc_iec} is {space} {pdu} -> {want}"
                    )
    return errs


def helics_closed(plant: Plant, files: dict[str, str]) -> list[str]:
    """Every io-federate subscription has a publisher; every write is accepted."""
    doc: dict[str, Any] = json.loads(files["helics.json"])
    pubs = {p["key"] for f in doc["federates"].values() for p in f["publications"]}
    errs = []
    for f in doc["federates"].values():
        for s in f["subscriptions"]:
            if s["key"] not in pubs:
                errs.append(f"HELICS: no publisher for {s['key']}")
    endpoints = {f["endpoint"]: f for f in doc["federates"].values()}
    for rio, io in doc["io_federates"].items():
        for key in io["subscriptions"]:
            if key not in pubs:
                errs.append(f"HELICS: {rio} subscribes {key} with no publisher")
        for send in io["sends"]:
            fed = endpoints.get(send["endpoint"])
            if fed is None or send["tag"] not in fed["accepted_updates"]:
                errs.append(f"HELICS: {rio} write {send['tag']} not accepted")
    return errs
