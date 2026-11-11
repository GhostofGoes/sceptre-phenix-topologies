"""The sim-ctl event API, reached through a minimega cc tunnel.

phenix's ``phenix/startup-autotunnel`` annotation forwards guest ports of
sim-ctl to the host through ``cc tunnel``. The annotation's first number is
the forward's source port: the port ``phenix-tunneler`` opens on the machine
of a user who runs it, not a port on the phenix host. There minimega listens
on a random port in 50000-64999, which changes whenever the tunnel is
re-created (the ``autotunnel`` spike, DESIGN.md 15.3). This code runs on the
phenix host, so every caller looks the port up per run with ``cc tunnel list
<vm>`` (the row with ``dst 127.0.0.1`` and ``dst port <guest port>``, column
``src port``) and connects to the minimega host that serves the VM: 127.0.0.1
when that is the headnode, as phenix itself does.

phenix's ``GET /experiments/{exp}/vms/{vm}/forwards`` cannot replace the
lookup: its ``srcPort`` is the label, and the host port is not in the JSON
(``ClusterPort`` is tagged ``json:"-"`` in ``web/forward/forwardtypes``).

When no tunnel exists (upstream phenix, which creates none for a
CLI-started experiment and never re-creates one), :func:`resolve` can open
one the way phenix does: ``cc tunnel <vm> <random port> 127.0.0.1 <guest
port>``. Later lookups find and reuse it.

Event API contract (served by cherrymountain_sim on sim-ctl, DESIGN.md 9.5):

    GET  /api/v1/health   -> 200 {"status": "ok"}
    POST /api/v1/events   {"event": <kind>, ...parameters} -> 200/202 JSON
    GET  /api/v1/kpis     -> 200 JSON plant KPIs (see cherrymountain.kpi)
"""

from __future__ import annotations

import random
import socket
from typing import Any, ClassVar, Final

import requests
from phenix_apps.common import utils
from phenix_apps.common.logger import logger
from pydantic import BaseModel, ConfigDict

#: phenix's forward port range (forwardPortBase and forwardPortRange in
#: web/forward/forward.go).
PORT_BASE: Final[int] = 50000
PORT_RANGE: Final[int] = 15000
#: Attempts at a free host port when creating a tunnel, as phenix makes.
TUNNEL_ATTEMPTS: Final[int] = 10
API_PREFIX: Final[str] = "/api/v1"


class TunnelError(RuntimeError):
    """No tunnel to the guest port, and none could be created."""


class Endpoint(BaseModel):
    """Where the host reaches a guest port."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    vm: str
    guest_port: int
    #: The minimega host serving the tunnel, as minimega names it.
    cluster_host: str
    #: The address to connect to (127.0.0.1 for the headnode).
    host: str
    port: int
    created: bool = False

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _rows(responses: Any) -> list[tuple[str, dict[str, str]]]:
    """(minimega host, row) for every row of every host's response."""
    rows: list[tuple[str, dict[str, str]]] = []
    for resp in responses or []:
        if not isinstance(resp, dict) or resp.get("Error"):
            continue
        header: list[str] = resp.get("Header") or []
        for row in resp.get("Tabular") or []:
            rows.append(
                (str(resp.get("Host", "")), dict(zip(header, row, strict=False)))
            )
    return rows


def find(
    mm: Any, vm: str, guest_port: int, dst_host: str = "127.0.0.1"
) -> tuple[str, int] | None:
    """(minimega host, host port) of a tunnel to ``vm``'s ``guest_port``."""
    try:
        responses: Any = mm.cc_tunnel_list(vm)
    except Exception as ex:  # minimega raises a bare Error for "no such client"
        logger.debug(f"cc tunnel list {vm} failed: {type(ex).__name__}")
        return None
    for host, row in _rows(responses):
        if row.get("dst") == dst_host and row.get("dst port") == str(guest_port):
            src: str = row.get("src port", "")
            if src.isdigit():
                return host, int(src)
    return None


#: Suffixes the phenix and minimega containers add to the host name (phenix's
#: default --hostname-suffixes).
HOST_SUFFIXES: Final[tuple[str, ...]] = ("-minimega", "-phenix")


def is_headnode(mm: Any, host: str) -> bool:
    """Whether minimega ``host`` is the headnode. Uses phenix-apps' helper
    where it exists (the fork); upstream has none, so this compares the names
    without the container suffixes, as phenix core does."""
    helper: Any = getattr(utils, "is_headnode", None)
    if helper is not None:
        return bool(helper(host, mm))

    def trim(name: str) -> str:
        for suffix in HOST_SUFFIXES:
            name = name.removesuffix(suffix)
        return name

    return trim(host) == trim(socket.gethostname())


def connect_host(mm: Any, cluster_host: str) -> str:
    """The address phenix would use for a tunnel on ``cluster_host``."""
    if not cluster_host or is_headnode(mm, cluster_host):
        return "127.0.0.1"
    return cluster_host


def resolve(mm: Any, vm: str, guest_port: int, *, create: bool = True) -> Endpoint:
    """The host endpoint of ``vm:guest_port``, looked up now."""
    found: tuple[str, int] | None = find(mm, vm, guest_port)
    created: bool = False
    if found is None:
        if not create:
            raise TunnelError(
                f"no cc tunnel to {vm}:{guest_port}; is the VM's C2 client active, and does "
                f"{vm} carry a phenix/startup-autotunnel entry for port {guest_port}?"
            )
        try:
            utils.mm_cc_client_locate(mm, vm, grace=0)
        except Exception as ex:
            raise TunnelError(
                f"{vm} has no active C2 client, so no cc tunnel can reach it"
            ) from ex
        for _ in range(TUNNEL_ATTEMPTS):
            port: int = PORT_BASE + random.randrange(PORT_RANGE)
            try:
                mm.cc_tunnel(vm, port, "127.0.0.1", guest_port)
            except Exception as ex:
                if "address already in use" in str(ex):
                    continue
                raise TunnelError(
                    f"creating a cc tunnel to {vm}:{guest_port} failed"
                ) from ex
            break
        found = find(mm, vm, guest_port)
        if found is None:
            raise TunnelError(
                f"created a cc tunnel to {vm}:{guest_port}, but cc tunnel list does not show it"
            )
        created = True
        logger.info(f"opened a cc tunnel to {vm}:{guest_port} on host port {found[1]}")
    cluster, port = found
    return Endpoint(
        vm=vm,
        guest_port=guest_port,
        cluster_host=cluster,
        host=connect_host(mm, cluster),
        port=port,
        created=created,
    )


class ApiError(RuntimeError):
    """The event API refused a request or did not answer."""


class EventApi:
    """A small client for the sim-ctl event API."""

    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url: str = base_url.rstrip("/")
        self.timeout: float = timeout
        self.session: requests.Session = requests.Session()

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        url: str = f"{self.base_url}{API_PREFIX}{path}"
        try:
            resp: requests.Response = self.session.request(
                method, url, json=body, timeout=self.timeout
            )
        except requests.RequestException as ex:
            raise ApiError(
                f"{method} {API_PREFIX}{path}: {type(ex).__name__}"
            ) from None
        if resp.status_code >= 400:
            raise ApiError(f"{method} {API_PREFIX}{path}: HTTP {resp.status_code}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            raise ApiError(
                f"{method} {API_PREFIX}{path}: the reply is not JSON"
            ) from None

    def health(self) -> Any:
        return self._request("GET", "/health")

    def post_event(self, event: dict[str, Any]) -> Any:
        return self._request("POST", "/events", event)

    def kpis(self) -> Any:
        return self._request("GET", "/kpis")
