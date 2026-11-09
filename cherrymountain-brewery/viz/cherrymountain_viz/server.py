"""The cm-viz server: the page, a read-only WebSocket, and two JSON endpoints.

One port carries everything, so one autotunnel forward (``18090:8090``,
DESIGN.md §15.10) reaches it:

- ``GET /`` and the files under ``web/``: the 3D page (``index.html``) and the
  2.5D SVG fallback (``svg.html``), with the vendored three.js.
- ``GET /ws``: a WebSocket that pushes the latest :class:`Frame` as JSON about
  once a second. Anything a browser sends is read and dropped: the view cannot
  write to the plant or to the event API.
- ``GET /frame.json``: the latest frame (for scripts and the SVG page's first
  paint).
- ``GET /healthz``: 200 ``{"status": "ok"}`` while frames are fresh, 503 with
  the reason while there is none or it is stale (for SoH and ``curl``).

Slow clients skip frames instead of queueing them. Every other method and
path gets a 404 or 405.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import mimetypes
import re
import threading
import time
from http import HTTPStatus
from importlib import resources
from pathlib import Path

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from cherrymountain_viz.sources import Source

LOG: logging.Logger = logging.getLogger("cherrymountain_viz.server")
WEB: Path = Path(str(resources.files("cherrymountain_viz") / "web"))
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")
#: Security headers on every HTTP reply: no framing by another origin, no
#: sniffing, and nothing loaded from anywhere but this server (air gap). An
#: HTML page's inline import map is allowed by its hash (:func:`csp`).
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("Referrer-Policy", "no-referrer"),
)
_INLINE_SCRIPT: re.Pattern[bytes] = re.compile(rb"<script type=\"importmap\">(.*?)</script>", re.S)


def csp(body: bytes = b"") -> str:
    """The Content-Security-Policy for a reply; inline import maps in an HTML
    body are allowed by their SHA-256."""
    hashes = " ".join(
        f"'sha256-{base64.b64encode(hashlib.sha256(m).digest()).decode()}'"
        for m in _INLINE_SCRIPT.findall(body)
    )
    script = f"script-src 'self' {hashes}".rstrip()
    return (
        f"default-src 'self'; {script}; connect-src 'self' ws: wss:; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'"
    )


class Hub:
    """Holds the latest frame text and wakes each client; read only."""

    text: str
    seq: int
    stale: bool
    waiters: set[asyncio.Event]
    clients: int
    sent: int

    def __init__(self) -> None:
        self.text = ""
        self.seq = -1
        self.stale = True
        self.waiters = set()
        self.clients = 0
        self.sent = 0

    def publish(self, text: str, seq: int, stale: bool) -> None:
        """Make ``text`` the latest frame and wake every client."""
        self.text, self.seq, self.stale = text, seq, stale
        for event in self.waiters:
            event.set()

    async def client(self, ws: ServerConnection) -> None:
        """Send the latest frame whenever one is ready; ignore what arrives."""
        wake = asyncio.Event()
        self.waiters.add(wake)
        self.clients = len(self.waiters)
        if self.text:
            wake.set()
        drain = asyncio.create_task(_ignore_input(ws))
        try:
            while True:
                await wake.wait()
                wake.clear()
                await ws.send(self.text)
                self.sent += 1
        except ConnectionClosed:
            pass
        finally:
            drain.cancel()
            self.waiters.discard(wake)
            self.clients = len(self.waiters)


async def _ignore_input(ws: ServerConnection) -> None:
    """Read and drop anything a browser sends."""
    with contextlib.suppress(ConnectionClosed):
        async for _ in ws:
            pass


async def produce(source: Source, hub: Hub, rate_hz: float) -> None:
    """Push the source's latest frame to the hub, at most ``rate_hz``."""
    last: tuple[int, bool] = (-1, False)
    while True:
        frame = source.frame()
        if frame is not None and (frame.seq, frame.stale) != last:
            last = (frame.seq, frame.stale)
            frame.sent_wall_ms = time.time() * 1000.0
            hub.publish(frame.model_dump_json(), frame.seq, frame.stale)
        await asyncio.sleep(1.0 / rate_hz)


def response(status: HTTPStatus, ctype: str, body: bytes) -> Response:
    """An HTTP reply with the security headers."""
    headers = Headers(
        [
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("Connection", "close"),
            ("Content-Security-Policy", csp(body if ctype.startswith("text/html") else b"")),
            *SECURITY_HEADERS,
        ]
    )
    return Response(status.value, status.phrase, headers, body)


def http_reply(path: str, hub: Hub, web: Path = WEB) -> Response:
    """Serve a file under ``web/``, ``/frame.json``, or ``/healthz``."""
    route = path.split("?", 1)[0]
    if route == "/frame.json":
        return response(HTTPStatus.OK, "application/json", hub.text.encode() or b"{}")
    if route == "/healthz":
        if not hub.text:
            doc, status = {"status": "waiting", "reason": "no frame yet"}, HTTPStatus.SERVICE_UNAVAILABLE
        elif hub.stale:
            doc, status = {"status": "stale", "seq": hub.seq}, HTTPStatus.SERVICE_UNAVAILABLE
        else:
            doc, status = {"status": "ok", "seq": hub.seq, "clients": hub.clients}, HTTPStatus.OK
        return response(status, "application/json", json.dumps(doc).encode())
    rel = route.lstrip("/") or "index.html"
    root = web.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return response(HTTPStatus.NOT_FOUND, "text/plain", b"not found\n")
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        ctype += "; charset=utf-8"
    return response(HTTPStatus.OK, ctype, target.read_bytes())


def make_server(hub: Hub, host: str, port: int, web: Path = WEB) -> serve:
    """The websockets server object (not yet started)."""

    def process_request(_conn: ServerConnection, request: Request) -> Response | None:
        if request.path.split("?", 1)[0] == "/ws":
            return None  # continue with the WebSocket handshake
        return http_reply(request.path, hub, web)

    return serve(hub.client, host, port, process_request=process_request, max_size=4096)


async def run(source: Source, host: str, port: int, rate_hz: float) -> None:
    """Serve until cancelled."""
    hub = Hub()
    async with make_server(hub, host, port) as server:
        LOG.info("cm-viz on http://%s:%d/ (WebSocket /ws, %.1f Hz)", host, bound_port(server), rate_hz)
        await produce(source, hub, rate_hz)


def bound_port(server: Server) -> int:
    """The port the server listens on (for port 0)."""
    port: int = next(iter(server.sockets)).getsockname()[1]
    return port


class BackgroundServer:
    """cm-viz served from a background thread (the browser test, screenshots)."""

    source: Source
    rate_hz: float
    loop: asyncio.AbstractEventLoop
    task: asyncio.Task[None] | None
    port: int
    ready: threading.Event
    thread: threading.Thread

    def __init__(self, source: Source, rate_hz: float = 4.0) -> None:
        self.source = source
        self.rate_hz = rate_hz
        self.loop = asyncio.new_event_loop()
        self.task = None
        self.port = 0
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="cm-viz", daemon=True)

    async def _serve(self) -> None:
        hub = Hub()
        async with make_server(hub, "127.0.0.1", 0) as server:
            self.port = bound_port(server)
            self.ready.set()
            await produce(self.source, hub, self.rate_hz)

    def _run(self) -> None:
        self.task = self.loop.create_task(self._serve())
        with contextlib.suppress(asyncio.CancelledError):
            self.loop.run_until_complete(self.task)
        self.loop.close()

    def start(self) -> str:
        """Start serving; the base URL."""
        self.thread.start()
        if not self.ready.wait(15):
            raise RuntimeError("cm-viz did not start")
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        """Stop serving and wait for the thread."""
        if self.task is not None:
            self.loop.call_soon_threadsafe(self.task.cancel)
        self.thread.join(10)
