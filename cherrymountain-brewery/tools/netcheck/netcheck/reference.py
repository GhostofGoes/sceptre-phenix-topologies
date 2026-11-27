"""What a variant or a site is checked against: its plan and its conduits.

The checks take the plan and the conduit matrix from here, not from the tables
of ``plan`` and ``conduits`` directly. For ``full``, ``lite``, and ``ci`` the
reference is those tables as they are (``for_variant``). For a site of the
fleet, ``netcheck.site`` derives it from the same tables and the site's plant
description.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from netcheck import conduits, plan

Flow = tuple[str, str, conduits.Proto, int]


class Reference(BaseModel):
    """The nodes, VLANs, conduits, and blocked paths of one variant or site."""

    model_config = ConfigDict(frozen=True)

    name: str
    #: The nodes in boot order.
    nodes: tuple[plan.Node, ...]
    vlans: dict[str, plan.Vlan]
    conduits: tuple[conduits.Conduit, ...]
    blocked: tuple[conduits.BlockedProbe, ...]
    #: The untrusted side of the edge: no firewall rule of this plant protects it.
    external_vlans: frozenset[str]
    #: (VMs, vCPUs, GB) when the design states them.
    totals: tuple[int, int, int] | None = None
    #: A site's conduits name hosts of the site, with no selector to expand.
    site: bool = False
    #: The bridge that the workflow must name: each workflow has its own.
    bridge: str = ""

    def expand(self, conduit: conduits.Conduit) -> list[Flow]:
        """Every (src host, dst host, proto, port) that a conduit allows here."""
        if not self.site:
            return conduits.expand(conduit, self.name)
        return [
            (src, dst, svc.proto, svc.port)
            for src in conduit.srcs
            for dst in conduit.dsts
            if src != dst
            for svc in conduit.services
        ]

    def expected_flows(self) -> set[Flow]:
        """The flows that the firewalls must allow (enforced conduits only)."""
        flows: set[Flow] = set()
        for conduit in self.conduits:
            if conduit.enforced:
                flows.update(self.expand(conduit))
        return flows


def for_variant(
    variant: str, external_vlans: frozenset[str], totals: tuple[int, int, int] | None
) -> Reference:
    """The Front Range tables, for ``full``, ``lite``, or ``ci``."""
    nodes: list[plan.Node] = plan.nodes_for(variant) if variant in plan.VARIANT_MODULES else []
    return Reference(
        name=variant,
        nodes=tuple(nodes),
        vlans=plan.VLANS,
        conduits=conduits.CONDUITS,
        blocked=conduits.BLOCKED_PROBES,
        external_vlans=external_vlans,
        totals=totals,
        bridge=plan.BRIDGES.get(variant, ""),
    )
