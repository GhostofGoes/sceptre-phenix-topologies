"""The checks: topology, scenario, and workflow against the IP plan and the
conduit matrix.

Every check returns ``Finding`` objects. Errors fail the run; notes report
what could not be checked yet (for example a generated app block that plantgen
has not emitted for the variant).
"""

from __future__ import annotations

import base64
import ipaddress
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from netcheck import conduits, plan
from netcheck import model as m
from netcheck.sim import Net, Verdict, flow, trace

#: DESIGN.md section 11 totals: (VMs, vCPUs, GB).
DESIGN_TOTALS: dict[str, tuple[int, int, int]] = {"full": (43, 67, 113), "lite": (37, 48, 71)}
CORE_NODE_COUNT: int = 37

#: Ports swept for flows that no conduit allows, beyond the conduit ports.
EXTRA_SERVICES: tuple[conduits.Service, ...] = (
    *conduits.tcp(22, 23, 80, 443, 445, 1883, 3389, 8080, 8086),
    *conduits.udp(161, 69),
)
BLOCKED_RESULTS: frozenset[str] = frozenset({"dropped", "no-route"})


class Finding(BaseModel):
    check: str
    message: str
    severity: Literal["error", "note"] = "error"


def _err(check: str, message: str) -> Finding:
    return Finding(check=check, message=message)


def _note(check: str, message: str) -> Finding:
    return Finding(check=check, message=message, severity="note")


# --- the IP plan -------------------------------------------------------------


def check_plan(v: m.Variant) -> list[Finding]:
    """Nodes, interfaces, routes, and resources equal the plan."""
    out: list[Finding] = []
    c: str = f"{v.name}/plan"
    want: list[plan.Node] = plan.nodes_for(v.name)
    have: dict[str, m.TNode] = {n.hostname: n for n in v.topology.nodes}
    if v.topology.names() != [n.name for n in want]:
        extra: list[str] = sorted(set(have) - {n.name for n in want})
        missing: list[str] = sorted({n.name for n in want} - set(have))
        out.append(_err(c, f"node list differs from the plan: missing {missing}, extra {extra}"))
        if not extra and not missing:
            out.append(_err(c, "node order (boot order) differs from the plan"))
    totals: tuple[int, int, int] = (
        len(v.topology.nodes),
        sum(n.vcpus for n in v.topology.nodes),
        sum(n.memory_mb for n in v.topology.nodes) // 1024,
    )
    if v.name in DESIGN_TOTALS and totals != DESIGN_TOTALS[v.name]:
        out.append(
            _err(
                c, f"totals (VMs, vCPUs, GB) {totals} differ from DESIGN.md {DESIGN_TOTALS[v.name]}"
            )
        )
    for p in want:
        n: m.TNode | None = have.get(p.name)
        if n is None:
            continue
        out += _check_node(c, p, n)
    return out


def _check_node(c: str, p: plan.Node, n: m.TNode) -> list[Finding]:
    out: list[Finding] = []
    who: str = p.name

    def diff(what: str, got: object, want: object) -> None:
        if got != want:
            out.append(_err(c, f"{who}: {what} is {got!r}, plan says {want!r}"))

    diff("type", n.type, p.kind)
    diff("image", n.image, p.image)
    diff("vcpus", n.vcpus, p.vcpus)
    diff("memory (MB)", n.memory_mb, p.mem_gb * 1024)
    diff("zone label", n.labels.get("zone"), p.zone)
    diff("os_type", n.os_type, "vyos" if p.is_router else "linux")
    if p.is_router:
        diff("inject_partition", n.inject_partition, 3)
    if "ntp-server" in n.labels:
        out.append(_err(c, f"{who}: has an ntp-server label (the ntp app must stay in app mode)"))
    diff("autotunnel", tuple(n.annotations.get("phenix/startup-autotunnel") or ()), p.autotunnel)
    diff("delay.c2", tuple(n.delay_c2), p.delay_c2)
    got_ifs: list[tuple[str, str, str, int, str | None, tuple[str, ...]]] = [
        (i.name, i.vlan, i.address, i.mask, i.gateway, tuple(i.dns)) for i in n.interfaces
    ]
    want_ifs: list[tuple[str, str, str, int, str | None, tuple[str, ...]]] = [
        (i.name, i.vlan, i.ip, i.mask, i.gateway, i.dns) for i in p.nics
    ]
    diff("interfaces (name, vlan, address, mask, gateway, dns)", got_ifs, want_ifs)
    diff(
        "routes",
        sorted((r.destination, r.next) for r in n.routes),
        sorted((r.destination, r.next) for r in p.routes),
    )
    if not n.description:
        out.append(_err(c, f"{who}: no description"))
    return out


def check_addresses(v: m.Variant) -> list[Finding]:
    """Unique addresses, addresses inside their VLAN, no overlapping subnets."""
    out: list[Finding] = []
    c: str = f"{v.name}/addresses"
    seen: dict[str, str] = {}
    routers_on: dict[str, set[str]] = {}
    for n in v.topology.nodes:
        for i in n.interfaces:
            if i.address in seen:
                out.append(_err(c, f"{i.address} on both {seen[i.address]} and {n.hostname}"))
            seen[i.address] = n.hostname
            vl: plan.Vlan | None = plan.VLANS.get(i.vlan)
            if vl is None:
                out.append(_err(c, f"{n.hostname} {i.name}: VLAN {i.vlan} is not in the plan"))
                continue
            if ipaddress.IPv4Address(i.address) not in vl.network:
                out.append(_err(c, f"{n.hostname} {i.name}: {i.address} is outside {vl.subnet}"))
            if i.mask != vl.network.prefixlen:
                out.append(
                    _err(c, f"{n.hostname} {i.name}: mask /{i.mask}, VLAN {vl.name} is {vl.subnet}")
                )
            if n.is_router:
                routers_on.setdefault(i.vlan, set()).add(i.address)
    for n in v.topology.nodes:
        for i in n.interfaces:
            if i.gateway is None:
                continue
            if ipaddress.IPv4Address(i.gateway) not in i.network:
                out.append(_err(c, f"{n.hostname} {i.name}: gateway {i.gateway} is off-link"))
            elif i.gateway not in routers_on.get(i.vlan, set()):
                out.append(
                    _err(
                        c,
                        f"{n.hostname} {i.name}: gateway {i.gateway} is not a router address on {i.vlan}",
                    )
                )
    nets: list[plan.Vlan] = list(plan.VLANS.values())
    for idx, a in enumerate(nets):
        for b in nets[idx + 1 :]:
            if a.network.overlaps(b.network):
                out.append(
                    _err(c, f"VLAN subnets {a.name} {a.subnet} and {b.name} {b.subnet} overlap")
                )
    wg: ipaddress.IPv4Network = ipaddress.IPv4Network(plan.WG_SUBNET)
    for vl in nets:
        if wg.overlaps(vl.network):
            out.append(_err(c, f"WireGuard subnet overlaps VLAN {vl.name}"))
    return out


def check_workflow(v: m.Variant, root: Path) -> list[Finding]:
    out: list[Finding] = []
    c: str = f"{v.name}/workflow"
    wf: m.Workflow = v.workflow
    used: set[str] = {i.vlan for n in v.topology.nodes for i in n.interfaces}
    for name in sorted(used):
        want: int | None = plan.VLANS[name].vid if name in plan.VLANS else None
        if name not in wf.vlans:
            out.append(_err(c, f"VLAN {name} is used but not pinned in {wf.source_file}"))
        elif wf.vlans[name] != want:
            out.append(
                _err(c, f"VLAN {name} is {wf.vlans[name]} in {wf.source_file}, plan says {want}")
            )
    for name, vid in wf.vlans.items():
        if name not in plan.VLANS:
            out.append(_err(c, f"VLAN {name} is pinned but not in the plan"))
        lo: int = wf.vlan_range.get("min", 0)
        hi: int = wf.vlan_range.get("max", 0)
        if not lo <= vid < hi:
            out.append(_err(c, f"VLAN {name} id {vid} is outside vlanRange {lo}-{hi}"))
    ids: list[int] = list(wf.vlans.values())
    if len(ids) != len(set(ids)):
        out.append(_err(c, "two VLANs share an id"))
    if v.name != "ci" and (wf.default_bridge or "") != "cherrymtn":
        out.append(
            _err(
                c, "defaultBridge must be cherrymtn (a non-default bridge enables the Netflow tab)"
            )
        )
    if v.scenario is not None:
        topos: list[str] = [
            t.strip() for t in v.scenario.annotations.get("topology", "").split(",")
        ]
        if wf.topology not in topos:
            out.append(
                _err(
                    c, f"scenario {v.scenario.name} annotation topology {topos} omits {wf.topology}"
                )
            )
        if v.scenario.name != wf.scenario:
            out.append(_err(c, "scenario name differs from the workflow's scenario"))
    if v.name == "full" and v.scenario is not None:
        topos = [t.strip() for t in v.scenario.annotations.get("topology", "").split(",")]
        for want_topo in ("cherrymountain-full", "cherrymountain-lite", "cherrymountain-ci"):
            if want_topo not in topos:
                out.append(
                    _err(
                        c, f"scenario-full annotation topology must list {want_topo} (fromScenario)"
                    )
                )
    return out


def check_boot_order(v: m.Variant) -> list[Finding]:
    """delay.c2 waits on a node that boots earlier, so no cycle can form."""
    out: list[Finding] = []
    c: str = f"{v.name}/boot-order"
    pos: dict[str, int] = {n.hostname: i for i, n in enumerate(v.topology.nodes)}
    for n in v.topology.nodes:
        for dep in n.delay_c2:
            if dep not in pos:
                out.append(_err(c, f"{n.hostname} waits for {dep}, which is not in the topology"))
            elif pos[dep] >= pos[n.hostname]:
                out.append(_err(c, f"{n.hostname} waits for {dep}, which is later in boot order"))
    return out


def check_injects(v: m.Variant, root: Path) -> list[Finding]:
    """Every injection source exists under phenix-injects/."""
    out: list[Finding] = []
    prefix: str = "/phenix/injects/${BRANCH_NAME}/"
    for n in v.topology.nodes:
        for inj in n.injections:
            if not inj.src.startswith(prefix):
                out.append(
                    _err(
                        f"{v.name}/injects", f"{n.hostname}: {inj.src} does not start with {prefix}"
                    )
                )
                continue
            if not (root / "phenix-injects" / inj.src.removeprefix(prefix)).is_file():
                out.append(
                    _err(
                        f"{v.name}/injects",
                        f"{n.hostname}: missing phenix-injects/{inj.src.removeprefix(prefix)}",
                    )
                )
    return out


# --- routing and the firewall ------------------------------------------------


def _routers(v: m.Variant) -> list[m.TNode]:
    return [n for n in v.topology.nodes if n.is_router]


def check_rulesets(v: m.Variant) -> list[Finding]:
    """Shape of the vrouter rulesets: stateful first, default drop, bound."""
    out: list[Finding] = []
    c: str = f"{v.name}/rulesets"
    for fw in _routers(v):
        cfg: m.RouterConfig = fw.router
        if fw.type != "Firewall":
            continue
        bound: set[str] = set(cfg.acl_inbound.values()) | set(cfg.acl_outbound.values())
        for i in fw.interfaces:
            if i.name not in cfg.acl_inbound:
                out.append(_err(c, f"{fw.hostname} {i.name} ({i.vlan}) has no inbound ruleset"))
        for name in cfg.acl_inbound.values():
            if name not in cfg.rulesets:
                out.append(_err(c, f"{fw.hostname}: ruleset {name} is bound but not defined"))
        for name, rs in cfg.rulesets.items():
            if name not in bound:
                out.append(_err(c, f"{fw.hostname}: ruleset {name} is defined but not bound"))
            out += _ruleset_shape(c, fw.hostname, rs)
        for iface in list(cfg.acl_inbound) + list(cfg.acl_outbound):
            if fw.iface(iface) is None:
                out.append(_err(c, f"{fw.hostname}: ACL names unknown interface {iface}"))
    return out


def _ruleset_shape(c: str, host: str, rs: m.Ruleset) -> list[Finding]:
    out: list[Finding] = []
    who: str = f"{host} {rs.name}"
    if rs.default != "drop":
        out.append(_err(c, f"{who}: default is {rs.default}, must be drop"))
    rules: list[m.Rule] = sorted(rs.rules, key=lambda r: r.id)
    ids: list[int] = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        out.append(_err(c, f"{who}: duplicate rule ids"))
    if not rules or not (rules[0].stateful and rules[0].action == "accept"):
        out.append(_err(c, f"{who}: the first rule must be the stateful accept"))
    for r in rules:
        if r.stateful:
            if r is not rules[0]:
                out.append(_err(c, f"{who} rule {r.id}: only the first rule may be stateful"))
            continue
        if r.action != "accept":
            out.append(
                _err(c, f"{who} rule {r.id}: explicit rules must accept (the default drops)")
            )
        if r.protocol not in ("tcp", "udp"):
            out.append(_err(c, f"{who} rule {r.id}: protocol {r.protocol} is not tcp or udp"))
        dst: dict[str, Any] = r.destination or {}
        if not dst.get("address") or not dst.get("port"):
            out.append(_err(c, f"{who} rule {r.id}: needs a destination address and port"))
        if not r.description:
            out.append(_err(c, f"{who} rule {r.id}: needs a description"))
    return out


def _service_set() -> list[conduits.Service]:
    seen: dict[tuple[str, int], conduits.Service] = {}
    for c in conduits.CONDUITS:
        for s in c.services:
            seen[(s.proto, s.port)] = s
    for s in EXTRA_SERVICES:
        seen[(s.proto, s.port)] = s
    return list(seen.values())


#: The untrusted side of fw-edge: no firewall rule of this plant protects it.
EXTERNAL_VLANS: frozenset[str] = frozenset({"INET", "VENDOR", "UTIL"})


def _external(net: Net, name: str) -> bool:
    return all(i.vlan in EXTERNAL_VLANS for i in net.nodes[name].interfaces)


def _same_l2(net: Net, a: str, b: str) -> bool:
    va: set[str] = {i.vlan for i in net.nodes[a].interfaces}
    return any(i.vlan in va for i in net.nodes[b].interfaces)


def check_flows(v: m.Variant) -> list[Finding]:
    """Sweep every host pair and service: allowed flows equal the conduits.

    * A conduit flow must be delivered, and its reply must pass back
      through the same firewalls (statefully).
    * Any other cross-zone flow must be dropped or have no route.
    * A conduit crosses exactly the firewalls its row names, and every
      firewall on the path accepts it with an explicit rule.
    """
    out: list[Finding] = []
    c: str = f"{v.name}/flows"
    net: Net = Net(v.topology)
    names: set[str] = set(net.nodes)
    expected: set[tuple[str, str, str, int]] = conduits.expected_flows(v.name)
    hosts: list[str] = [n.hostname for n in v.topology.nodes if not n.is_router]
    router_srcs: set[str] = {s for (s, _, _, _) in expected if net.nodes[s].is_router}
    services: list[conduits.Service] = _service_set()
    by_flow: dict[tuple[str, str, str, int], conduits.Conduit] = {}
    for cd in conduits.CONDUITS:
        if cd.enforced:
            for f in conduits.expand(cd, v.name):
                by_flow[f] = cd
    dnat_dsts: dict[str, str] = {
        cd.dsts[0]: cd.dst_ip for cd in conduits.CONDUITS if cd.dst_ip and cd.enforced
    }

    for src in [*hosts, *sorted(router_srcs)]:
        for dst in hosts:
            if src == dst or _same_l2(net, src, dst):
                continue
            if _external(net, src) and _external(net, dst):
                continue  # the internet side: no plant firewall in between
            for svc in services:
                key: tuple[str, str, str, int] = (src, dst, svc.proto, svc.port)
                is_expected: bool = key in expected
                if net.nodes[src].is_router and not is_expected:
                    continue
                fwd: Verdict = trace(
                    net, src, net.nodes[dst].interfaces[0].address, svc.proto, svc.port
                )
                if is_expected:
                    out += _check_expected(c, net, key, by_flow[key], dnat_dsts)
                elif fwd.result == "allowed":
                    out.append(
                        _err(
                            c,
                            f"{src} -> {dst} {svc.proto}/{svc.port} is allowed ({' > '.join(fwd.path)}) but no conduit covers it",
                        )
                    )
                elif fwd.result not in BLOCKED_RESULTS:
                    out.append(
                        _err(
                            c, f"{src} -> {dst} {svc.proto}/{svc.port}: {fwd.result} ({fwd.detail})"
                        )
                    )
    # Anything reaching a host through the DNAT public address.
    for dst, pub in dnat_dsts.items():
        if dst not in names:
            continue
        for src in hosts:
            if _same_l2(net, src, dst) or _external(net, src):
                continue  # the DNAT rule has no source restriction by design
            for svc in services:
                key = (src, dst, svc.proto, svc.port)
                fwd = trace(net, src, pub, svc.proto, svc.port)
                if key in expected:
                    continue
                if fwd.result == "allowed":
                    out.append(
                        _err(
                            c,
                            f"{src} -> {pub} {svc.proto}/{svc.port} reaches {fwd.final_node} through DNAT but no conduit covers it",
                        )
                    )
    return out


def _fws(net: Net, routers: list[str]) -> set[str]:
    """The routers among ``routers`` that filter (firewalls, not plain routers)."""
    return {r for r in routers if net.nodes[r].type == "Firewall"}


def _check_expected(
    c: str,
    net: Net,
    key: tuple[str, str, str, int],
    cd: conduits.Conduit,
    dnat_dsts: dict[str, str],
) -> list[Finding]:
    src, dst, proto, port = key
    out: list[Finding] = []
    pub: str | None = dnat_dsts.get(dst) if cd.dst_ip else None
    fwd, rev = flow(net, src, dst, proto, port, dst_ip=pub)
    tag: str = f"{src} -> {dst} {proto}/{port} ({cd.id})"
    if fwd.result != "allowed" or fwd.final_node != dst:
        return [
            _err(
                c, f"{tag}: not delivered: {fwd.result} {fwd.detail} (path {' > '.join(fwd.path)})"
            )
        ]
    if rev is None or rev.result not in ("allowed", "local") or rev.final_node != src:
        det: str = f"{rev.result} {rev.detail}" if rev else "no reply trace"
        out.append(_err(c, f"{tag}: the reply does not return: {det}"))
    elif _fws(net, rev.firewalls) != _fws(net, fwd.firewalls):
        out.append(
            _err(c, f"{tag}: reply crosses {rev.firewalls}, request {fwd.firewalls} (asymmetric)")
        )
    if _fws(net, fwd.firewalls) != set(cd.via):
        out.append(
            _err(
                c, f"{tag}: crosses firewalls {fwd.firewalls}, the conduit row names {list(cd.via)}"
            )
        )
    for fw in cd.via:
        if not any(a.startswith(f"{fw}:") and not a.endswith(":stateful") for a in fwd.accepted_by):
            # A new connection never matches the stateful rule, so a conduit
            # needs an explicit rule at every firewall it crosses.
            out.append(_err(c, f"{tag}: {fw} accepts it without an explicit rule"))
    return out


def check_rule_coverage(v: m.Variant) -> list[Finding]:
    """Every explicit accept rule is used by at least one conduit flow."""
    out: list[Finding] = []
    c: str = f"{v.name}/rule-coverage"
    net: Net = Net(v.topology)
    used: set[str] = set()
    for key in conduits.expected_flows(v.name):
        src, dst, proto, port = key
        cd: conduits.Conduit | None = next(
            (x for x in conduits.CONDUITS if x.enforced and key in set(conduits.expand(x, v.name))),
            None,
        )
        pub: str | None = cd.dst_ip if cd else None
        fwd: Verdict = trace(net, src, pub or net.nodes[dst].interfaces[0].address, proto, port)
        used.update(fwd.accepted_by)
    for fw in _routers(v):
        for name, rs in fw.router.rulesets.items():
            for r in rs.rules:
                if r.stateful:
                    continue
                if f"{fw.hostname}:{name}:{r.id}" not in used:
                    out.append(
                        _err(
                            c,
                            f"{fw.hostname} {name} rule {r.id} ({r.description}) matches no conduit flow",
                        )
                    )
    return out


def check_nat_ipsec(v: m.Variant) -> list[Finding]:
    out: list[Finding] = []
    c: str = f"{v.name}/nat-ipsec"
    net: Net = Net(v.topology)
    try:
        edge: m.TNode = v.topology.node("fw-edge")
        util: m.TNode = v.topology.node("util-rtr")
    except KeyError:
        return [_err(c, "fw-edge or util-rtr is missing")]
    snat: list[m.NatRule] = edge.router.snat
    if [(r.interface, r.srcAddr, r.translation) for r in snat] != [
        ("eth0", "10.10.10.0/24", "masquerade")
    ]:
        out.append(
            _err(c, "fw-edge SNAT must be exactly masquerade of 10.10.10.0/24 out eth0 (INET)")
        )
    dnat: list[tuple[str, str | None, int | None, str | None, str]] = [
        (r.interface, r.destAddr, r.dstPort, r.protocol, r.translation) for r in edge.router.dnat
    ]
    want_dnat: list[tuple[str, str | None, int | None, str | None, str]] = [
        (
            "eth0",
            plan.FW_EDGE_INET_IP,
            plan.WG_PORT,
            "udp",
            f"{plan.NODE_BY_NAME['dmz-vpn'].primary_ip}:{plan.WG_PORT}",
        )
    ]
    if dnat != want_dnat:
        out.append(_err(c, f"fw-edge DNAT is {dnat}, plan says {want_dnat}"))
    for fw, peer in ((edge, util), (util, edge)):
        sites: list[m.IpsecSite] = fw.router.ipsec
        if len(sites) != 1:
            out.append(_err(c, f"{fw.hostname}: expected one IPsec site, found {len(sites)}"))
            continue
        s: m.IpsecSite = sites[0]
        mine: tuple[tuple[m.TNode, m.Iface] | None, ...] = (
            net.owner.get(s.local),
            net.owner.get(s.remote),
        )
        if mine[0] is None or mine[0][0] is not fw or mine[0][1].vlan != "INET":
            out.append(_err(c, f"{fw.hostname}: IPsec local {s.local} is not its INET address"))
        if mine[1] is None or mine[1][0] is not peer:
            out.append(
                _err(c, f"{fw.hostname}: IPsec remote {s.remote} is not {peer.hostname}'s address")
            )
        if s.peer != peer.hostname:
            out.append(
                _err(c, f"{fw.hostname}: IPsec peer name {s.peer} should be {peer.hostname}")
            )
    e_t: list[tuple[str, str]] = (
        [(t.local, t.remote) for t in edge.router.ipsec[0].tunnels] if edge.router.ipsec else []
    )
    u_t: list[tuple[str, str]] = (
        [(t.remote, t.local) for t in util.router.ipsec[0].tunnels] if util.router.ipsec else []
    )
    if e_t != [("10.20.15.0/24", "192.0.2.0/24")] or u_t != e_t:
        out.append(
            _err(
                c,
                f"IPsec tunnels must mirror 10.20.15.0/24 <-> 192.0.2.0/24, found {e_t} and {u_t}",
            )
        )
    # Netem on the WAN legs.
    isp: m.TNode = v.topology.node("isp-rtr")
    wan: set[str] = {e for em in isp.router.emulators for e in em.egress}
    if wan != {"eth0", "eth1"}:
        out.append(
            _err(c, f"isp-rtr netem must cover eth0 and eth1 (INET, VENDOR), covers {sorted(wan)}")
        )
    uw: set[str] = {e for em in util.router.emulators for e in em.egress}
    if uw != {"eth0", "eth1"}:
        out.append(
            _err(c, f"util-rtr netem must cover eth0 and eth1 (INET, UTIL), covers {sorted(uw)}")
        )
    for fw in (isp, util):
        for em in fw.router.emulators:
            for e in em.egress:
                if fw.iface(e) is None:
                    out.append(
                        _err(c, f"{fw.hostname}: emulator {em.name} names unknown interface {e}")
                    )
                elif not re.fullmatch(r"eth\d+", e):
                    out.append(
                        _err(c, f"{fw.hostname}: emulator interface {e} must be a VyOS name (ethN)")
                    )
    return out


# --- SoH ---------------------------------------------------------------------


def _soh(v: m.Variant) -> m.App | None:
    return v.scenario.app("soh") if v.scenario else None


def check_soh(v: m.Variant) -> list[Finding]:
    out: list[Finding] = []
    c: str = f"{v.name}/soh"
    app: m.App | None = _soh(v)
    if app is None:
        return [_err(c, "scenario has no soh app")]
    md: dict[str, Any] = app.metadata
    net: Net = Net(v.topology)
    names: set[str] = set(net.nodes)
    if md.get("testReachability") != "off":
        out.append(_err(c, 'testReachability must be "off" (segmentation blocks pings by design)'))
    skip: set[str] = set(md.get("skipHosts") or [])
    for r in _routers(v):
        if r.hostname not in skip:
            out.append(_err(c, f"router {r.hostname} must be in skipHosts"))
    tests: list[dict[str, Any]] = md.get("testCustomReachability") or []
    have: set[tuple[str, str, str, int]] = set()
    for t in tests:
        dst_host, _, dst_if = str(t.get("dst", "")).partition("|")
        key: tuple[str, str, str, int] = (t["src"], dst_host, t["proto"], int(t["port"]))
        have.add(key)
        if t["src"] not in names or dst_host not in names:
            out.append(_err(c, f"reachability test {key}: unknown host"))
            continue
        if net.nodes[dst_host].iface(dst_if) is None:
            out.append(_err(c, f"reachability test {key}: {dst_host} has no interface {dst_if}"))
        if t["proto"] == "udp":
            if not t.get("udpPacketBase64"):
                out.append(_err(c, f"reachability test {key}: UDP needs udpPacketBase64"))
            else:
                try:
                    base64.b64decode(t["udpPacketBase64"], validate=True)
                except ValueError:
                    out.append(_err(c, f"reachability test {key}: udpPacketBase64 is not base64"))
        if not t.get("wait"):
            out.append(_err(c, f"reachability test {key}: no wait"))
    for cd in conduits.CONDUITS:
        for p in cd.probes:
            if p.src not in names or p.dst not in names:
                continue
            if (p.src, p.dst, p.proto, p.port) not in have:
                out.append(
                    _err(
                        c,
                        f"no testCustomReachability for {p.src} -> {p.dst} {p.proto}/{p.port} ({cd.id})",
                    )
                )
    # Every TCP conduit has a probe.
    for cd in conduits.CONDUITS:
        if any(s.proto == "tcp" for s in cd.services) and conduits.expand(cd, v.name):
            tcp_probes: list[conduits.Probe] = [
                p for p in cd.probes if p.proto == "tcp" and p.src in names and p.dst in names
            ]
            if not tcp_probes:
                out.append(_err(c, f"TCP conduit {cd.id} has no reachability probe"))
    # A positive probe across a firewall must be allowed (tunnel flows excepted).
    unenforced: set[tuple[str, str, str, int]] = {
        (p.src, p.dst, p.proto, p.port)
        for cd in conduits.CONDUITS
        if not cd.enforced
        for p in cd.probes
    }
    for key in sorted(have):
        if key in unenforced or key[0] not in names or key[1] not in names:
            continue
        if _same_l2(net, key[0], key[1]):
            continue
        fwd, _ = flow(net, key[0], key[1], key[2], key[3])
        if fwd.result != "allowed":
            out.append(
                _err(c, f"reachability test {key} is blocked by the firewall rules ({fwd.detail})")
            )
    out += _check_negative_tests(v, c, md, net)
    return out


def _check_negative_tests(v: m.Variant, c: str, md: dict[str, Any], net: Net) -> list[Finding]:
    out: list[Finding] = []
    custom: dict[str, list[dict[str, Any]]] = md.get("hostCustomTests") or {}
    for bp in conduits.BLOCKED_PROBES:
        if bp.src not in net.nodes or bp.dst not in net.nodes:
            continue
        dst_ip: str = net.nodes[bp.dst].interfaces[0].address
        verdict: Verdict = trace(net, bp.src, dst_ip, "tcp", bp.port)
        if verdict.result not in BLOCKED_RESULTS:
            out.append(
                _err(
                    c,
                    f"blocked-path probe {bp.src} -> {bp.dst} tcp/{bp.port} is not blocked: {verdict.result} ({verdict.detail})",
                )
            )
        test: dict[str, Any] | None = next(
            (t for t in custom.get(bp.src, []) if t.get("name") == f"blocked-{bp.dst}-{bp.port}"),
            None,
        )
        if test is None:
            out.append(_err(c, f"no cc negative test blocked-{bp.dst}-{bp.port} on {bp.src}"))
            continue
        script: str = str(test.get("testScript", ""))
        if f"/dev/tcp/{dst_ip}/{bp.port}" not in script:
            out.append(
                _err(
                    c,
                    f"negative test {bp.src} -> {bp.dst}:{bp.port} does not connect to {dst_ip}:{bp.port}",
                )
            )
        if test.get("testStdout") != "DROPPED":
            out.append(_err(c, f"negative test {bp.src} -> {bp.dst}:{bp.port} must expect DROPPED"))
    known: set[tuple[str, str]] = {(b.src, b.dst) for b in conduits.BLOCKED_PROBES}
    for host, tests in custom.items():
        for t in tests:
            nm: str = str(t.get("name", ""))
            mt: re.Match[str] | None = re.fullmatch(r"blocked-(.+)-(\d+)", nm)
            if mt and (host, mt.group(1)) not in known and host in net.nodes:
                out.append(_err(c, f"negative test {nm} on {host} is not in the blocked-path list"))
    return out


# --- apps --------------------------------------------------------------------

#: App blocks plantgen generates (scenario placeholders until it emits them).
GENERATED_APPS: tuple[str, ...] = ("ot-sim", "helics", "cherrymountain", "ntp")
APP_ORDER: tuple[str, ...] = ("ot-sim", "cherrymountain", "helics")


def check_apps(v: m.Variant) -> list[Finding]:
    out: list[Finding] = []
    c: str = f"{v.name}/apps"
    if v.scenario is None:
        return [_err(c, "no scenario")]
    sc: m.Scenario = v.scenario
    names: set[str] = {n.hostname for n in v.topology.nodes}
    order: list[str] = sc.app_order()
    if len(order) != len(set(order)):
        out.append(_err(c, f"duplicate apps in {sc.source_file}"))
    idx: list[int] = [order.index(a) for a in APP_ORDER if a in order]
    if idx != sorted(idx):
        out.append(_err(c, "app order must be ot-sim, cherrymountain, helics"))
    for app in sc.apps:
        for host in app.hosts:
            if host.get("hostname") not in names:
                out.append(
                    _err(c, f"app {app.name}: host {host.get('hostname')} is not in the topology")
                )
    for gen in GENERATED_APPS:
        if sc.app(gen) is None:
            out.append(
                _note(
                    c,
                    f"app {gen} is not in {sc.source_file} yet (plantgen has not emitted it for {v.name}; sync.py fills the placeholder)",
                )
            )
    ntp: m.App | None = sc.app("ntp")
    if ntp is not None:
        out += _check_ntp(c, v, ntp, names)
    for want in ("startup", "vrouter", "soh"):
        if sc.app(want) is None:
            out.append(_err(c, f"scenario has no {want} app"))
    if v.name == "full":
        for want in ("protonuke", "wireguard", "mirror"):
            if sc.app(want) is None:
                out.append(_err(c, f"scenario has no {want} app"))
    out += _check_helics_otsim(c, v, names)
    return out


def _check_ntp(c: str, v: m.Variant, ntp: m.App, names: set[str]) -> list[Finding]:
    out: list[Finding] = []
    listed: dict[str, dict[str, Any]] = {h["hostname"]: h.get("metadata") or {} for h in ntp.hosts}
    node_by: dict[str, m.TNode] = {n.hostname: n for n in v.topology.nodes}
    default: dict[str, Any] = (ntp.metadata or {}).get("defaultSource") or {}
    if default.get("hostname") not in names:
        out.append(_err(c, "ntp defaultSource host is not in the topology"))
    for hn in sorted(names - set(listed)):
        out.append(_err(c, f"ntp app mode needs every host: {hn} is missing"))
    for hn, md in listed.items():
        if hn not in node_by:
            continue
        node: m.TNode = node_by[hn]
        if node.is_router and md.get("client") not in (None, "ntp"):
            out.append(_err(c, f"ntp: router {hn} must use client: ntp, not {md.get('client')}"))
        if not md.get("client") and not md.get("server"):
            out.append(_err(c, f"ntp: {hn} has neither client nor server"))
        src: dict[str, Any] = md.get("source") or {}
        if src.get("hostname"):
            tgt: m.TNode | None = node_by.get(src["hostname"])
            if tgt is None or tgt.iface(src.get("interface", "")) is None:
                out.append(_err(c, f"ntp: {hn} source {src} is not a topology host and interface"))
    return out


def _check_helics_otsim(c: str, v: m.Variant, names: set[str]) -> list[Finding]:
    out: list[Finding] = []
    sc: m.Scenario | None = v.scenario
    if sc is None:
        return out
    node_by: dict[str, m.TNode] = {n.hostname: n for n in v.topology.nodes}
    ot: m.App | None = sc.app("ot-sim")
    if ot is not None:
        for h in ot.hosts:
            md: dict[str, Any] = h.get("metadata") or {}
            node: m.TNode | None = node_by.get(h["hostname"])
            if node is None:
                continue
            for proto in ("modbus", "dnp3", "bacnet"):
                spec: dict[str, Any] | None = md.get(proto)
                if isinstance(spec, dict) and isinstance(spec.get("interface"), str):
                    ifname: str = spec["interface"].split(":")[0]
                    if node.iface(ifname) is None:
                        out.append(
                            _err(c, f"ot-sim {h['hostname']} {proto}: no interface {ifname}")
                        )
    helics: m.App | None = sc.app("helics")
    if helics is not None:
        root: str = str(((helics.metadata or {}).get("broker") or {}).get("root", ""))
        host, _, ifname = root.partition("|")
        if host and (host not in node_by or node_by[host].iface(ifname) is None):
            out.append(_err(c, f"helics broker root {root} is not a topology host and interface"))
    return out


def check_app_hosts(v: m.Variant) -> list[Finding]:
    """protonuke, wireguard, and mirror entries agree with the topology."""
    out: list[Finding] = []
    c: str = f"{v.name}/app-hosts"
    if v.scenario is None:
        return out
    node_by: dict[str, m.TNode] = {n.hostname: n for n in v.topology.nodes}
    wg: m.App | None = v.scenario.app("wireguard")
    if wg is not None:
        hosts: dict[str, dict[str, Any]] = {
            h["hostname"]: h.get("metadata") or {} for h in wg.hosts
        }
        if set(hosts) != {"dmz-vpn", "vendor-ws"}:
            out.append(
                _err(c, f"wireguard hosts must be dmz-vpn and vendor-ws, found {sorted(hosts)}")
            )
        else:
            srv: dict[str, Any] = hosts["dmz-vpn"]
            cli: dict[str, Any] = hosts["vendor-ws"]
            if srv["interface"].get("address") != f"{plan.WG_SERVER_IP}/24":
                out.append(_err(c, "dmz-vpn wireguard address must be 10.99.0.1/24"))
            if str(cli["interface"].get("address", "")).split("/")[0] != plan.WG_VENDOR_IP:
                out.append(_err(c, "vendor-ws wireguard address must be 10.99.0.2"))
            if srv["interface"].get("listen_port") != plan.WG_PORT:
                out.append(_err(c, "dmz-vpn wireguard listen_port must be 51820"))
            jump: str = plan.NODE_BY_NAME["dmz-jump"].primary_ip
            allowed: set[str] = {
                a.strip() for p in cli["peers"] for a in p["allowed_ips"].split(",")
            }
            if f"{jump}/32" not in allowed or not allowed <= {
                f"{jump}/32",
                f"{plan.WG_SERVER_IP}/32",
            }:
                out.append(
                    _err(
                        c,
                        f"vendor-ws AllowedIPs must admit dmz-jump (and at most the tunnel gateway), found {sorted(allowed)}",
                    )
                )
            endpoint: str = str(cli["peers"][0].get("server_endpoint", ""))
            if endpoint != f"{plan.FW_EDGE_INET_IP}:{plan.WG_PORT}":
                out.append(
                    _err(
                        c,
                        f"vendor-ws endpoint must be {plan.FW_EDGE_INET_IP}:{plan.WG_PORT}, found {endpoint}",
                    )
                )
            peers_srv: set[str] = {
                a.strip() for p in srv["peers"] for a in p["allowed_ips"].split(",")
            }
            if peers_srv != {f"{plan.WG_VENDOR_IP}/32"}:
                out.append(
                    _err(
                        c,
                        f"dmz-vpn peer AllowedIPs must be the vendor tunnel address only, found {sorted(peers_srv)}",
                    )
                )
            if srv.get("boot") is not True:
                out.append(_err(c, "dmz-vpn wireguard needs boot: true"))
    mirror: m.App | None = v.scenario.app("mirror")
    if mirror is not None:
        for h in mirror.hosts:
            hmd: dict[str, Any] = h.get("metadata") or {}
            for vlan in hmd.get("vlans") or []:
                if vlan not in plan.VLANS or vlan in ("MGMT", "MIRROR", "SOC"):
                    out.append(_err(c, f"mirror VLAN {vlan} must be a monitored plant VLAN"))
        if (mirror.metadata or {}).get("mirrorVLAN") != "MIRROR":
            out.append(_err(c, "mirror mirrorVLAN must be MIRROR"))
        net_: str = str((mirror.metadata or {}).get("mirrorNet", ""))
        if net_ != plan.VLANS["MIRROR"].subnet:
            out.append(_err(c, f"mirrorNet must be {plan.VLANS['MIRROR'].subnet}"))
        if "malcolm" not in node_by:
            out.append(_err(c, "mirror app needs malcolm in the topology"))
    return out


# --- ot-svc and the BBMDs ----------------------------------------------------


def check_services(root: Path, v: m.Variant) -> list[Finding]:
    """ot-svc's DNS zone lists every host, and the BBMD config matches the plan."""
    out: list[Finding] = []
    c: str = f"{v.name}/services"
    inj: Path = root / "phenix-injects"
    hosts_file: Path = inj / "ot-svc" / "plant.hosts"
    if hosts_file.is_file():
        entries: dict[str, str] = {}
        for raw in hosts_file.read_text().splitlines():
            line: str = raw.split("#")[0].strip()
            if line:
                ip, *aliases = line.split()
                for a in aliases:
                    entries[a] = ip
        for n in plan.nodes_for("full"):
            for nic in n.nics:
                key: str = n.name if nic is n.nics[0] else f"{n.name}-{nic.name.lower()}"
                if entries.get(key) != nic.ip:
                    out.append(
                        _err(c, f"plant.hosts: {key} should be {nic.ip}, found {entries.get(key)}")
                    )
    else:
        out.append(_note(c, "phenix-injects/ot-svc/plant.hosts not present"))
    return out


# --- driver ------------------------------------------------------------------

Check = Callable[[m.Variant], list[Finding]]


def run_variant(variant: str, root: Path = m.ROOT) -> list[Finding]:
    """Every check for one variant (``full`` or ``lite``)."""
    try:
        v: m.Variant = m.load_variant(variant, root)
    except (m.LoadError, KeyError) as exc:
        return [_err(f"{variant}/load", str(exc))]
    out: list[Finding] = []
    out += check_plan(v)
    out += check_addresses(v)
    out += check_workflow(v, root)
    out += check_boot_order(v)
    out += check_injects(v, root)
    out += check_rulesets(v)
    out += check_nat_ipsec(v)
    out += check_flows(v)
    if variant == "full":
        out += check_rule_coverage(v)
    out += check_soh(v)
    out += check_apps(v)
    out += check_app_hosts(v)
    out += check_services(root, v)
    return out


def run_ci(root: Path = m.ROOT) -> list[Finding]:
    """Light checks for the ci slice (its own flat CTRL VLAN, no firewalls)."""
    try:
        v: m.Variant = m.load_variant("ci", root)
    except (m.LoadError, KeyError) as exc:
        return [_err("ci/load", str(exc))]
    out: list[Finding] = []
    out += check_addresses(v)
    out += check_workflow(v, root)
    out += check_boot_order(v)
    out += check_injects(v, root)
    return out


def run_all(root: Path = m.ROOT) -> list[Finding]:
    out: list[Finding] = []
    for variant in ("full", "lite"):
        out += run_variant(variant, root)
    out += run_ci(root)
    return out
