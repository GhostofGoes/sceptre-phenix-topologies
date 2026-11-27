"""The conduits that cross from one site to another (docs/multisite/PLANS.md 4.6).

The conduit matrix (``conduits``) is that of one site: both ends of a row are
hosts of one topology. A row here has its two ends at two sites. Each end is a
role, and a site's plant description says which host has it (``netcheck.site``):

- ``sensor``: the network sensor of a site that has one.
- ``malcolm``: the fleet's one Malcolm, at the site with the ``soc`` module.

A site rules its own part of a row, on the firewalls between its host and the
far side of its edge (``site.FleetPair``). The packet crosses the WAN in a
tunnel of the edge. A tunnel is no interface of the topology, so phenix has no
ruleset for it: what comes out of a tunnel is the WAN generator's to filter
(``wangen``, stage 4). Until then, what a site's rules let out of the site
arrives at the far site's inner firewall, which rules it again where the far
site has one.

No SoH test probes these rows: a site's scenario runs alone, and the far end
is not there.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from netcheck import conduits

Role = Literal["sensor", "malcolm"]

#: The ports of a sensor's three conduits. The scripts of phenix-injects/sensor
#: and Malcolm's settings name the same ones (tests/test_fleet_conduits.py).
LOGSTASH_PORT: int = 5044
OPENSEARCH_PORT: int = 9200
VIEWER_PORT: int = 8005


class FleetConduit(BaseModel):
    """One row: every host of the role ``src`` to every host of the role
    ``dst`` at another site."""

    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    src: Role
    dst: Role
    services: tuple[conduits.Service, ...]


FLEET_CONDUITS: tuple[FleetConduit, ...] = (
    FleetConduit(
        id="fleet-sensor-logs",
        text="A site's sensor sends its Zeek and Suricata logs to Malcolm's Logstash "
        "(Filebeat, TLS with a client certificate)",
        src="sensor",
        dst="malcolm",
        services=conduits.tcp(LOGSTASH_PORT),
    ),
    FleetConduit(
        id="fleet-sensor-sessions",
        text="A site's sensor writes its Arkime sessions to Malcolm's OpenSearch "
        "(HTTPS through nginx, the account `sensor`)",
        src="sensor",
        dst="malcolm",
        services=conduits.tcp(OPENSEARCH_PORT),
    ),
    FleetConduit(
        id="fleet-sensor-pcap",
        text="Malcolm's Arkime asks a sensor's Arkime viewer for the packets of a session "
        "(HTTPS, signed with the secret that the two share)",
        src="malcolm",
        dst="sensor",
        services=conduits.tcp(VIEWER_PORT),
    ),
)

SERVICES: tuple[conduits.Service, ...] = tuple(
    dict.fromkeys(service for row in FLEET_CONDUITS for service in row.services)
)
