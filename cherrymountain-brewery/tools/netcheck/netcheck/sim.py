"""A small packet-path simulator for the topology's static routing and the
vrouter app's firewall rulesets.

It follows one flow hop by hop: connected networks, static routes, default
gateways, DNAT on arrival, the forward filter of each router (the inbound
ruleset of the arrival interface, then the outbound ruleset of the exit
interface), policy-based IPsec, and source NAT. It models what the phenix
``vyatta.tmpl`` renders, which is only the ``forward`` filter, so traffic to a
router's own address is never filtered and is reported as ``local``.

A reply is traced the same way from the server back to the client, with the
connection marked established, so the stateful rule of each ruleset matches.
"""

from __future__ import annotations

import ipaddress
from typing import Literal

from pydantic import BaseModel, Field

from netcheck.model import Iface, IpsecSite, NatRule, Rule, Ruleset, TNode, Topology

IPv4 = ipaddress.IPv4Address
IPv4Net = ipaddress.IPv4Network

MAX_HOPS: int = 16


class Verdict(BaseModel):
    """The outcome of tracing one flow."""

    result: Literal["allowed", "dropped", "no-route", "loop", "local"]
    #: Nodes the packet visited, source first.
    path: list[str] = Field(default_factory=list)
    #: Routers whose forward filter accepted the packet, in order.
    firewalls: list[str] = Field(default_factory=list)
    detail: str = ""
    #: (router, translated source address, original source address).
    snat: list[tuple[str, str, str]] = Field(default_factory=list)
    #: The packet's destination address and port when it was delivered.
    final_ip: str = ""
    final_port: int = 0
    final_node: str = ""
    #: The ruleset rule ids that accepted the packet, as "router:ruleset:rule".
    accepted_by: list[str] = Field(default_factory=list)
    #: Router -> name of the interface the packet arrived on (forwarded only).
    fw_in: dict[str, str] = Field(default_factory=dict)


class Net:
    """Indexes over a topology that the tracer needs."""

    def __init__(self, topology: Topology) -> None:
        self.topology: Topology = topology
        self.nodes: dict[str, TNode] = {n.hostname: n for n in topology.nodes}
        self.owner: dict[str, tuple[TNode, Iface]] = {}
        for n in topology.nodes:
            for i in n.interfaces:
                self.owner[i.address] = (n, i)

    def on_vlan(self, vlan: str, address: str) -> tuple[TNode, Iface] | None:
        hit: tuple[TNode, Iface] | None = self.owner.get(address)
        if hit is not None and hit[1].vlan == vlan:
            return hit
        return None


def _addr_match(spec: str | None, ip: str) -> bool:
    if not spec:
        return True
    net: IPv4Net = ipaddress.IPv4Network(spec, strict=False)
    return IPv4(ip) in net


def _proto_match(rule_proto: str, proto: str) -> bool:
    if rule_proto in ("all", "any"):
        return True
    if rule_proto == "tcp_udp":
        return proto in ("tcp", "udp")
    return rule_proto == proto


def rule_matches(
    rule: Rule,
    proto: str,
    src: str,
    sport: int,
    dst: str,
    dport: int,
    established: bool,
) -> bool:
    """Whether ``rule`` matches a packet of a new or established connection."""
    if rule.stateful and not established:
        return False
    if not _proto_match(rule.protocol, proto):
        return False
    s: dict[str, object] = rule.source or {}
    d: dict[str, object] = rule.destination or {}
    if not _addr_match(s.get("address"), src):  # type: ignore[arg-type]
        return False
    if s.get("port") and int(s["port"]) != sport:  # type: ignore[call-overload]
        return False
    if not _addr_match(d.get("address"), dst):  # type: ignore[arg-type]
        return False
    return not (d.get("port") and int(d["port"]) != dport)  # type: ignore[call-overload]


def eval_ruleset(
    rs: Ruleset,
    proto: str,
    src: str,
    sport: int,
    dst: str,
    dport: int,
    established: bool,
) -> tuple[str, str]:
    """(action, rule id) of the first matching rule.

    The id is ``"stateful"`` for the stateful rule and ``"default"`` when no
    rule matched.
    """
    for rule in sorted(rs.rules, key=lambda r: r.id):
        if rule_matches(rule, proto, src, sport, dst, dport, established):
            return rule.action, "stateful" if rule.stateful else str(rule.id)
    return rs.default, "default"


def _route(node: TNode, dst: str) -> tuple[Iface, str] | None:
    """The exit interface and next-hop address for ``dst``, or None."""
    ip: IPv4 = IPv4(dst)
    best: tuple[int, int, Iface, str] | None = None  # (prefixlen, connected, ...)
    for i in node.interfaces:
        if ip in i.network:
            cand = (i.network.prefixlen, 1, i, dst)
            if best is None or cand[:2] > best[:2]:
                best = cand
    for r in node.routes:
        net: IPv4Net = ipaddress.IPv4Network(r.destination, strict=False)
        if ip not in net:
            continue
        for i in node.interfaces:
            if IPv4(r.next) in i.network:
                cand = (net.prefixlen, 0, i, r.next)
                if best is None or cand[:2] > best[:2]:
                    best = cand
                break
    for i in node.interfaces:
        if i.gateway:
            cand = (0, 0, i, i.gateway)
            if best is None or cand[:2] > best[:2]:
                best = cand
    if best is None:
        return None
    return best[2], best[3]


def _ipsec_peer(node: TNode, src: str, dst: str) -> IpsecSite | None:
    for site in node.router.ipsec:
        for t in site.tunnels:
            if _addr_match(t.local, src) and ipaddress.IPv4Address(dst) in (
                ipaddress.IPv4Network(t.remote, strict=False)
            ):
                return site
    return None


def _dnat(node: TNode, in_iface: Iface, proto: str, dst: str, dport: int) -> NatRule | None:
    for r in node.router.dnat:
        if r.interface != in_iface.name:
            continue
        if r.protocol and r.protocol != proto:
            continue
        if r.destAddr and r.destAddr != dst:
            continue
        if r.dstPort and r.dstPort != dport:
            continue
        return r
    return None


def _snat(node: TNode, out_iface: Iface, src: str) -> NatRule | None:
    for r in node.router.snat:
        if r.interface == out_iface.name and _addr_match(r.srcAddr, src):
            return r
    return None


def trace(
    net: Net,
    src_name: str,
    dst_ip: str,
    proto: str,
    dport: int,
    *,
    sport: int = 40000,
    established: bool = False,
    src_ip: str | None = None,
    unnat: dict[str, tuple[str, str]] | None = None,
) -> Verdict:
    """Trace one packet from the host ``src_name`` to ``dst_ip``."""
    node: TNode = net.nodes[src_name]
    unnat = unnat or {}
    path: list[str] = [node.hostname]
    firewalls: list[str] = []
    snat: list[tuple[str, str, str]] = []
    accepted: list[str] = []
    fw_in: dict[str, str] = {}
    in_iface: Iface | None = None
    cur_src: str | None = src_ip
    dst: str = dst_ip
    dp: int = dport

    def done(result: str, detail: str = "", final: TNode | None = None) -> Verdict:
        return Verdict(
            result=result,  # type: ignore[arg-type]
            path=path,
            firewalls=firewalls,
            detail=detail,
            snat=snat,
            final_ip=dst,
            final_port=dp,
            final_node=final.hostname if final else "",
            accepted_by=accepted,
            fw_in=fw_in,
        )

    for _ in range(MAX_HOPS):
        if in_iface is not None and node.is_router:
            if node.hostname in unnat and dst == unnat[node.hostname][0]:
                dst = unnat[node.hostname][1]
            rule: NatRule | None = _dnat(node, in_iface, proto, dst, dp)
            if rule is not None:
                dst = rule.translation.split(":")[0]
                if ":" in rule.translation:
                    dp = int(rule.translation.split(":")[1])
        owner: tuple[TNode, Iface] | None = net.owner.get(dst)
        if owner is not None and owner[0] is node:
            return done("local" if node.is_router else "allowed", final=node)
        hop: tuple[Iface, str] | None = _route(node, dst)
        if hop is None:
            return done("no-route", f"{node.hostname}: no route to {dst}")
        out_iface, next_ip = hop
        if cur_src is None:
            cur_src = out_iface.address
        if in_iface is not None and node.is_router:
            # Forward filter: inbound ruleset first, then outbound.
            fw_in[node.hostname] = in_iface.name
            for acl, iface, direction in (
                (node.router.acl_inbound, in_iface, "in"),
                (node.router.acl_outbound, out_iface, "out"),
            ):
                name: str | None = acl.get(iface.name)
                if not name:
                    continue
                rs: Ruleset | None = node.router.rulesets.get(name)
                if rs is None:
                    return done("dropped", f"{node.hostname}: ruleset {name} missing")
                action, rid = eval_ruleset(rs, proto, cur_src, sport, dst, dp, established)
                if action != "accept":
                    return done(
                        "dropped",
                        f"{node.hostname} {direction}bound {iface.name} "
                        f"{name} rule {rid}: {action}",
                    )
                accepted.append(f"{node.hostname}:{name}:{rid}")
                break
            firewalls.append(node.hostname)
        # Policy IPsec: after the filter, before the packet leaves.
        site: IpsecSite | None = _ipsec_peer(node, cur_src, dst) if node.is_router else None
        if site is not None:
            peer: tuple[TNode, Iface] | None = net.owner.get(site.remote)
            if peer is None:
                return done("no-route", f"{node.hostname}: IPsec peer {site.remote}")
            path.append(peer[0].hostname)
            node, in_iface = peer
            continue
        if node.is_router:
            nat: NatRule | None = _snat(node, out_iface, cur_src)
            if nat is not None:
                new_src: str = out_iface.address
                snat.append((node.hostname, new_src, cur_src))
                cur_src = new_src
        nxt: tuple[TNode, Iface] | None = net.on_vlan(out_iface.vlan, next_ip)
        if nxt is None:
            return done(
                "no-route",
                f"{node.hostname}: next hop {next_ip} not on {out_iface.vlan}",
            )
        node, in_iface = nxt
        path.append(node.hostname)
    return done("loop", "hop limit")


def flow(
    net: Net,
    src: str,
    dst: str,
    proto: str,
    port: int,
    *,
    dst_ip: str | None = None,
) -> tuple[Verdict, Verdict | None]:
    """Forward and reply verdicts for a flow from host ``src`` to host ``dst``.

    ``dst_ip`` overrides the destination address (the DNAT public address).
    The reply is traced only when the forward packet was delivered.
    """
    target: str = dst_ip or net.nodes[dst].interfaces[0].address
    fwd: Verdict = trace(net, src, target, proto, port)
    if fwd.result != "allowed":
        return fwd, None
    unnat: dict[str, tuple[str, str]] = {r: (new, old) for r, new, old in fwd.snat}
    client: TNode = net.nodes[src]
    wire_src: str = fwd.snat[-1][1] if fwd.snat else _first_src(net, src, target)
    del client
    rev: Verdict = trace(
        net,
        fwd.final_node,
        wire_src,
        proto,
        40000,
        sport=fwd.final_port,
        established=True,
        unnat=unnat,
    )
    return fwd, rev


def _first_src(net: Net, src: str, target: str) -> str:
    """The address a host uses as the source toward ``target``."""
    hop: tuple[Iface, str] | None = _route(net.nodes[src], target)
    assert hop is not None
    return hop[0].address
