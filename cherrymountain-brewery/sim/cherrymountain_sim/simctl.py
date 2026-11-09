"""The sim-ctl event API (DESIGN.md §9.5).

A small HTTP API on sim-ctl through which the SCORCH ``plantevent`` component
and the ``cherrymountain`` app inject scenario events and read plant KPIs.
The contract is the one ``apps/cherrymountain_apps/simctl.py`` documents::

    GET  /api/v1/health   -> 200 {"status": "ok", ...}
    POST /api/v1/events   {"event": <kind>, ...parameters} -> 202 JSON
    POST /events          the same (short form)
    GET  /api/v1/events   -> 200 {"events": [...]} (accepted events, oldest first)
    GET  /api/v1/kpis     -> 200 plant KPIs

The server runs in a thread of the weather federate (which runs on sim-ctl).
Accepted events go into a queue; the federate drains it every step and
forwards each event to the federate that owns it as an endpoint message
``{"event": {"type": ..., "params": ..., "id": ...}}``. The KPIs are a
snapshot the federate refreshes every step from its subscriptions.

The server binds ``127.0.0.1`` by default: phenix's autotunnel forwards the
guest port from ``127.0.0.1`` (DESIGN.md §15.3), and MGMT is not an exercise
network.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

LOG: logging.Logger = logging.getLogger("cherrymountain_sim.simctl")
API: str = "/api/v1"
#: Event kind -> the federate that applies it.
EVENT_TARGETS: dict[str, str] = {
    "start-batch": "brewhouse",
    "utility-breaker": "power",
    "chiller-fault": "utilities",
    "co2-release": "cellar",
    "jam": "packaging",
    "weather-step": "weather",
    "cip-panel": "utilities",
}
#: Most events the history keeps.
HISTORY: int = 500
#: Largest request body accepted, bytes.
MAX_BODY: int = 16 * 1024


class EventRequest(BaseModel):
    """``POST /events`` body: the kind and its parameters (plantevent README)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    event: str

    @model_validator(mode="after")
    def _check(self) -> EventRequest:
        if self.event not in EVENT_TARGETS:
            raise ValueError(
                f"unknown event {self.event!r}; know {sorted(EVENT_TARGETS)}"
            )
        p = self.params
        match self.event:
            case "utility-breaker":
                if p.get("action") not in ("open", "close"):
                    raise ValueError("utility-breaker needs action: open or close")
            case "weather-step":
                delta = p.get("delta_f")
                if not isinstance(delta, int | float) or not -60 <= delta <= 60:
                    raise ValueError("weather-step needs delta_f between -60 and 60")
            case "co2-release":
                if not p.get("fv"):
                    raise ValueError("co2-release needs fv (for example FV-102)")
                if p.get("rate", "high") not in ("low", "medium", "high"):
                    raise ValueError("co2-release rate is low, medium, or high")
            case "chiller-fault":
                if p.get("action", "fail") not in ("fail", "clear"):
                    raise ValueError("chiller-fault action is fail or clear")
                if p.get("compressor", 1) not in (1, 2):
                    raise ValueError("chiller-fault compressor is 1 or 2 (two stages)")
            case "start-batch":
                if p.get("recipe", "fire-of-the-phenix") != "fire-of-the-phenix":
                    raise ValueError("start-batch recipe must be fire-of-the-phenix")
                brews = p.get("brews", 2)
                if not isinstance(brews, int) or isinstance(brews, bool) or not 1 <= brews <= 4:
                    raise ValueError("start-batch brews is an integer from 1 to 4")
                if not isinstance(p.get("batch_id", ""), str):
                    raise ValueError("start-batch batch_id is a string")
            case _:
                pass
        return self

    @property
    def params(self) -> dict[str, Any]:
        """Every field except ``event``."""
        return dict(self.model_extra or {})


class AcceptedEvent(BaseModel):
    """An event the API accepted."""

    id: str
    event: str
    params: dict[str, Any] = Field(default_factory=dict)
    target: str
    received: str  # simulated time when the API received it


class Kpis(BaseModel):
    """``GET /api/v1/kpis`` reply (the app's ``SimKpis``)."""

    sim_time: str | None = None
    batches_completed: int = 0
    batches_in_progress: int = 0
    bottles_packaged: int = 0
    kegs_packaged: int = 0
    energy_kwh: float = 0.0
    demand_kw: float = 0.0
    peak_demand_kw: float = 0.0


class EventApi:
    """State shared between the HTTP threads and the federate."""

    variant: str
    pending: queue.Queue[AcceptedEvent]
    history: list[AcceptedEvent]
    kpis: Kpis
    sim_time: str | None
    lock: threading.Lock
    server: ThreadingHTTPServer | None
    thread: threading.Thread | None

    def __init__(self, variant: str = "") -> None:
        self.variant = variant
        self.pending = queue.Queue()
        self.history = []
        self.kpis = Kpis()
        self.sim_time = None
        self.lock = threading.Lock()
        self.server = None
        self.thread = None

    # -- federate side -----------------------------------------------------------------

    def drain(self) -> list[AcceptedEvent]:
        """Events accepted since the last call."""
        out = []
        while True:
            try:
                out.append(self.pending.get_nowait())
            except queue.Empty:
                return out

    def update(self, sim_time: str, kpis: Kpis) -> None:
        """Refresh the clock and the KPI snapshot."""
        with self.lock:
            self.sim_time = sim_time
            self.kpis = kpis

    # -- HTTP side ---------------------------------------------------------------------

    def accept(self, body: dict[str, Any]) -> AcceptedEvent:
        """Validate and queue an event; raises ValueError when invalid."""
        try:
            req = EventRequest.model_validate(body)
        except ValidationError as exc:
            raise ValueError("; ".join(e["msg"] for e in exc.errors())) from None
        with self.lock:
            accepted = AcceptedEvent(
                id=uuid.uuid4().hex[:12],
                event=req.event,
                params=req.params,
                target=EVENT_TARGETS[req.event],
                received=self.sim_time or "",
            )
            self.history.append(accepted)
            del self.history[:-HISTORY]
        self.pending.put(accepted)
        LOG.info("event %s %s -> %s", accepted.event, accepted.params, accepted.target)
        return accepted

    def health(self) -> dict[str, Any]:
        """``GET /health`` reply."""
        with self.lock:
            return {"status": "ok", "variant": self.variant, "sim_time": self.sim_time}

    def kpi_reply(self) -> dict[str, Any]:
        """``GET /kpis`` reply."""
        with self.lock:
            return self.kpis.model_dump()

    def events_reply(self) -> dict[str, Any]:
        """``GET /events`` reply."""
        with self.lock:
            return {"events": [e.model_dump() for e in self.history]}

    def serve(self, host: str = "127.0.0.1", port: int = 8080) -> int:
        """Start the HTTP server in a daemon thread; returns the bound port."""
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "cherrymountain-simctl/1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                LOG.debug("%s %s", self.address_string(), format % args)

            def _reply(self, code: int, doc: dict[str, Any]) -> None:
                body = json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                routes = {
                    f"{API}/health": api.health,
                    f"{API}/kpis": api.kpi_reply,
                    f"{API}/events": api.events_reply,
                    "/events": api.events_reply,
                }
                handler = routes.get(self.path.split("?")[0].rstrip("/"))
                if handler is None:
                    self._reply(404, {"error": "not found"})
                else:
                    self._reply(200, handler())

            def do_POST(self) -> None:
                if self.path.split("?")[0].rstrip("/") not in (
                    f"{API}/events",
                    "/events",
                ):
                    self._reply(404, {"error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = -1
                if length < 0:
                    self._reply(400, {"error": "bad Content-Length"})
                    return
                if length > MAX_BODY:
                    self._reply(413, {"error": "body too large"})
                    return
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("the body must be a JSON object")
                    accepted = api.accept(body)
                except ValueError as exc:  # JSONDecodeError is a ValueError
                    self._reply(400, {"error": str(exc)})
                    return
                self._reply(202, {"accepted": True, **accepted.model_dump()})

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, name="simctl-api", daemon=True
        )
        self.thread.start()
        bound: int = self.server.server_address[1]
        LOG.info("event API on %s:%d", host, bound)
        return bound

    def close(self) -> None:
        """Stop the HTTP server."""
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
