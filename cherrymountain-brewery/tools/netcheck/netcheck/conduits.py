"""The DESIGN.md section 10.3 conduit matrix, as data.

Each ``Conduit`` is one row (a few rows are split where their services or
enforcement differ). Endpoints are selectors:

* a host name (``scada-fuxa``),
* ``zone:<VLAN>`` for every host on that VLAN (the firewall rule then matches
  the VLAN's prefix), or
* ``@<group>`` for a named group of hosts (``GROUPS``).

``enforced`` is false for flows that no firewall rule can carry: router-local
traffic (IKE, the firewalls' own syslog and NTP), intra-zone traffic, and the
WireGuard tunnel, whose peer restriction is the WireGuard AllowedIPs.

Probes drive SoH ``testCustomReachability`` (flows that answer) and the
cc-based negative tests (``BLOCKED_PROBES``). Rules that send no reply
(syslog) have no probe: SoH TCP and UDP probes need an answer.
"""

from __future__ import annotations

import base64
import struct
from typing import Literal

from pydantic import BaseModel, ConfigDict

from netcheck import plan

Proto = Literal["tcp", "udp"]


class Service(BaseModel):
    model_config = ConfigDict(frozen=True)

    proto: Proto
    port: int


def tcp(*ports: int) -> tuple[Service, ...]:
    return tuple(Service(proto="tcp", port=p) for p in ports)


def udp(*ports: int) -> tuple[Service, ...]:
    return tuple(Service(proto="udp", port=p) for p in ports)


class Probe(BaseModel):
    """One SoH reachability test: ``src`` connects to ``dst`` on its IF0."""

    model_config = ConfigDict(frozen=True)

    src: str
    dst: str
    proto: Proto
    port: int
    packet: str | None = None  # base64 UDP payload that draws a reply


class Conduit(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    srcs: tuple[str, ...]
    dsts: tuple[str, ...]
    services: tuple[Service, ...]
    #: Firewalls whose forward chain must accept the flow, in no order.
    via: tuple[str, ...] = ()
    enforced: bool = True
    #: Initial destination address when it is not a host (DNAT public address).
    dst_ip: str | None = None
    probes: tuple[Probe, ...] = ()
    note: str = ""


# --- reply payloads for UDP probes -----------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def bacnet_read_device_name(instance: int) -> str:
    """BVLC unicast + NPDU + ReadProperty(Device <instance>, object-name)."""
    obj: int = (8 << 22) | instance
    apdu: bytes = bytes([0x00, 0x05, 0x01, 0x0C, 0x0C]) + struct.pack(">I", obj)
    apdu += bytes([0x19, 0x4D])
    npdu: bytes = bytes([0x01, 0x04])
    body: bytes = npdu + apdu
    return _b64(bytes([0x81, 0x0A]) + struct.pack(">H", 4 + len(body)) + body)


def bvlc_read_bdt() -> str:
    """BVLC Read-Broadcast-Distribution-Table: a BBMD answers with an ack."""
    return _b64(bytes([0x81, 0x02, 0x00, 0x04]))


def ntp_request() -> str:
    """A 48-byte NTPv3 client request."""
    return _b64(bytes([0x1B]) + bytes(47))


def dns_query(name: str) -> str:
    """A DNS A query for ``name``."""
    qname: bytes = b"".join(bytes([len(label)]) + label.encode() for label in name.split("."))
    header: bytes = struct.pack(">HHHHHH", 0x4E43, 0x0100, 1, 0, 0, 0)
    return _b64(header + qname + b"\x00" + struct.pack(">HH", 1, 1))


BACNET_DEVICE_INSTANCE: dict[str, int] = {
    "bas-rtu-taproom": 3001,
    "bas-coldroom": 3002,
    "bas-vent": 3003,
}
DNS_PROBE_NAME: str = "ot-svc.cherrymountain.test"

GROUPS: dict[str, tuple[str, ...]] = {
    "dmz-hosts": ("dmz-vpn", "dmz-jump"),
    "hmis": ("hmi-brewhouse", "hmi-packaging"),
    "plcs": plan.PLCS,
    "bas-devices": plan.BAS_DEVICES,
    "internet": ("inet-nuke", "vendor-ws"),
    "ops-hmi-bas-agents": (
        "scada-fuxa",
        "historian",
        "ews",
        "ot-svc",
        "hmi-brewhouse",
        "hmi-packaging",
        "bas-fuxa",
    ),
}


def _bacnet_probe(src: str, dst: str) -> Probe:
    return Probe(
        src=src,
        dst=dst,
        proto="udp",
        port=plan.BACNET_PORT,
        packet=bacnet_read_device_name(BACNET_DEVICE_INSTANCE[dst]),
    )


def _t(src: str, dst: str, *ports: int) -> tuple[Probe, ...]:
    return tuple(Probe(src=src, dst=dst, proto="tcp", port=p) for p in ports)


def _u(src: str, dst: str, port: int, packet: str) -> Probe:
    return Probe(src=src, dst=dst, proto="udp", port=port, packet=packet)


#: One representative host per routed zone that depends on ot-svc.
ZONE_REPS: dict[str, str] = {
    "DMZ": "dmz-vpn",
    "HMI": "hmi-brewhouse",
    "CTRL": "plc-cellar",
    "DERLINK": "rtu-der",
    "BAS": "bas-fuxa",
    "SOC": "wazuh",
}

CONDUITS: tuple[Conduit, ...] = (
    Conduit(
        id="corp-inet",
        text="CORP -> INET: TCP 80/443/25, UDP 123/53 (SNAT)",
        srcs=("zone:CORP",),
        dsts=("inet-nuke",),
        services=(*tcp(80, 443, 25), *udp(123, 53)),
        via=("fw-edge",),
        probes=(
            *_t("corp-ws", "inet-nuke", 80, 443, 25),
            _u("corp-ws", "inet-nuke", 123, ntp_request()),
            _u("corp-ws", "inet-nuke", 53, dns_query(DNS_PROBE_NAME)),
        ),
    ),
    Conduit(
        id="edge-ntp",
        text="fw-edge -> inet-nuke: UDP 123 (router-originated)",
        srcs=("fw-edge",),
        dsts=("inet-nuke",),
        services=udp(123),
        enforced=False,
        note="Originated by the router; no forward chain sees it.",
    ),
    Conduit(
        id="inet-wireguard",
        text="INET -> dmz-vpn: UDP 51820 (DNAT 203.0.113.2 -> 10.10.20.10)",
        srcs=("@internet",),
        dsts=("dmz-vpn",),
        services=udp(plan.WG_PORT),
        via=("fw-edge",),
        dst_ip=plan.FW_EDGE_INET_IP,
        note="Rule matches the translated destination 10.10.20.10.",
    ),
    Conduit(
        id="vendor-jump",
        text="vendor-ws (tunnel 10.99.0.2) -> dmz-jump: TCP 443",
        srcs=("vendor-ws",),
        dsts=("dmz-jump",),
        services=tcp(443),
        enforced=False,
        probes=_t("vendor-ws", "dmz-jump", 443),
        note="Intra-DMZ via dmz-vpn; WireGuard AllowedIPs restrict the peer.",
    ),
    Conduit(
        id="dmz-ot-svc",
        text="DMZ hosts -> ot-svc: UDP 123, UDP/TCP 53",
        srcs=("zone:DMZ",),
        dsts=("ot-svc",),
        services=(*udp(123, 53), *tcp(53)),
        via=("fw-ot",),
        probes=(
            *_t("dmz-vpn", "ot-svc", 53),
            *_t("dmz-jump", "ot-svc", 53),
            _u("dmz-vpn", "ot-svc", 123, ntp_request()),
            _u("dmz-vpn", "ot-svc", 53, dns_query(DNS_PROBE_NAME)),
        ),
    ),
    Conduit(
        id="ipsec",
        text="util-rtr <-> fw-edge: IKE UDP 500/4500, ESP",
        srcs=("util-rtr",),
        dsts=("fw-edge",),
        services=udp(500, 4500),
        enforced=False,
        note="Terminates on the router; the forward chain never sees it.",
    ),
    Conduit(
        id="dnp3",
        text="utility-derms -> rtu-der: TCP 20000 (DNP3 over IPsec)",
        srcs=("utility-derms",),
        dsts=("rtu-der",),
        services=tcp(20000),
        via=("fw-edge", "fw-ot"),
        probes=_t("utility-derms", "rtu-der", 20000),
    ),
    Conduit(
        id="jump-ews",
        text="dmz-jump -> ews: TCP 3389 (RDP), 22",
        srcs=("dmz-jump",),
        dsts=("ews",),
        services=tcp(3389, 22),
        via=("fw-ot",),
        probes=_t("dmz-jump", "ews", 3389, 22),
    ),
    Conduit(
        id="hmi-fuxa",
        text="HMI -> scada-fuxa, bas-fuxa: TCP 1881",
        srcs=("zone:HMI",),
        dsts=("scada-fuxa", "bas-fuxa"),
        services=tcp(1881),
        via=("fw-ot",),
        probes=tuple(
            p
            for h in ("hmi-brewhouse", "hmi-packaging")
            for d in ("scada-fuxa", "bas-fuxa")
            for p in _t(h, d, 1881)
        ),
    ),
    Conduit(
        id="hmi-historian",
        text="HMI -> historian: TCP 3000",
        srcs=("zone:HMI",),
        dsts=("historian",),
        services=tcp(3000),
        via=("fw-ot",),
        probes=(
            *_t("hmi-brewhouse", "historian", 3000),
            *_t("hmi-packaging", "historian", 3000),
        ),
    ),
    Conduit(
        id="fuxa-opcua",
        text="scada-fuxa -> plc-brewhouse: TCP 4840 (OPC UA)",
        srcs=("scada-fuxa",),
        dsts=("plc-brewhouse",),
        services=tcp(4840),
        via=("fw-ot",),
        probes=_t("scada-fuxa", "plc-brewhouse", 4840),
    ),
    Conduit(
        id="fuxa-s7",
        text="scada-fuxa -> plc-packaging: TCP 102 (S7comm)",
        srcs=("scada-fuxa",),
        dsts=("plc-packaging",),
        services=tcp(102),
        via=("fw-ot",),
        probes=_t("scada-fuxa", "plc-packaging", 102),
    ),
    Conduit(
        id="fuxa-modbus",
        text="scada-fuxa -> plc-cellar, plc-utilities, plc-power: TCP 502",
        srcs=("scada-fuxa",),
        dsts=("plc-cellar", "plc-utilities", "plc-power"),
        services=tcp(502),
        via=("fw-ot",),
        probes=tuple(
            p
            for d in ("plc-cellar", "plc-utilities", "plc-power")
            for p in _t("scada-fuxa", d, 502)
        ),
    ),
    Conduit(
        id="fuxa-bacnet",
        text="scada-fuxa -> BAS devices: UDP 47808 (BACnet/IP)",
        srcs=("scada-fuxa",),
        dsts=("@bas-devices",),
        services=udp(plan.BACNET_PORT),
        via=("fw-ot",),
        probes=tuple(_bacnet_probe("scada-fuxa", d) for d in plan.BAS_DEVICES),
    ),
    Conduit(
        id="bbmd-1",
        text="ot-svc (OPS BBMD) -> bas-bbmd: UDP 47808 (BBMD rule 1)",
        srcs=("ot-svc",),
        dsts=("bas-bbmd",),
        services=udp(plan.BACNET_PORT),
        via=("fw-ot",),
        probes=(_u("ot-svc", "bas-bbmd", plan.BACNET_PORT, bvlc_read_bdt()),),
    ),
    Conduit(
        id="bbmd-2",
        text="BAS devices -> scada-fuxa, ews: UDP 47808 (BBMD rule 2, required)",
        srcs=("@bas-devices",),
        dsts=("scada-fuxa", "ews"),
        services=udp(plan.BACNET_PORT),
        via=("fw-ot",),
        note="Unicast I-Am to the client; FUXA sends no reply, so no probe.",
    ),
    Conduit(
        id="bbmd-3",
        text="bas-bbmd -> ot-svc: UDP 47808 (BBMD rule 3, optional)",
        srcs=("bas-bbmd",),
        dsts=("ot-svc",),
        services=udp(plan.BACNET_PORT),
        via=("fw-ot",),
        probes=(_u("bas-bbmd", "ot-svc", plan.BACNET_PORT, bvlc_read_bdt()),),
    ),
    Conduit(
        id="ews-plc",
        text="ews -> CTRL PLCs: TCP 8443 (OpenPLC REST), 502, 4840, 102",
        srcs=("ews",),
        dsts=("zone:CTRL",),
        services=tcp(8443, 502, 4840, 102),
        via=("fw-ot",),
        probes=(
            *(p for d in plan.PLCS for p in _t("ews", d, 8443)),
            *(p for d in ("plc-cellar", "plc-utilities", "plc-power") for p in _t("ews", d, 502)),
            *_t("ews", "plc-brewhouse", 4840),
            *_t("ews", "plc-packaging", 102),
        ),
    ),
    Conduit(
        id="ews-bas-fuxa",
        text="ews -> bas-fuxa: TCP 1881 (BAS engineering)",
        srcs=("ews",),
        dsts=("bas-fuxa",),
        services=tcp(1881),
        via=("fw-ot",),
        probes=_t("ews", "bas-fuxa", 1881),
    ),
    Conduit(
        id="ews-bacnet",
        text="ews -> BAS devices: UDP 47808 (unicast reads)",
        srcs=("ews",),
        dsts=("@bas-devices",),
        services=udp(plan.BACNET_PORT),
        via=("fw-ot",),
        probes=tuple(_bacnet_probe("ews", d) for d in plan.BAS_DEVICES),
    ),
    Conduit(
        id="bas-historian",
        text="bas-fuxa -> historian: TCP 8086 (BAS DAQ)",
        srcs=("bas-fuxa",),
        dsts=("historian",),
        services=tcp(8086),
        via=("fw-ot",),
        probes=_t("bas-fuxa", "historian", 8086),
    ),
    Conduit(
        id="ot-svc-services",
        text="Routed OT zones -> ot-svc: UDP 123, UDP/TCP 53, UDP 514",
        srcs=("zone:HMI", "zone:CTRL", "zone:DERLINK", "zone:BAS", "zone:SOC"),
        dsts=("ot-svc",),
        services=(*udp(123, 53, 514), *tcp(53)),
        via=("fw-ot",),
        probes=tuple(
            p
            for z in ("HMI", "CTRL", "DERLINK", "BAS", "SOC")
            for p in (
                *_t(ZONE_REPS[z], "ot-svc", 53),
                _u(ZONE_REPS[z], "ot-svc", 123, ntp_request()),
                _u(ZONE_REPS[z], "ot-svc", 53, dns_query(DNS_PROBE_NAME)),
            )
        ),
        note="fw-ot itself reaches ot-svc through its own OPS interface.",
    ),
    Conduit(
        id="syslog-relay",
        text="ot-svc -> wazuh: UDP 514 (syslog relay)",
        srcs=("ot-svc",),
        dsts=("wazuh",),
        services=udp(514),
        via=("fw-ot",),
        note="One-way; checked statically and by Wazuh receiving it.",
    ),
    Conduit(
        id="edge-syslog",
        text="fw-edge -> wazuh: UDP 514 (firewall logs, including drops)",
        srcs=("fw-edge",),
        dsts=("wazuh",),
        services=udp(514),
        via=("fw-ot",),
        note="One-way; carried through fw-ot from the DMZ.",
    ),
    Conduit(
        id="ot-fw-syslog",
        text="fw-ot -> wazuh: UDP 514 (firewall logs, router-originated)",
        srcs=("fw-ot",),
        dsts=("wazuh",),
        services=udp(514),
        enforced=False,
        note="Originated by the router; no forward chain sees it.",
    ),
    Conduit(
        id="dmz-wazuh",
        text="DMZ hosts -> wazuh: TCP 1514/1515 (agents)",
        srcs=("zone:DMZ",),
        dsts=("wazuh",),
        services=tcp(1514, 1515),
        via=("fw-ot",),
        probes=tuple(p for h in ("dmz-vpn", "dmz-jump") for p in _t(h, "wazuh", 1514, 1515)),
    ),
    Conduit(
        id="ops-wazuh",
        text="OPS/HMI/BAS Linux hosts -> wazuh: TCP 1514/1515 (agents)",
        srcs=("@ops-hmi-bas-agents",),
        dsts=("wazuh",),
        services=tcp(1514, 1515),
        via=("fw-ot",),
        probes=tuple(
            p
            for h in (
                "scada-fuxa",
                "historian",
                "ews",
                "hmi-brewhouse",
                "hmi-packaging",
                "bas-fuxa",
            )
            for p in _t(h, "wazuh", 1514, 1515)
        ),
        note="Hosts in OPS reach wazuh through fw-ot like the others.",
    ),
    Conduit(
        id="soc-analyst",
        text="soc-analyst -> malcolm, wazuh: intra-zone",
        srcs=("soc-analyst",),
        dsts=("malcolm", "wazuh"),
        services=tcp(443),
        enforced=False,
        probes=(
            *_t("soc-analyst", "malcolm", 443),
            *_t("soc-analyst", "wazuh", 443),
        ),
        note="Same VLAN; no firewall.",
    ),
)

CONDUIT_BY_ID: dict[str, Conduit] = {c.id: c for c in CONDUITS}


class BlockedProbe(BaseModel):
    """A path that must stay blocked, tested from ``src`` over TCP."""

    model_config = ConfigDict(frozen=True)

    src: str
    dst: str
    port: int
    why: str


BLOCKED_PROBES: tuple[BlockedProbe, ...] = (
    BlockedProbe(src="corp-ws", dst="ot-svc", port=53, why="CORP reaches only the internet"),
    BlockedProbe(src="corp-ws", dst="ews", port=3389, why="CORP has no path to OPS"),
    BlockedProbe(src="corp-ws", dst="scada-fuxa", port=1881, why="CORP has no path to SCADA"),
    BlockedProbe(src="corp-ws", dst="plc-cellar", port=502, why="CORP has no path to CTRL"),
    BlockedProbe(src="corp-ws", dst="dmz-jump", port=443, why="CORP has no path to the DMZ"),
    BlockedProbe(
        src="vendor-ws", dst="ews", port=3389, why="Vendors reach OPS only through Guacamole"
    ),
    BlockedProbe(
        src="vendor-ws", dst="plc-cellar", port=502, why="Vendors have no direct PLC path"
    ),
    BlockedProbe(
        src="utility-derms", dst="ews", port=3389, why="The utility link carries only DNP3"
    ),
    BlockedProbe(
        src="utility-derms", dst="plc-cellar", port=502, why="The utility link reaches only rtu-der"
    ),
    BlockedProbe(
        src="utility-derms", dst="rtu-der", port=22, why="Only TCP 20000 crosses the IPsec conduit"
    ),
    BlockedProbe(
        src="dmz-jump", dst="plc-cellar", port=502, why="Only ews is reachable from the jump host"
    ),
    BlockedProbe(
        src="dmz-jump", dst="scada-fuxa", port=1881, why="Only ews is reachable from the jump host"
    ),
    BlockedProbe(src="dmz-jump", dst="ews", port=8443, why="Only RDP and SSH reach ews"),
    BlockedProbe(src="dmz-vpn", dst="ews", port=3389, why="Only dmz-jump reaches ews"),
    BlockedProbe(src="hmi-brewhouse", dst="plc-cellar", port=502, why="HMIs reach SCADA, not PLCs"),
    BlockedProbe(src="hmi-brewhouse", dst="ews", port=3389, why="HMIs have no engineering access"),
    BlockedProbe(
        src="hmi-brewhouse", dst="historian", port=8086, why="HMIs get read-only Grafana only"
    ),
    BlockedProbe(src="scada-fuxa", dst="rio-cellar", port=502, why="FIELD networks are unrouted"),
    BlockedProbe(
        src="scada-fuxa", dst="plc-cellar", port=8443, why="SCADA polls PLC protocols only"
    ),
    BlockedProbe(src="historian", dst="plc-cellar", port=502, why="The historian has no PLC path"),
    BlockedProbe(
        src="historian", dst="bas-fuxa", port=1881, why="Only ews and the HMIs reach bas-fuxa"
    ),
    BlockedProbe(src="ews", dst="hmi-brewhouse", port=22, why="OPS does not initiate to HMI"),
    BlockedProbe(src="ews", dst="rio-cellar", port=502, why="FIELD networks are unrouted"),
    BlockedProbe(src="plc-cellar", dst="scada-fuxa", port=1881, why="PLCs initiate nothing upward"),
    BlockedProbe(src="plc-cellar", dst="historian", port=8086, why="PLCs initiate nothing upward"),
    BlockedProbe(
        src="rtu-der",
        dst="utility-derms",
        port=20000,
        why="DNP3 is initiated from the utility side",
    ),
    BlockedProbe(src="rtu-der", dst="scada-fuxa", port=1881, why="DERLINK reaches only ot-svc"),
    BlockedProbe(src="bas-fuxa", dst="plc-cellar", port=502, why="BAS has no path to CTRL"),
    BlockedProbe(src="bas-fuxa", dst="ews", port=22, why="BAS does not initiate to ews"),
    BlockedProbe(src="bas-fuxa", dst="scada-fuxa", port=1881, why="BAS does not initiate to SCADA"),
    BlockedProbe(src="wazuh", dst="ews", port=22, why="SOC initiates nothing into OPS"),
    BlockedProbe(src="wazuh", dst="plc-cellar", port=502, why="SOC initiates nothing into CTRL"),
)


def expand_selector(sel: str, variant: str) -> list[str]:
    """Host names a selector stands for in ``variant`` (routers included)."""
    names: set[str] = {n.name for n in plan.nodes_for(variant)}
    if sel.startswith("zone:"):
        vlan: str = sel.removeprefix("zone:")
        return [n.name for n in plan.hosts_in(vlan, variant) if not n.is_router]
    if sel.startswith("@"):
        return [h for h in GROUPS[sel.removeprefix("@")] if h in names]
    return [sel] if sel in names else []


def expand(conduit: Conduit, variant: str) -> list[tuple[str, str, Proto, int]]:
    """Every (src host, dst host, proto, port) a conduit allows in ``variant``."""
    out: list[tuple[str, str, Proto, int]] = []
    for s in conduit.srcs:
        for d in conduit.dsts:
            for src in expand_selector(s, variant):
                for dst in expand_selector(d, variant):
                    if src == dst:
                        continue
                    for svc in conduit.services:
                        out.append((src, dst, svc.proto, svc.port))
    return out


def expected_flows(variant: str) -> set[tuple[str, str, Proto, int]]:
    """Cross-zone flows the firewalls must allow (enforced conduits only)."""
    flows: set[tuple[str, str, Proto, int]] = set()
    for c in CONDUITS:
        if c.enforced:
            flows.update(expand(c, variant))
    return flows
