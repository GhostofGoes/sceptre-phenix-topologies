"""The DESIGN.md IP plan and node inventory, as data.

Sections 10.2 (VLAN and IP plan, routing), 11 (node inventory and resources),
and 15.3 (modules, boot order, autotunnel) are encoded here once. The topology
YAML is checked against this table (``checks.check_plan``), so a typo in an
address, a VLAN, a route, or a resource fails the check. Section 10.3 (the
conduit matrix) is in ``conduits``.

Boot order is the order of ``NODES`` within each module: firewalls and routers,
services, sim-ctl, the sim federates, field devices, PLCs, FUXA, HMIs.
"""

from __future__ import annotations

import ipaddress
from typing import Literal

from pydantic import BaseModel, ConfigDict

Module = Literal["core", "soc", "remote-access"]
Kind = Literal["VirtualMachine", "Router", "Firewall"]

#: The two lite and full variants and the modules each is built from.
VARIANT_MODULES: dict[str, tuple[Module, ...]] = {
    "full": ("core", "soc", "remote-access"),
    "lite": ("core",),
}

#: DNS and time for the plant (ot-svc) and for the "internet" side (inet-nuke).
OT_SVC_IP: str = "10.20.30.13"
INET_NUKE_IP: str = "203.0.113.10"
WAZUH_IP: str = "10.40.10.11"
FW_OT_DMZ_IP: str = "10.10.20.2"
FW_EDGE_DMZ_IP: str = "10.10.20.1"
FW_EDGE_INET_IP: str = "203.0.113.2"
UTIL_RTR_INET_IP: str = "203.0.113.3"
ISP_RTR_INET_IP: str = "203.0.113.1"
ISP_RTR_VENDOR_IP: str = "198.51.100.1"
WG_SERVER_IP: str = "10.99.0.1"
WG_VENDOR_IP: str = "10.99.0.2"
WG_PORT: int = 51820
BACNET_PORT: int = 47808


class Vlan(BaseModel):
    """One row of the section 10.2 VLAN table."""

    model_config = ConfigDict(frozen=True)

    name: str
    vid: int
    subnet: str
    gateway: str | None = None

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.IPv4Network(self.subnet)


VLANS: dict[str, Vlan] = {
    v.name: v
    for v in (
        Vlan(name="INET", vid=101, subnet="203.0.113.0/24", gateway="203.0.113.1"),
        Vlan(name="VENDOR", vid=102, subnet="198.51.100.0/24", gateway="198.51.100.1"),
        Vlan(name="UTIL", vid=103, subnet="192.0.2.0/24", gateway="192.0.2.1"),
        Vlan(name="CORP", vid=104, subnet="10.10.10.0/24", gateway="10.10.10.1"),
        Vlan(name="DMZ", vid=105, subnet="10.10.20.0/24", gateway="10.10.20.1"),
        Vlan(name="OPS", vid=106, subnet="10.20.30.0/24", gateway="10.20.30.1"),
        Vlan(name="HMI", vid=107, subnet="10.20.20.0/24", gateway="10.20.20.1"),
        Vlan(name="CTRL", vid=108, subnet="10.20.10.0/24", gateway="10.20.10.1"),
        Vlan(name="FIELD-BH", vid=109, subnet="10.20.11.0/24"),
        Vlan(name="FIELD-CEL", vid=110, subnet="10.20.12.0/24"),
        Vlan(name="FIELD-PKG", vid=111, subnet="10.20.13.0/24"),
        Vlan(name="FIELD-UTL", vid=112, subnet="10.20.14.0/24"),
        Vlan(name="DERLINK", vid=113, subnet="10.20.15.0/24", gateway="10.20.15.1"),
        Vlan(name="FIELD-PWR", vid=114, subnet="10.20.16.0/24"),
        Vlan(name="BAS", vid=115, subnet="10.30.10.0/24", gateway="10.30.10.1"),
        Vlan(name="SOC", vid=116, subnet="10.40.10.0/24", gateway="10.40.10.1"),
        Vlan(name="MGMT", vid=117, subnet="172.16.0.0/16"),
        # Not a plant VLAN: the mirror app's `mirrorVLAN`, with the GRE
        # subnet it assigns. It has no static members.
        Vlan(name="MIRROR", vid=118, subnet="172.30.0.0/16"),
    )
}

#: The WireGuard tunnel network (not a VLAN).
WG_SUBNET: str = "10.99.0.0/24"

#: The workflow's defaultBridge per variant. phenix lets only one stored
#: experiment use a bridge other than `phenix`, running or not (the experiment
#: config hook in api/experiment and CheckBridge in api/workflow), so each
#: variant needs its own or the second variant cannot be created while the
#: first one exists. A non-default bridge also enables the Netflow tab.
BRIDGES: dict[str, str] = {
    "full": "cherrymtn",
    "lite": "cherrymtn-lite",
    "ci": "cherrymtn-ci",
}
#: phenix's MaxBridgeNameLength: a bridge is a Linux interface.
MAX_BRIDGE_NAME: int = 15

#: Where VyOS mounts the image's persistence partition (partition 3, the one a
#: topology injects into). An injection to /boot/vyos/rw/... is this path plus
#: the destination in the running router; /config there is a bind mount of
#: /opt/vyatta/etc/config, which hides what was injected below rw/config.
VYOS_PERSISTENCE: str = "/usr/lib/live/mount/persistence"
VYOS_INJECT_ROOT: str = "/boot/vyos/rw/"

#: Zones that are routed by a firewall (not FIELD islands, MGMT, or MIRROR).
OT_ROUTED_VLANS: tuple[str, ...] = ("OPS", "HMI", "CTRL", "DERLINK", "BAS", "SOC")


class Nic(BaseModel):
    """One interface: IFn (hosts), ethN (VyOS routers), or mgmt."""

    model_config = ConfigDict(frozen=True)

    name: str
    vlan: str
    ip: str
    mask: int
    gateway: str | None = None
    dns: tuple[str, ...] = ()


class Route(BaseModel):
    model_config = ConfigDict(frozen=True)

    destination: str
    next: str


class Node(BaseModel):
    """One row of the section 11 inventory plus its section 10.2 addressing."""

    model_config = ConfigDict(frozen=True)

    name: str
    module: Module
    kind: Kind = "VirtualMachine"
    image: str
    vcpus: int
    mem_gb: int
    zone: str
    description: str
    nics: tuple[Nic, ...]
    routes: tuple[Route, ...] = ()
    autotunnel: tuple[str, ...] = ()
    delay_c2: tuple[str, ...] = ()

    @property
    def is_router(self) -> bool:
        return self.kind != "VirtualMachine"

    def nic(self, name: str) -> Nic:
        for n in self.nics:
            if n.name == name:
                return n
        raise KeyError(f"{self.name} has no interface {name}")

    def nic_on(self, vlan: str) -> Nic | None:
        for n in self.nics:
            if n.vlan == vlan:
                return n
        return None

    @property
    def primary_ip(self) -> str:
        """The address other hosts use: IF0 (routers: the first interface)."""
        return self.nics[0].ip


def _mask(vlan: str) -> int:
    return VLANS[vlan].network.prefixlen


def _nic(
    name: str,
    vlan: str,
    ip: str,
    gateway: str | None = None,
    dns: tuple[str, ...] = (),
) -> Nic:
    return Nic(name=name, vlan=vlan, ip=ip, mask=_mask(vlan), gateway=gateway, dns=dns)


OT_ROUTES: tuple[Route, ...] = (
    Route(destination="10.20.0.0/16", next=FW_OT_DMZ_IP),
    Route(destination="10.30.10.0/24", next=FW_OT_DMZ_IP),
    Route(destination="10.40.10.0/24", next=FW_OT_DMZ_IP),
)


def _vm(
    name: str,
    module: Module,
    image: str,
    cpu: int,
    mem: int,
    zone: str,
    description: str,
    plant: list[tuple[str, str]],
    *,
    mgmt: str | None = None,
    gateway: str | None = None,
    dns: str | None = OT_SVC_IP,
    routes: tuple[Route, ...] = (),
    autotunnel: tuple[str, ...] = (),
    delay_c2: tuple[str, ...] = (),
) -> Node:
    """A VM: plant NICs IF0.. (default gateway on IF0), then the mgmt NIC."""
    nics: list[Nic] = []
    for i, (vlan, ip) in enumerate(plant):
        nics.append(
            _nic(
                f"IF{i}",
                vlan,
                ip,
                gateway=gateway if i == 0 else None,
                dns=(dns,) if (i == 0 and dns) else (),
            )
        )
    if mgmt:
        nics.append(_nic("mgmt", "MGMT", mgmt))
    return Node(
        name=name,
        module=module,
        image=image,
        vcpus=cpu,
        mem_gb=mem,
        zone=zone,
        description=description,
        nics=tuple(nics),
        routes=routes,
        autotunnel=autotunnel,
        delay_c2=delay_c2,
    )


def _rtr(
    name: str,
    kind: Kind,
    zone: str,
    description: str,
    ifaces: list[tuple[str, str]],
    routes: tuple[Route, ...],
) -> Node:
    """A VyOS router or firewall: interfaces named eth0.. in topology order."""
    return Node(
        name=name,
        module="core",
        kind=kind,
        image="vyos.qc2",
        vcpus=1,
        mem_gb=2,
        zone=zone,
        description=description,
        nics=tuple(_nic(f"eth{i}", v, ip) for i, (v, ip) in enumerate(ifaces)),
        routes=routes,
    )


PLCS: tuple[str, ...] = (
    "plc-brewhouse",
    "plc-cellar",
    "plc-packaging",
    "plc-utilities",
    "plc-power",
)
POWER_DEVICES: tuple[str, ...] = ("relay-main", "pv-inverter", "bess-pcs", "gen-ats")
BAS_DEVICES: tuple[str, ...] = ("bas-rtu-taproom", "bas-coldroom", "bas-vent")
RIOS: tuple[str, ...] = (
    "rio-brewhouse",
    "rio-cellar",
    "rio-packaging",
    "rio-utilities",
    "glycol-chiller",
)

# fmt: off
_FIELD_NODES: list[Node] = [
    _vm("rio-brewhouse", "core", "cm-otsim.qc2", 1, 1, "field-bh", "Brewhouse remote I/O (ot-sim fd-server)", [("FIELD-BH", "10.20.11.10")], mgmt="172.16.20.11", dns=None, delay_c2=("sim-ctl",)),
    _vm("rio-cellar", "core", "cm-otsim.qc2", 1, 1, "field-cel", "Cellar remote I/O (ot-sim fd-server)", [("FIELD-CEL", "10.20.12.10")], mgmt="172.16.20.12", dns=None, delay_c2=("sim-ctl",)),
    _vm("rio-packaging", "core", "cm-otsim.qc2", 1, 1, "field-pkg", "Packaging remote I/O (ot-sim fd-server)", [("FIELD-PKG", "10.20.13.10")], mgmt="172.16.20.13", dns=None, delay_c2=("sim-ctl",)),
    _vm("rio-utilities", "core", "cm-otsim.qc2", 1, 1, "field-utl", "Utilities remote I/O (ot-sim fd-server)", [("FIELD-UTL", "10.20.14.10")], mgmt="172.16.20.14", dns=None, delay_c2=("sim-ctl",)),
    _vm("glycol-chiller", "core", "cm-otsim.qc2", 1, 1, "field-utl", "Chiller OEM controller (ot-sim)", [("FIELD-UTL", "10.20.14.11")], mgmt="172.16.20.15", dns=None, delay_c2=("sim-ctl",)),
    _vm("relay-main", "core", "cm-otsim.qc2", 1, 1, "field-pwr", "Main breaker protective relay and meter (ot-sim, Modbus and Telnet)", [("FIELD-PWR", "10.20.16.20")], mgmt="172.16.20.21", dns=None, delay_c2=("sim-ctl",)),
    _vm("pv-inverter", "core", "cm-otsim.qc2", 1, 1, "field-pwr", "PV inverter (ot-sim)", [("FIELD-PWR", "10.20.16.21")], mgmt="172.16.20.22", dns=None, delay_c2=("sim-ctl",)),
    _vm("bess-pcs", "core", "cm-otsim.qc2", 1, 1, "field-pwr", "BESS power conversion system (ot-sim)", [("FIELD-PWR", "10.20.16.22")], mgmt="172.16.20.23", dns=None, delay_c2=("sim-ctl",)),
    _vm("gen-ats", "core", "cm-otsim.qc2", 1, 1, "field-pwr", "Genset controller and ATS (ot-sim)", [("FIELD-PWR", "10.20.16.23")], mgmt="172.16.20.24", dns=None, delay_c2=("sim-ctl",)),
    _vm("bas-rtu-taproom", "core", "cm-otsim.qc2", 1, 1, "bas", "RTU-1 controller (ot-sim and BACnet device 3001)", [("BAS", "10.30.10.20")], mgmt="172.16.20.31", gateway="10.30.10.1", delay_c2=("sim-ctl",)),
    _vm("bas-coldroom", "core", "cm-otsim.qc2", 1, 1, "bas", "Cold room controller (ot-sim and BACnet device 3002)", [("BAS", "10.30.10.21")], mgmt="172.16.20.32", gateway="10.30.10.1", delay_c2=("sim-ctl",)),
    _vm("bas-vent", "core", "cm-otsim.qc2", 1, 1, "bas", "Ventilation and CO2 controller (ot-sim and BACnet device 3003)", [("BAS", "10.30.10.22")], mgmt="172.16.20.33", gateway="10.30.10.1", delay_c2=("sim-ctl",)),
    _vm("rtu-der", "core", "cm-otsim.qc2", 1, 1, "derlink", "DER gateway (ot-sim FEP: DNP3 outstation and Modbus client)", [("DERLINK", "10.20.15.10"), ("FIELD-PWR", "10.20.16.2")], gateway="10.20.15.1", delay_c2=("sim-ctl",)),
]

NODES: tuple[Node, ...] = (
    # 1. Firewalls and routers. isp-rtr is first because the vrouter app keeps
    # the NTP address of the first router that has one for every later router
    # (see the README), and the INET-side address is the right one for most.
    _rtr("isp-rtr", "Router", "inet", "ShadeCast Cable edge router (the internet); netem on the WAN legs", [("INET", ISP_RTR_INET_IP), ("VENDOR", ISP_RTR_VENDOR_IP)], ()),
    _rtr("util-rtr", "Router", "inet", "Summit Valley Power router, IPsec peer of fw-edge", [("INET", UTIL_RTR_INET_IP), ("UTIL", "192.0.2.1")], (Route(destination="0.0.0.0/0", next=ISP_RTR_INET_IP),)),
    _rtr("fw-edge", "Firewall", "edge", "IT/Internet firewall: NAT, WireGuard DNAT, IPsec to util-rtr", [("INET", FW_EDGE_INET_IP), ("CORP", "10.10.10.1"), ("DMZ", FW_EDGE_DMZ_IP)], (Route(destination="0.0.0.0/0", next=ISP_RTR_INET_IP), *(Route(destination=r.destination, next=FW_OT_DMZ_IP) for r in OT_ROUTES))),
    _rtr("fw-ot", "Firewall", "ot-edge", "OT firewall: every zone conduit of the conduit matrix", [("DMZ", FW_OT_DMZ_IP), ("OPS", "10.20.30.1"), ("HMI", "10.20.20.1"), ("CTRL", "10.20.10.1"), ("DERLINK", "10.20.15.1"), ("BAS", "10.30.10.1"), ("SOC", "10.40.10.1")], (Route(destination="0.0.0.0/0", next=FW_EDGE_DMZ_IP),)),
    # 2. Services.
    _vm("ot-svc", "core", "cm-svc.qc2", 1, 1, "ops", "NTP (chrony), DNS (dnsmasq), syslog relay, OPS BBMD (BACpypes3)", [("OPS", OT_SVC_IP)], gateway="10.20.30.1"),
    _vm("inet-nuke", "core", "cm-svc-protonuke.qc2", 1, 1, "inet", "Internet web and SMTP server (protonuke), plus the public NTP and DNS stand-in", [("INET", INET_NUKE_IP)], gateway=ISP_RTR_INET_IP, dns=None),
    _vm("bas-bbmd", "core", "cm-svc.qc2", 1, 1, "bas", "BAS BBMD (BACpypes3); its BDT lists itself and ot-svc", [("BAS", "10.30.10.5")], gateway="10.30.10.1"),
    # 3. sim-ctl, then the federates.
    _vm("sim-ctl", "core", "cm-sim.qc2", 2, 6, "mgmt", "HELICS root broker, weather, event API, ground-truth OpenSearch and Grafana, cm-viz 3D view, chronyd for MGMT", [], mgmt="172.16.10.1", autotunnel=("13001:3000", "18080:8080", "18090:8090", "19200:9200")),
    _vm("sim-process", "core", "cm-sim.qc2", 2, 3, "mgmt", "Process federates (HELICS)", [], mgmt="172.16.10.2", delay_c2=("sim-ctl",)),
    _vm("sim-power", "core", "cm-sim.qc2", 2, 3, "mgmt", "OpenDSS power federate (HELICS)", [], mgmt="172.16.10.3", delay_c2=("sim-ctl",)),
    _vm("sim-building", "core", "cm-sim.qc2", 2, 3, "mgmt", "Building federate (HELICS)", [], mgmt="172.16.10.4", delay_c2=("sim-ctl",)),
    # 4. Field devices (delay on sim-ctl).
    *_FIELD_NODES,
    # 5. PLCs (delay on their I/O devices).
    _vm("plc-brewhouse", "core", "cm-openplc.qc2", 1, 2, "ctrl", "OpenPLC v4 (OPC UA plugin)", [("CTRL", "10.20.10.11"), ("FIELD-BH", "10.20.11.1")], gateway="10.20.10.1", delay_c2=("rio-brewhouse",)),
    _vm("plc-cellar", "core", "cm-openplc.qc2", 1, 1, "ctrl", "OpenPLC v4", [("CTRL", "10.20.10.12"), ("FIELD-CEL", "10.20.12.1")], gateway="10.20.10.1", delay_c2=("rio-cellar",)),
    _vm("plc-packaging", "core", "cm-openplc.qc2", 1, 1, "ctrl", "OpenPLC v4 (PackML, S7comm plugin)", [("CTRL", "10.20.10.13"), ("FIELD-PKG", "10.20.13.1")], gateway="10.20.10.1", delay_c2=("rio-packaging",)),
    _vm("plc-utilities", "core", "cm-openplc.qc2", 1, 1, "ctrl", "OpenPLC v4", [("CTRL", "10.20.10.14"), ("FIELD-UTL", "10.20.14.1")], gateway="10.20.10.1", delay_c2=("rio-utilities", "glycol-chiller")),
    _vm("plc-power", "core", "cm-openplc.qc2", 1, 1, "ctrl", "OpenPLC v4", [("CTRL", "10.20.10.15"), ("FIELD-PWR", "10.20.16.1")], gateway="10.20.10.1", delay_c2=POWER_DEVICES),
    # DNP3 master on the utility side (after the outstation).
    _vm("utility-derms", "core", "cm-otsim.qc2", 1, 1, "util", "Utility DERMS (ot-sim DNP3 master)", [("UTIL", "192.0.2.10")], gateway="192.0.2.1", dns=None, delay_c2=("rtu-der",)),
    # 6. FUXA and the other OPS and BAS servers.
    _vm("scada-fuxa", "core", "cm-fuxa.qc2", 2, 4, "ops", "Process SCADA: FUXA, batch engine, alarms", [("OPS", "10.20.30.10")], gateway="10.20.30.1", autotunnel=("11881:1881",), delay_c2=PLCS),
    _vm("historian", "core", "cm-historian.qc2", 2, 6, "ops", "InfluxDB 2, Telegraf, Grafana, Mosquitto", [("OPS", "10.20.30.11")], gateway="10.20.30.1", autotunnel=("13000:3000",)),
    _vm("ews", "core", "cm-ews.qc2", 2, 3, "ops", "Engineering workstation: OpenPLC Editor, Wireshark, BAC0, mbpoll, xrdp", [("OPS", "10.20.30.12")], gateway="10.20.30.1"),
    _vm("bas-fuxa", "core", "cm-fuxa.qc2", 2, 3, "bas", "BAS front end (FUXA instance 2)", [("BAS", "10.30.10.10")], gateway="10.30.10.1", autotunnel=("11882:1881",), delay_c2=BAS_DEVICES),
    # 7. HMIs.
    _vm("hmi-brewhouse", "core", "cm-desktop.qc2", 2, 3, "hmi", "Brewhouse and cellar operator kiosk", [("HMI", "10.20.20.10")], gateway="10.20.20.1", delay_c2=("scada-fuxa",)),
    _vm("hmi-packaging", "core", "cm-desktop.qc2", 2, 3, "hmi", "Packaging and utilities operator kiosk", [("HMI", "10.20.20.11")], gateway="10.20.20.1", delay_c2=("scada-fuxa",)),
    _vm("corp-ws", "core", "cm-desktop.qc2", 2, 3, "corp", "Office workstation (protonuke client)", [("CORP", "10.10.10.10")], gateway="10.10.10.1", dns=INET_NUKE_IP),
    # SOC module.
    _vm("wazuh", "soc", "cm-wazuh.qc2", 4, 8, "soc", "Wazuh manager, indexer, and dashboard", [("SOC", WAZUH_IP)], gateway="10.40.10.1", autotunnel=("18444:443",)),
    _vm("malcolm", "soc", "cm-malcolm.qc2", 8, 24, "soc", "Malcolm: Zeek and ICSNPP, Suricata, Arkime, OpenSearch (mirror NIC added by the mirror app)", [("SOC", "10.40.10.10")], gateway="10.40.10.1", autotunnel=("18443:443",)),
    _vm("soc-analyst", "soc", "cm-desktop.qc2", 2, 3, "soc", "Analyst workstation", [("SOC", "10.40.10.12")], gateway="10.40.10.1", delay_c2=("wazuh", "malcolm")),
    # Remote-access module.
    _vm("dmz-vpn", "remote-access", "cm-wireguard.qc2", 1, 1, "dmz", "WireGuard gateway (DNAT target of fw-edge)", [("DMZ", "10.10.20.10")], gateway=FW_EDGE_DMZ_IP, routes=OT_ROUTES),
    _vm("dmz-jump", "remote-access", "cm-guacamole.qc2", 2, 3, "dmz", "Guacamole jump host (TOTP, session recording)", [("DMZ", "10.10.20.11")], gateway=FW_EDGE_DMZ_IP, routes=(*OT_ROUTES, Route(destination=WG_SUBNET, next="10.10.20.10")), delay_c2=("dmz-vpn",)),
    _vm("vendor-ws", "remote-access", "cm-desktop.qc2", 2, 3, "vendor", "Ridgeline Process Controls technician laptop (WireGuard client, browser)", [("VENDOR", "198.51.100.10")], gateway=ISP_RTR_VENDOR_IP, dns=INET_NUKE_IP, delay_c2=("dmz-vpn",)),
)
# fmt: on

NODE_BY_NAME: dict[str, Node] = {n.name: n for n in NODES}

# Section 14: Linux hosts with a Wazuh agent.
WAZUH_AGENT_HOSTS: tuple[str, ...] = (
    "scada-fuxa",
    "historian",
    "ews",
    "ot-svc",
    "hmi-brewhouse",
    "hmi-packaging",
    "bas-fuxa",
    "dmz-vpn",
    "dmz-jump",
)


def nodes_for(variant: str) -> list[Node]:
    """The nodes of a variant, in boot order (core, then soc, then remote)."""
    modules: tuple[Module, ...] = VARIANT_MODULES[variant]
    return [n for m in modules for n in NODES if n.module == m]


def totals(variant: str) -> tuple[int, int, int]:
    """(VMs, vCPUs, GB) of a variant."""
    nodes: list[Node] = nodes_for(variant)
    return len(nodes), sum(n.vcpus for n in nodes), sum(n.mem_gb for n in nodes)


def hosts_in(vlan: str, variant: str = "full") -> list[Node]:
    return [n for n in nodes_for(variant) if n.nic_on(vlan) is not None]
