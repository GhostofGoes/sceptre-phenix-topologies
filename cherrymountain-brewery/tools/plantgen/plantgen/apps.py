"""Per-variant phenix app blocks whose hosts differ by variant (DESIGN.md §15.3).

- ``soh``: state of health, with one ``testCustomReachability`` entry per
  testable flow (DESIGN.md §15.4). Flows are derived from the plant (PLC to
  its field devices, FUXA to what it polls, FEP to its upstream devices, the
  DNP3 master to the FEP) plus the explicit conduits in plant.yaml. Only
  flows whose two hosts are in the variant are kept.
- ``wireguard``: the vendor tunnel with deterministic range-only keys
  (:mod:`plantgen.wgkeys`).
- SCORCH ``pcap`` capture lists per run (DESIGN.md §15.6).
- ``cherrymountain``: the app block listing every host the app provisions or
  checks, with its type (DESIGN.md §16; the app's metadata models are in
  ``apps/cherrymountain_apps/cherrymountain/metadata.py``).
"""

from __future__ import annotations

import base64
from typing import Any

from plantgen.allocate import Allocation
from plantgen.model import (
    MODBUS_IO_ROLES,
    BacnetEndpoint,
    Conduit,
    Dnp3Endpoint,
    Host,
    ModbusEndpoint,
    Plant,
    PlantError,
    SohProfile,
)
from plantgen.render import NORTHBOUND_PORT, fuxa_sources
from plantgen.wgkeys import derive

#: BACnet object type number of the Device object.
BACNET_DEVICE_TYPE: int = 8
#: BACnet property identifier ``object-name``.
PROP_OBJECT_NAME: int = 77


def bacnet_read_property(instance: int, prop: int = PROP_OBJECT_NAME) -> str:
    """Base64 BACnet/IP ReadProperty(Device ``instance``, ``prop``) request.

    BVLC Original-Unicast-NPDU, NPDU expecting a reply, confirmed request
    (invoke id 1), service 12 (ReadProperty). For device 3003 and
    object-name this is the packet in DESIGN.md §15.4.
    """
    oid = (BACNET_DEVICE_TYPE << 22) | instance
    apdu = bytes([0x00, 0x05, 0x01, 0x0C, 0x0C]) + oid.to_bytes(4, "big")
    apdu += bytes([0x19, prop])
    npdu = bytes([0x01, 0x04])
    length = 4 + len(npdu) + len(apdu)
    frame = bytes([0x81, 0x0A]) + length.to_bytes(2, "big") + npdu + apdu
    return base64.b64encode(frame).decode()


def _first_plant_nic(host: Host) -> str:
    for name in host.interfaces:
        if name.startswith("IF"):
            return name
    raise PlantError(f"{host.name} has no plant interface")


def _flow(
    plant: Plant,
    src: str,
    dst: str,
    interface: str | None,
    *,
    proto: str,
    port: int,
    payload: str | None = None,
) -> dict[str, Any] | None:
    if src not in plant.hosts or dst not in plant.hosts:
        return None
    if plant.hosts[src].vyos:
        return None  # SoH probes run from the guest agent; VyOS has none here
    nic = interface or _first_plant_nic(plant.hosts[dst])
    if nic not in plant.hosts[dst].interfaces:
        raise PlantError(f"soh: {dst} has no interface {nic}")
    entry: dict[str, Any] = {
        "src": src,
        "dst": f"{dst}|{nic}",
        "proto": proto,
        "port": port,
        "wait": plant.file.soh.wait if plant.file.soh else "5m",
    }
    if payload:
        entry["udpPacketBase64"] = payload
    return entry


def reachability(plant: Plant, alloc: Allocation) -> list[dict[str, Any]]:
    """Every testable flow in the variant, deduplicated, in a stable order."""
    flows: list[dict[str, Any] | None] = []
    for scada in plant.hosts_by_role("scada"):
        for host_name, _ in fuxa_sources(plant, scada):
            host = plant.hosts[host_name]
            if host.role == "bas":
                ep = host.bacnet or BacnetEndpoint(instance=0, name="")
                flows.append(
                    _flow(
                        plant,
                        scada.name,
                        host_name,
                        ep.interface,
                        proto="udp",
                        port=ep.port,
                        payload=bacnet_read_property(ep.instance),
                    )
                )
            else:
                mb = host.modbus or ModbusEndpoint()
                port = NORTHBOUND_PORT[host.northbound]
                flows.append(
                    _flow(
                        plant,
                        scada.name,
                        host_name,
                        mb.interface,
                        proto="tcp",
                        port=port,
                    )
                )
    for plc, rios in alloc.plc_rios.items():
        for rio in rios:
            host = plant.hosts.get(rio)
            if host is None or host.role not in MODBUS_IO_ROLES:
                continue
            mb = host.modbus or ModbusEndpoint()
            flows.append(
                _flow(plant, plc, rio, mb.interface, proto="tcp", port=mb.port)
            )
    for fep in plant.hosts_by_role("fep"):
        for up in fep.upstream:
            host = plant.hosts.get(up)
            mb = (host.modbus if host else None) or ModbusEndpoint()
            flows.append(
                _flow(plant, fep.name, up, mb.interface, proto="tcp", port=mb.port)
            )
    for master in plant.hosts_by_role("dnp3-master"):
        for up in master.upstream:
            host = plant.hosts.get(up)
            dnp = (host.dnp3 if host else None) or Dnp3Endpoint()
            flows.append(
                _flow(plant, master.name, up, dnp.interface, proto="tcp", port=dnp.port)
            )
    conduits: list[Conduit] = plant.file.soh.conduits if plant.file.soh else []
    for c in conduits:
        flows.append(
            _flow(plant, c.src, c.dst, c.interface, proto=c.proto, port=c.port)
        )
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()
    for f in flows:
        if f is None:
            continue
        key = (f["src"], f["dst"], f["proto"], f["port"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _profile(plant: Plant, host: Host) -> SohProfile:
    soh = plant.file.soh
    if soh is None:
        return SohProfile()
    base = soh.profiles.get(host.role, SohProfile())
    extra = soh.host_profiles.get(host.name)
    if extra is None:
        return base
    return SohProfile(
        processes=[*base.processes, *extra.processes],
        listeners=[*base.listeners, *extra.listeners],
        containers=[*base.containers, *extra.containers],
    )


def soh_app(plant: Plant, alloc: Allocation) -> dict[str, Any]:
    """Scenario ``soh`` app block for the variant (DESIGN.md §15.4)."""
    soh = plant.file.soh
    if soh is None:
        raise PlantError("variant enables soh but plant.yaml has no soh section")
    skip = [h for h in soh.skip_hosts if h in plant.hosts]
    skip += [h.name for h in plant.hosts.values() if h.vyos and h.name not in skip]
    processes: dict[str, list[str]] = {}
    listeners: dict[str, list[str]] = {}
    containers: dict[str, list[str]] = {}
    for host in plant.hosts.values():
        if host.name in skip:
            continue
        prof = _profile(plant, host)
        if prof.processes:
            processes[host.name] = list(prof.processes)
        if prof.listeners:
            listeners[host.name] = list(prof.listeners)
        if prof.containers:
            containers[host.name] = list(prof.containers)
    md: dict[str, Any] = {
        "startupDelay": soh.startup_delay,
        "c2Timeout": soh.c2_timeout,
        # Segmentation blocks pings by design; flows are tested one by one.
        "testReachability": "off",
        "skipHosts": skip,
        "testCustomReachability": reachability(plant, alloc),
        "hostProcesses": processes,
        "hostListeners": listeners,
    }
    if containers:
        md["dockerContainers"] = containers
    return {"name": "soh", "metadata": md}


def wireguard_app(plant: Plant) -> dict[str, Any]:
    """Scenario ``wireguard`` app block: dmz-vpn and vendor-ws (DESIGN.md §13)."""
    wg = plant.file.wireguard
    if wg is None:
        raise PlantError("variant enables wireguard but plant.yaml has none")
    for host in (wg.server, wg.client):
        if host not in plant.hosts:
            raise PlantError(f"wireguard: {host} is not in variant {plant.variant}")
    server = derive(wg.seed, wg.server)
    client = derive(wg.seed, wg.client)
    client_ip = wg.client_address.split("/")[0]
    return {
        "name": "wireguard",
        "hosts": [
            {
                "hostname": wg.server,
                "metadata": {
                    "boot": True,
                    "interface": {
                        "address": wg.server_address,
                        "listen_port": wg.listen_port,
                        "private_key": server.private_key,
                    },
                    "peers": [
                        {
                            "public_key": client.public_key,
                            "allowed_ips": f"{client_ip}/32",
                        }
                    ],
                },
            },
            {
                "hostname": wg.client,
                "metadata": {
                    "boot": True,
                    "interface": {
                        "address": wg.client_address,
                        "private_key": client.private_key,
                    },
                    "peers": [
                        {
                            "public_key": server.public_key,
                            "allowed_ips": ", ".join(wg.client_allowed_ips),
                            "server_endpoint": wg.endpoint,
                            "keepalive": wg.keepalive,
                        }
                    ],
                },
            },
        ],
    }


def scorch_captures(plant: Plant) -> dict[str, Any]:
    """SCORCH ``pcap`` component ``vms`` lists per run, for the variant.

    Each run's list keeps only the hosts in the variant; an interface a host
    does not have is an error.
    """
    sc = plant.file.scorch
    if sc is None:
        raise PlantError("variant enables scorch but plant.yaml has none")
    runs: dict[str, Any] = {}
    for run, targets in sc.captures.items():
        vms = []
        for target in targets:
            host, _, nic = target.partition("|")
            if host not in plant.hosts:
                continue
            if nic not in plant.hosts[host].interfaces:
                raise PlantError(f"scorch {run}: {host} has no interface {nic}")
            vms.append({"hostname": host, "interface": nic})
        if vms:
            runs[run] = {"type": "pcap", "metadata": {"vms": vms}}
    return {"runs": runs}


#: cherrymountain app host type per plant.yaml role (hosts may override it
#: with ``app:``). Roles not listed are not app hosts.
APP_TYPES: dict[str, str] = {
    "sim": "sim-federate",
    "rio": "rio",
    "field": "rio",
    "bas": "rio",
    "fep": "rio",
    "dnp3-master": "rio",
    "plc": "openplc",
    "scada": "fuxa",
    "historian": "historian",
    "ews": "ews",
}
#: Order of the host types in the block (the app's fixtures use it).
APP_ORDER: tuple[str, ...] = (
    "sim-ctl",
    "sim-federate",
    "rio",
    "openplc",
    "fuxa",
    "historian",
    "hmi",
    "guacamole",
    "wazuh-manager",
    "bbmd",
    "ews",
)


def cherrymountain_app(plant: Plant) -> dict[str, Any]:
    """Scenario ``cherrymountain`` app block for the variant.

    The HELICS broker host is ``sim-ctl``; other sim hosts run federates.
    Host metadata comes from ``hosts.<name>.app`` in plant.yaml, else from
    the role (:data:`APP_TYPES`). App-level metadata is the variant's
    ``app`` block plus its name.
    """
    broker = plant.spec.helics.broker.hostname
    entries: list[tuple[int, int, dict[str, Any]]] = []
    for i, host in enumerate(plant.hosts.values()):
        if host.app is not None:
            md = dict(host.app)
        elif host.name == broker:
            md = {"type": "sim-ctl"}
        elif host.role in APP_TYPES:
            md = {"type": APP_TYPES[host.role]}
        else:
            continue
        if md.get("type") not in APP_ORDER:
            raise PlantError(
                f"cherrymountain app: {host.name} has type {md.get('type')}"
            )
        entries.append(
            (APP_ORDER.index(md["type"]), i, {"hostname": host.name, "metadata": md})
        )
    hosts = [e for _, _, e in sorted(entries, key=lambda e: (e[0], e[1]))]
    return {
        "name": "cherrymountain",
        "metadata": {"variant": plant.variant, **plant.spec.app},
        "hosts": hosts,
    }
