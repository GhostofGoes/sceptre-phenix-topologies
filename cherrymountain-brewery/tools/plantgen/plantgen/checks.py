"""Consistency checks (DESIGN.md §12.5, §17).

The address checks re-derive each convention from the *rendered* files
(the ot-sim app block, the modbus_master JSON, the FUXA tag list) rather
than from the allocator, so a bug in one renderer cannot hide behind the
same bug in another.
"""

from __future__ import annotations

import ipaddress
import json
import math
from collections import defaultdict
from typing import Any

import yaml

from plantgen.allocate import (
    FUXA_MEMADDRESS,
    MASTER_FC,
    OTSIM_BASE,
    READ_HOLDING_FC,
    SPACE_FOR_TYPE,
    Allocation,
    IecAddress,
    SlaveLayout,
)
from plantgen.model import (
    ALARM_KINDS,
    INT16_MAX,
    INT16_MIN,
    IO_ROLES,
    MAX_REGISTER,
    MODBUS_IO_ROLES,
    PRIORITIES,
    RATIONALE_FIELDS,
    Plant,
    is_analog,
)

#: modbus_master function code -> ot-sim space.
FC_SPACE: dict[int, str] = {fc: space for space, fc in MASTER_FC.items()} | {
    READ_HOLDING_FC: "holding"
}


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
        addresses_valid,
        bacnet_valid,
        external_writers,
        federates_hosted,
    ):
        errors += check(plant, alloc)
    return errors


def file_checks(plant: Plant, alloc: Allocation, files: dict[str, str]) -> list[str]:
    """Cross-checks of the rendered files against each other."""
    errors = otsim_agrees(plant, alloc, files)
    errors += master_agrees(plant, alloc, files)
    errors += slave_agrees(plant, alloc, files)
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
    """Every tag has an owner: a PLC, or for BACnet points their controller.

    I/O tags also need a field host of the right kind (a Modbus RIO or field
    device, or a BACnet controller) and a federate in the variant.
    """
    errs = []
    for t in plant.tags:
        if t.bacnet is None:
            plc = plant.hosts.get(t.plc or "")
            if plc is None or plc.role != "plc":
                errs.append(
                    f"{t.name}: owner PLC {t.plc!r} is not a plc host in the variant"
                )
            if t.name not in alloc.tags:
                errs.append(f"{t.name}: no address assigned")
        if t.device:
            rio = plant.hosts.get(t.rio or "")
            roles = MODBUS_IO_ROLES if t.bacnet is None else ("bas",)
            if rio is None or rio.role not in roles:
                errs.append(
                    f"{t.name}: field host {t.rio!r} is not a {'/'.join(roles)} "
                    "host in the variant"
                )
            if t.federate not in plant.federates:
                errs.append(f"{t.name}: federate {t.federate!r} is not in the variant")
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
    for objs in alloc.bacnet.values():
        for o in objs:
            n = count[(o.device.name, o.point)]
            if n != 1:
                errs.append(f"{o.host} {o.device.name}.{o.point}: bound to {n} tags")
    return errs


def ranges_fit(plant: Plant, alloc: Allocation) -> list[str]:
    """Analog ranges fit an unsigned 16-bit register after scaling."""
    errs = []
    for t in plant.tags:
        if not is_analog(t.type):
            continue
        lo, hi = t.range or (math.nan, math.nan)
        if t.bacnet is not None:  # BACnet REAL: any finite range
            if not lo < hi:
                errs.append(f"{t.name}: range {t.range} is empty")
            continue
        scale = 10**t.scaling
        if t.signed:
            if not (lo * scale >= INT16_MIN and hi * scale <= INT16_MAX and lo < hi):
                errs.append(
                    f"{t.name}: range {t.range} x10^{t.scaling} does not fit int16"
                )
        elif not (lo >= 0 and hi * scale <= MAX_REGISTER and lo < hi):
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
        src = plant.tag(a.source)
        if a.kind == "deviation":
            if not a.setpoint or a.setpoint not in names:
                errs.append(f"{where}: deviation needs an existing setpoint tag")
            elif a.limit <= 0:
                errs.append(f"{where}: deviation limit must be positive")
        elif a.kind == "state":
            if not is_analog(src.type) and a.limit not in (0.0, 1.0):
                errs.append(f"{where}: a bit's state alarm limit must be 0 or 1")
        elif not is_analog(src.type):
            errs.append(f"{where}: {a.kind} alarms need an analog source")
        elif src.range and not src.range[0] <= a.limit <= src.range[1]:
            errs.append(f"{where}: limit {a.limit} outside range {src.range}")
        if a.suppress is not None:
            if a.suppress not in names:
                errs.append(f"{where}: suppression tag {a.suppress} does not exist")
            elif is_analog(plant.tag(a.suppress).type):
                errs.append(f"{where}: suppression tag {a.suppress} is not a bit")
            elif plant.tag(a.suppress).owner != src.owner:
                errs.append(f"{where}: suppression tag {a.suppress} has another owner")
    seen: dict[tuple[str, str], str] = {}
    for a in plant.alarms:
        key = (a.source, a.kind)
        if key in seen:
            errs.append(f"alarm {a.name}: second {a.kind} alarm on {a.source}")
        seen[key] = a.name
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
        plc_host = plant.hosts.get(a.tag.plc or "")
        if plc_host is not None and plc_host.northbound != "modbus":
            blocks[
                (a.tag.plc or "", f"{a.plc_iec.area}{a.plc_iec.size}", block)
            ].append(a.plc_iec.index)
            continue
        blocks[(a.tag.plc, f"{a.plc_iec.area}{a.plc_iec.size}", block)].append(
            a.plc_iec.index
        )
        blocks[(a.tag.plc, f"slave-{a.slave_space}", block)].append(a.slave_address)
        blocks[(a.tag.plc, f"fuxa-{a.fuxa_memaddress}", block)].append(a.fuxa_address)
    for (plc, kind, block), idx in blocks.items():
        if not _contiguous(idx):
            errs.append(f"{plc} {kind} ({block}): addresses have gaps or duplicates")
    return errs


def reachable(plant: Plant, client: str, vlan: str) -> bool:
    """A client reaches an address on ``vlan``: it shares the VLAN, or both
    are on routed networks (every routed network has static routes to every
    other, DESIGN.md §10.2; the firewalls decide what may pass)."""
    vlans = plant.hosts[client].vlans()
    if vlan in vlans:
        return True

    def routed(v: str) -> bool:
        net = plant.network(v)
        return net is not None and net.gateway is not None

    return routed(vlan) and any(routed(v) for v in vlans)


def ntp_hosts(plant: Plant, alloc: Allocation) -> list[str]:
    """Every variant host has an entry; client sources are reachable servers."""
    errs = []
    ntp = plant.spec.ntp
    for h in ntp.hosts:
        if h not in plant.hosts:
            errs.append(f"ntp: {h} is not a variant host")
    for h in plant.hosts:
        md = plant.ntp_entry(h)
        if md is None:
            errs.append(f"ntp: host {h} not listed")
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
        entry = plant.ntp_entry(src.hostname) if server else None
        if server is None or entry is None or not entry.server:
            errs.append(f"ntp: {h} source {src.hostname} is not a server")
            continue
        if src.interface not in server.interfaces:
            errs.append(f"ntp: {src.hostname} has no interface {src.interface}")
            continue
        vlan = server.interfaces[src.interface].vlan
        if not reachable(plant, h, vlan):
            errs.append(f"ntp: {h} cannot reach {src.hostname} ({vlan})")
    return errs


def otsim_agrees(plant: Plant, alloc: Allocation, files: dict[str, str]) -> list[str]:
    """Replay the phenix ot-sim app's numbering on the rendered app block."""
    app = yaml.safe_load(files["phenix/ot-sim.yaml"])
    infra = app["metadata"]["infrastructures"][app["metadata"]["infrastructure"]]
    expected = {
        (t.device.name, t.point): alloc.tags[t.name]
        for t in plant.tags
        if t.device and t.bacnet is None
    }
    objects = {
        (o.device.name, o.point): o for objs in alloc.bacnet.values() for o in objs
    }
    errs = []
    for host in app["hosts"]:
        counters = dict(OTSIM_BASE)
        if "bacnet" in host["metadata"]:
            # The proposed module numbers objects like the app numbers
            # registers: from 1 per object type, device order, point order.
            instances: dict[str, int] = defaultdict(lambda: 1)
            for dev in host["metadata"]["bacnet"]["devices"]:
                name = dev["name"].split("/")[-1]
                for var, spec in infra[dev["type"]].items():
                    otype = spec["bacnet"]["object-type"]
                    inst = instances[otype]
                    instances[otype] += 1
                    o = objects.get((name, var))
                    if o is None or (o.object_type, o.instance) != (otype, inst):
                        errs.append(
                            f"ot-sim {name}.{var}: BACnet {otype} {inst} disagrees"
                        )
            continue
        if "modbus" not in host["metadata"]:
            continue  # the FEP and the DNP3 master
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
        peers: dict[tuple[str, int], Any] = {
            (r.source_plc, r.coil): r for r in alloc.peer_reads.get(plc, [])
        }
        for dev in cfg:
            if dev["name"] in plant.hosts and plant.hosts[dev["name"]].name in alloc.plc_rios:
                # A peer PLC: every coil read must land where the peer read says.
                for p in dev["config"]["io_points"]:
                    start = IecAddress.parse(p["iec_location"])
                    for i in range(p["len"]):
                        read = peers.pop((dev["name"], int(p["offset"]) + i), None)
                        iec = start.model_copy(update={"index": start.index + i})
                        if p["fc"] != 1 or read is None or read.iec != iec:
                            errs.append(f"modbus_master {plc} peer {dev['name']} coil {int(p['offset']) + i}: not a planned peer read")
                continue
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
        errs += [f"modbus_master {plc}: peer read {r.tag} not polled" for r in peers.values()]
    return errs


#: FUXA ``memaddress`` area -> Modbus space.
FUXA_SPACE: dict[str, str] = {v: k for k, v in FUXA_MEMADDRESS.items()}


def resolve_slave(
    mapping: dict[str, dict[str, int]], space: str, pdu: int
) -> str | None:
    """The IEC location OpenPLC v4 ``modbus_slave`` serves at a PDU address.

    A re-implementation of the runtime's own direction (PDU address to
    segment, as ``OpenPLCSegmentedHoldingRegistersDataBlock._get_segment_info``
    does at v4.3.2), written from the buffer_mapping alone. It is the inverse
    of ``SlaveLayout.to_slave`` on purpose: a mistake in one cannot hide in the
    other. Returns ``None`` for an address outside every segment.
    """
    if pdu < 0:
        return None
    if space == "holding":
        hr = mapping["holding_registers"]
        for name, width, label in (
            ("qw_count", 1, "QW"),
            ("mw_count", 1, "MW"),
            ("md_count", 2, "MD"),
            ("ml_count", 4, "ML"),
        ):
            span = hr[name] * width
            if pdu < span:
                return f"%{label}{pdu // width}"
            pdu -= span
        return None
    if space in ("coil", "discrete"):
        segments = (
            [("qx_bits", "Q"), ("mx_bits", "M")]
            if space == "coil"
            else [("ix_bits", "I")]
        )
        section = mapping["coils" if space == "coil" else "discrete_inputs"]
        for name, area in segments:
            span = section.get(name, 0)
            if pdu < span:
                return f"%{area}X{pdu // 8}.{pdu % 8}"
            pdu -= span
        return None
    if space == "input":
        return f"%IW{pdu}" if pdu < mapping["input_registers"]["iw_count"] else None
    return None


def slave_agrees(plant: Plant, alloc: Allocation, files: dict[str, str]) -> list[str]:
    """Each tag's slave address resolves, through the *rendered*
    ``modbus_slave.json``, back to the tag's own IEC location; and the counts
    are tight (the highest located index + 1), so the v4 runtime clamps nothing."""
    errs: list[str] = []
    for plc in alloc.plc_rios:
        key = f"openplc/{plc}/modbus_slave.json"
        if plant.hosts[plc].northbound != "modbus":
            if key in files:
                errs.append(f"{key}: {plc} is {plant.hosts[plc].northbound}")
            continue
        cfg = json.loads(files[key])
        mapping = cfg["buffer_mapping"]
        for a in alloc.tags.values():
            if a.tag.plc != plc:
                continue
            got = resolve_slave(mapping, a.slave_space, a.slave_address)
            if got != str(a.plc_iec):
                errs.append(
                    f"modbus_slave {plc}: {a.tag.name} is at {a.slave_space} "
                    f"{a.slave_address}, which the runtime serves as {got}, "
                    f"not {a.plc_iec}"
                )
        want = SlaveLayout.from_addresses(
            a.plc_iec for a in alloc.tags.values() if a.tag.plc == plc
        )
        if mapping != want.buffer_mapping():
            errs.append(
                f"modbus_slave {plc}: buffer_mapping {mapping} is not the tight "
                f"layout {want.buffer_mapping()}"
            )
    return errs


def fuxa_agrees(plant: Plant, alloc: Allocation, files: dict[str, str]) -> list[str]:
    """FUXA (memaddress, address - 1) resolves, through ``modbus_slave.json``,
    to the tag's IEC location."""
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
            if dev["type"] == "BACnet":
                errs += _fuxa_bacnet(plant, alloc, name, dev)
                continue
            if dev["type"] != "ModbusTCP":
                for tag in dev["tags"]:
                    a = alloc.tags[tag["name"]]
                    if tag.get("plc_iec") != str(a.plc_iec):
                        errs.append(f"FUXA {tag['name']}: plc_iec is not {a.plc_iec}")
                continue
            plc = dev["name"]
            mapping = json.loads(files[f"openplc/{plc}/modbus_slave.json"])[
                "buffer_mapping"
            ]
            for tag in dev["tags"]:
                a = alloc.tags[tag["name"]]
                space = FUXA_SPACE.get(tag["memaddress"], "?")
                got = resolve_slave(mapping, space, tag["address"] - 1)
                if got != str(a.plc_iec):
                    errs.append(
                        f"FUXA {tag['name']}: {tag['memaddress']}/{tag['address']} "
                        f"is {space} {tag['address'] - 1}, served as {got}, "
                        f"but the tag is {a.plc_iec}"
                    )
    return errs


def _fuxa_bacnet(
    plant: Plant, alloc: Allocation, name: str, dev: dict[str, Any]
) -> list[str]:
    """BACnet tags address the object the controller serves for the tag."""
    errs = []
    objects = {
        o.tag.name: (o, h) for h, objs in alloc.bacnet.items() for o in objs if o.tag
    }
    for tag in dev["tags"]:
        found = objects.get(tag["name"])
        if found is None:
            errs.append(f"{name} {tag['name']}: no BACnet object")
            continue
        obj, host = found
        ep = plant.hosts[host].bacnet
        want = (obj.fuxa_address, str(ep.instance if ep else ""))
        if (tag["address"], tag["memaddress"]) != want:
            errs.append(f"{name} {tag['name']}: BACnet address is not {want}")
    return errs


def addresses_valid(plant: Plant, alloc: Allocation) -> list[str]:
    """Interface addresses parse, sit in their VLAN's subnet, and are unique."""
    errs = []
    seen: dict[str, str] = {}
    for host in plant.hosts.values():
        for nic, iface in host.interfaces.items():
            where = f"{host.name} {nic}"
            try:
                addr = ipaddress.ip_interface(iface.ip)
            except ValueError:
                errs.append(f"{where}: bad address {iface.ip}")
                continue
            net = plant.network(iface.vlan)
            if net is not None and addr.ip not in ipaddress.ip_network(net.subnet):
                errs.append(f"{where}: {addr.ip} is not in {iface.vlan} {net.subnet}")
            ip = str(addr.ip)
            if ip in seen:
                errs.append(f"{where}: {ip} is also on {seen[ip]}")
            seen[ip] = where
    return errs


def bacnet_valid(plant: Plant, alloc: Allocation) -> list[str]:
    """BACnet device instances are unique and every controller has one."""
    errs = []
    instances: dict[int, str] = {}
    for host in alloc.bacnet:
        ep = plant.hosts[host].bacnet if host in plant.hosts else None
        if ep is None:
            errs.append(f"{host}: BACnet points but no bacnet endpoint")
            continue
        if ep.interface not in plant.hosts[host].interfaces:
            errs.append(f"{host}: bacnet interface {ep.interface} does not exist")
        if ep.instance in instances:
            other = instances[ep.instance]
            errs.append(f"{host}: device instance {ep.instance} also on {other}")
        instances[ep.instance] = host
    return errs


def external_writers(plant: Plant, alloc: Allocation) -> list[str]:
    """A ``written_by`` host polls the device (a FEP lists it upstream)."""
    errs = []
    for t in plant.tags:
        if not t.written_by:
            continue
        writer = plant.hosts.get(t.written_by)
        if writer is None:
            continue  # not in this variant: nothing writes the register
        if t.rio not in writer.upstream:
            errs.append(f"{t.name}: {t.written_by} does not poll {t.rio}")
    return errs


def federates_hosted(plant: Plant, alloc: Allocation) -> list[str]:
    """Each federate runs on a sim host of the variant; field hosts of a
    federate's devices are HELICS-backed (an MGMT NIC)."""
    errs = []
    for name, fed in plant.federates.items():
        host = plant.hosts.get(fed.host)
        if host is None or host.role != "sim":
            errs.append(f"federate {name}: host {fed.host} is not a sim host")
    for d in plant.devices:
        host = plant.hosts.get(d.rio)
        if host is not None and host.role in IO_ROLES and "mgmt" not in host.interfaces:
            errs.append(f"{d.rio}: HELICS-backed device {d.name} but no mgmt NIC")
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
