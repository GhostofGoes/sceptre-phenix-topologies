"""The cm-viz server: the page, a read-only WebSocket, and two JSON endpoints.

One port carries everything, so one autotunnel forward (``18090:8090``,
DESIGN.md §15.10) reaches it:

- ``GET /`` and the files under ``web/``: the 3D page (``index.html``) and the
  2.5D SVG fallback (``svg.html``), with the vendored three.js.
- ``GET /ws``: a WebSocket that pushes the latest :class:`Frame` as JSON when
  it changes and at least once a second (a heartbeat, so a browser can tell a
  quiet plant from a dead link). Anything a browser sends is read and dropped:
  the view cannot write to the plant or to the event API.
- ``GET /frame.json``: the latest frame (for scripts).
- ``GET /healthz``: 200 ``{"status": "ok"}`` while frames are fresh and the
  source is healthy, 503 with the reason otherwise (for SoH and ``curl``).

Requests are checked before anything is served: the ``Host`` header must name
a loopback address or an allowed host (DNS rebinding), a WebSocket ``Origin``
must be the same host and port as ``Host`` (cross-site reads), and at most
:data:`MAX_CLIENTS` WebSockets are open at once. Any method but GET gets 405,
an unknown or malformed path 404. Slow clients skip frames instead of
queueing them.
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
from collections.abc import Generator, Iterable
from http import HTTPStatus
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed, InvalidMessage
from websockets.http11 import Request, Response, parse_headers, parse_line
from websockets.protocol import CONNECTING, Protocol
from websockets.server import ServerProtocol

from cherrymountain_viz.sources import Source

LOG: logging.Logger = logging.getLogger("cherrymountain_viz.server")
WEB: Path = Path(str(resources.files("cherrymountain_viz") / "web"))
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")
#: Host names a request may address without --allow-host (the autotunnel
#: arrives on the phenix host's loopback).
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})
#: Open WebSockets at most; more get 503 (a white cell is a handful of people).
MAX_CLIENTS: int = 32
#: Re-send the latest frame at least this often (s), with a fresh send time.
HEARTBEAT_S: float = 1.0
#: The path a non-GET request is given, so process_request can answer 405.
_BAD_METHOD: str = "\x00method:"
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
    body are allowed by their SHA-256. ``connect-src 'self'`` covers the
    page's own ws: and wss: origin (CSP Level 3)."""
    hashes = " ".join(
        f"'sha256-{base64.b64encode(hashlib.sha256(m).digest()).decode()}'"
        for m in _INLINE_SCRIPT.findall(body)
    )
    script = f"script-src 'self' {hashes}".rstrip()
    return (
        f"default-src 'self'; {script}; connect-src 'self'; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'"
    )


# -- request parsing ------------------------------------------------------------------


def _parse_request(
    read_line: Any,
) -> Generator[None, None, Request]:
    """websockets' ``Request.parse``, except that a method other than GET
    becomes a request for ``_BAD_METHOD + method`` (answered 405) instead of an
    error that drops the connection and logs a traceback."""
    try:
        line: bytes = yield from parse_line(read_line)
    except EOFError as exc:
        raise EOFError("connection closed while reading HTTP request line") from exc
    try:
        method, raw_path, protocol = line.split(b" ", 2)
    except ValueError:
        raise ValueError(f"invalid HTTP request line: {line!r}") from None
    if protocol != b"HTTP/1.1":
        raise ValueError(f"unsupported protocol: {line!r}")
    headers: Headers = yield from parse_headers(read_line)
    if method != b"GET":
        return Request(_BAD_METHOD + method.decode("ascii", "replace"), headers)
    if "Transfer-Encoding" in headers or "Content-Length" in headers:
        raise ValueError("unsupported request body")
    return Request(raw_path.decode("ascii", "surrogateescape"), headers)


class _Protocol(ServerProtocol):
    """ServerProtocol with :func:`_parse_request`."""

    def parse(self) -> Generator[None]:
        if self.state is CONNECTING:
            try:
                request = yield from _parse_request(self.reader.read_line)
            except Exception as exc:
                self.handshake_exc = InvalidMessage("did not receive a valid HTTP request")
                self.handshake_exc.__cause__ = exc
                self.send_eof()
                self.parser = self.discard()
                next(self.parser)
                yield
            self.events.append(request)
            if request.path.startswith(_BAD_METHOD):
                # A refused method: its body (if any) is not WebSocket data.
                # Drop everything until the 405 goes out, which closes the
                # connection and replaces this parser.
                while not (yield from self.reader.at_eof()):
                    self.reader.discard()
                yield
        yield from Protocol.parse(self)


class _Connection(ServerConnection):
    """Swaps in :class:`_Protocol` before any byte is read (``serve`` builds a
    plain ServerProtocol and has no hook for another)."""

    def __init__(self, protocol: ServerProtocol, *args: Any, **kwargs: Any) -> None:
        protocol.__class__ = _Protocol
        protocol.parser = protocol.parse()
        next(protocol.parser)
        super().__init__(protocol, *args, **kwargs)


def _host_name(netloc: str) -> str:
    """``[::1]:8090`` -> ``::1``, ``Localhost:8090`` -> ``localhost``."""
    return (urlsplit(f"//{netloc}").hostname or "").lower()


# -- the hub ------------------------------------------------------------------------------


class Hub:
    """Holds the latest frame text and wakes each client; read only."""

    text: str
    seq: int
    stale: bool
    #: Why the source is unhealthy (a dead poller, an unreachable sim-ctl), or None.
    problem: str | None
    waiters: set[asyncio.Event]
    clients: int
    sent: int

    def __init__(self) -> None:
        self.text = ""
        self.seq = -1
        self.stale = True
        self.problem = None
        self.waiters = set()
        self.clients = 0
        self.sent = 0

    def publish(self, text: str, seq: int, stale: bool, problem: str | None = None) -> None:
        """Make ``text`` the latest frame and wake every client."""
        self.text, self.seq, self.stale, self.problem = text, seq, stale, problem
        for event in self.waiters:
            event.set()

    async def client(self, ws: ServerConnection) -> None:
        """Send the latest frame whenever one is ready; ignore what arrives.
        Returns as soon as the client goes away, sent frame or not."""
        wake = asyncio.Event()
        self.waiters.add(wake)
        self.clients = len(self.waiters)
        if self.text:
            wake.set()
        drain = asyncio.create_task(_ignore_input(ws))
        try:
            while True:
                woken = asyncio.create_task(wake.wait())
                done, _ = await asyncio.wait({woken, drain}, return_when=asyncio.FIRST_COMPLETED)
                if drain in done:
                    woken.cancel()
                    break
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
    """Read and drop anything a browser sends; returns when it disconnects."""
    with contextlib.suppress(ConnectionClosed):
        async for _ in ws:
            pass


def _problem(source: Source) -> str | None:
    health = getattr(source, "health", None)
    return health() if callable(health) else None


async def produce(source: Source, hub: Hub, rate_hz: float) -> None:
    """Push the source's latest frame to the hub when it changes (at most
    ``rate_hz``) and every HEARTBEAT_S, stamped with the send time; a frame is
    sent as stale while the source reports a problem."""
    last: tuple[int, bool, str | None] = (-1, False, None)
    last_sent = 0.0
    while True:
        frame = source.frame()
        problem = _problem(source)
        now = time.monotonic()
        if frame is not None:
            stale = frame.stale or problem is not None
            key = (frame.seq, stale, problem)
            if key != last or now - last_sent >= HEARTBEAT_S:
                last, last_sent = key, now
                out = frame.model_copy(update={"stale": stale, "sent_wall_ms": time.time() * 1000.0})
                hub.publish(out.model_dump_json(), frame.seq, stale, problem)
        elif problem != hub.problem:
            hub.problem = problem
        await asyncio.sleep(1.0 / rate_hz)


# -- HTTP -----------------------------------------------------------------------------------


def response(
    status: HTTPStatus, ctype: str, body: bytes, extra: Iterable[tuple[str, str]] = ()
) -> Response:
    """An HTTP reply with the security headers."""
    headers = Headers(
        [
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("Connection", "close"),
            ("Content-Security-Policy", csp(body if ctype.startswith("text/html") else b"")),
            *SECURITY_HEADERS,
            *extra,
        ]
    )
    return Response(status.value, status.phrase, headers, body)


def _text(status: HTTPStatus, msg: str, extra: Iterable[tuple[str, str]] = ()) -> Response:
    return response(status, "text/plain; charset=utf-8", f"{msg}\n".encode(), extra)


def _health(hub: Hub) -> Response:
    if hub.problem:
        doc: dict[str, Any] = {"status": "unhealthy", "reason": hub.problem, "seq": hub.seq}
    elif not hub.text:
        doc = {"status": "waiting", "reason": "no frame yet"}
    elif hub.stale:
        doc = {"status": "stale", "seq": hub.seq}
    else:
        doc = {"status": "ok", "seq": hub.seq, "clients": hub.clients}
    status = HTTPStatus.OK if doc["status"] == "ok" else HTTPStatus.SERVICE_UNAVAILABLE
    return response(status, "application/json", json.dumps(doc).encode())


def _file(route: str, web: Path) -> Response:
    """A file under ``web/``; 404 for anything outside it, missing, or not a
    valid file name (a NUL byte, a name the file system rejects)."""
    try:
        root = web.resolve()
        target = (root / (route.lstrip("/") or "index.html")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return _text(HTTPStatus.NOT_FOUND, "not found")
        body = target.read_bytes()
    except (OSError, ValueError):
        return _text(HTTPStatus.NOT_FOUND, "not found")
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        ctype += "; charset=utf-8"
    return response(HTTPStatus.OK, ctype, body)


def http_reply(path: str, hub: Hub, web: Path = WEB) -> Response:
    """Serve a file under ``web/``, ``/frame.json``, or ``/healthz``."""
    route = path.split("?", 1)[0]
    if route == "/frame.json":
        return response(HTTPStatus.OK, "application/json", hub.text.encode() or b"{}")
    if route == "/healthz":
        return _health(hub)
    return _file(route, web)


def check_request(
    request: Request, hub: Hub, allowed_hosts: frozenset[str]
) -> Response | None:
    """The reply that refuses ``request``, or None to serve it."""
    if request.path.startswith(_BAD_METHOD):
        return _text(HTTPStatus.METHOD_NOT_ALLOWED, "only GET", [("Allow", "GET")])
    host = request.headers.get("Host", "")
    if _host_name(host) not in allowed_hosts:
        return _text(HTTPStatus.FORBIDDEN, "host not allowed")
    if request.path.split("?", 1)[0] != "/ws":
        return None
    origin = request.headers.get("Origin")
    # A browser always sends Origin on a WebSocket; it must be this server.
    # Non-browser clients (scripts, tests) send none and cannot be used for a
    # cross-site read.
    if origin is not None:
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or parts.netloc.lower() != host.lower():
            return _text(HTTPStatus.FORBIDDEN, "origin not allowed")
    if hub.clients >= MAX_CLIENTS:
        return _text(HTTPStatus.SERVICE_UNAVAILABLE, "too many viewers")
    return None


def make_server(
    hub: Hub,
    host: str,
    port: int,
    web: Path = WEB,
    allowed_hosts: Iterable[str] = (),
) -> serve:
    """The websockets server object (not yet started). ``allowed_hosts`` adds
    names a browser may use in ``Host`` besides the loopback ones."""
    allowed = LOOPBACK_HOSTS | {h.lower() for h in allowed_hosts}

    def process_request(_conn: ServerConnection, request: Request) -> Response | None:
        refused = check_request(request, hub, allowed)
        if refused is not None:
            return refused
        if request.path.split("?", 1)[0] == "/ws":
            return None  # continue with the WebSocket handshake
        return http_reply(request.path, hub, web)

    return serve(
        hub.client,
        host,
        port,
        process_request=process_request,
        max_size=4096,
        create_connection=_Connection,
    )


async def run(
    source: Source, host: str, port: int, rate_hz: float, allowed_hosts: Iterable[str] = ()
) -> None:
    """Serve until cancelled."""
    hub = Hub()
    async with make_server(hub, host, port, allowed_hosts=allowed_hosts) as server:
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
    hub: Hub
    port: int
    ready: threading.Event
    thread: threading.Thread

    def __init__(self, source: Source, rate_hz: float = 4.0) -> None:
        self.source = source
        self.rate_hz = rate_hz
        self.loop = asyncio.new_event_loop()
        self.task = None
        self.hub = Hub()
        self.port = 0
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="cm-viz", daemon=True)

    async def _serve(self) -> None:
        async with make_server(self.hub, "127.0.0.1", 0) as server:
            self.port = bound_port(server)
            self.ready.set()
            await produce(self.source, self.hub, self.rate_hz)

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
