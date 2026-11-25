"""The server's edges: closed and excess clients, bad methods, paths, hosts and
origins, the heartbeat, and health from a failing source."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from cherrymountain_viz import server as srv
from cherrymountain_viz.frame import Frame, build_frame
from cherrymountain_viz.server import Hub, bound_port, make_server, produce
from cherrymountain_viz.sources import StaticSource


def frame(seq: int = 1) -> Frame:
    return build_frame(
        {"building/cellar.co2_ppm": 900.0},
        seq=seq,
        source="test",
        sim_t_s=1.0,
        sim_time=None,
        sampled_wall_ms=time.time() * 1000.0,
    )


def raw_http(port: int, request: bytes) -> tuple[int, dict[str, str], bytes]:
    """Send ``request`` as is and read the reply until the server closes."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(request)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1]) if lines and lines[0] else 0
    headers = {k.lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
    return status, headers, body


type Scenario = Callable[[Hub, int], Coroutine[Any, Any, None]]


def serve_and(scenario: Scenario, hub: Hub | None = None) -> None:
    """Run ``scenario(hub, port)`` against a server on a free port."""

    async def main() -> None:
        h = hub or Hub()
        async with make_server(h, "127.0.0.1", 0) as server:
            await scenario(h, bound_port(server))

    asyncio.run(main())


async def in_thread(fn: Callable[..., Any], *args: Any) -> Any:
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


async def until(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.02)


def test_closed_clients_leave_at_once() -> None:
    """A client that goes away is dropped without waiting for the next frame."""

    async def scenario(hub: Hub, port: int) -> None:
        hub.publish(frame().model_dump_json(), 1, False)
        sockets = [await connect(f"ws://127.0.0.1:{port}/ws") for _ in range(3)]
        for ws in sockets:
            await ws.recv()
        await until(lambda: hub.clients == 3)
        for ws in sockets:
            await ws.close()
        await until(lambda: hub.clients == 0, 1.0)  # no publish in between

    serve_and(scenario)


def test_more_viewers_than_the_cap_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv, "MAX_CLIENTS", 3)

    async def scenario(hub: Hub, port: int) -> None:
        hub.publish(frame().model_dump_json(), 1, False)
        sockets = [await connect(f"ws://127.0.0.1:{port}/ws") for _ in range(3)]
        await until(lambda: hub.clients == 3)
        with pytest.raises(InvalidStatus) as exc:
            await connect(f"ws://127.0.0.1:{port}/ws")
        assert exc.value.response.status_code == 503
        await sockets[0].close()
        await until(lambda: hub.clients == 2)
        extra = await connect(f"ws://127.0.0.1:{port}/ws")
        await extra.recv()
        for ws in [*sockets[1:], extra]:
            await ws.close()

    serve_and(scenario)


def test_bad_methods_get_405_without_a_traceback(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    async def scenario(hub: Hub, port: int) -> None:
        for req in (
            b"POST /ws HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 3\r\n\r\nabc",
            b"HEAD / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            b"PUT /frame.json HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
        ):
            status, headers, _ = await in_thread(raw_http, port, req)
            assert status == 405 and headers["allow"] == "GET"

    serve_and(scenario)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    "path",
    [b"/a\x00b", b"/" + b"x" * 300, b"/" + b"/".join([b"y" * 200] * 30), b"/../server.py", b"/nope"],
)
def test_bad_paths_get_404(path: bytes, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    async def scenario(hub: Hub, port: int) -> None:
        req = b"GET " + path + b" HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        status, _, body = await in_thread(raw_http, port, req)
        assert status == 404, body

    serve_and(scenario)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    ("host", "status"),
    [("127.0.0.1", 200), ("LOCALHOST:8090", 200), ("[::1]:1", 200), ("evil.example", 403), ("", 403)],
)
def test_host_header_must_be_loopback(host: str, status: int) -> None:
    """DNS rebinding: a page on another site that rebinds its name to
    127.0.0.1 still sends its own name in Host."""

    async def scenario(hub: Hub, port: int) -> None:
        hdr = f"Host: {host}\r\n" if host else ""
        req = f"GET / HTTP/1.1\r\n{hdr}\r\n".encode()
        assert (await in_thread(raw_http, port, req))[0] == status

    serve_and(scenario)


def test_extra_allowed_hosts() -> None:
    async def main() -> None:
        hub = Hub()
        async with make_server(hub, "127.0.0.1", 0, allowed_hosts=["phenix-host"]) as server:
            port = bound_port(server)
            req = b"GET / HTTP/1.1\r\nHost: phenix-host:5123\r\n\r\n"
            assert (await in_thread(raw_http, port, req))[0] == 200

    asyncio.run(main())


@pytest.mark.parametrize(
    ("origin", "ok"),
    [
        (None, True),  # not a browser
        ("http://127.0.0.1:{port}", True),
        ("http://evil.example", False),
        ("http://127.0.0.1:1", False),  # another local page
        ("null", False),
    ],
)
def test_websocket_origin_must_be_this_server(origin: str | None, ok: bool) -> None:
    async def scenario(hub: Hub, port: int) -> None:
        hub.publish(frame().model_dump_json(), 1, False)
        url = f"ws://127.0.0.1:{port}/ws"
        kw: dict[str, Any] = {}
        if origin is not None:
            kw["origin"] = origin.format(port=port)
        if ok:
            async with connect(url, **kw) as ws:
                assert Frame.model_validate_json(await ws.recv()).seq == 1
        else:
            with pytest.raises(InvalidStatus) as exc:
                await connect(url, **kw)
            assert exc.value.response.status_code == 403

    serve_and(scenario)


def test_heartbeat_resends_with_a_fresh_send_time() -> None:
    """An unchanged frame is re-sent each HEARTBEAT_S, its sent time moving
    and its sampled time not: the page's data age grows while nothing new
    arrives, on the server's clock alone."""

    async def scenario(hub: Hub, port: int) -> None:
        task = asyncio.create_task(produce(StaticSource(frame(5)), hub, 20.0))
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                a = Frame.model_validate_json(await asyncio.wait_for(ws.recv(), 3))
                b = Frame.model_validate_json(await asyncio.wait_for(ws.recv(), 3))
            assert a.seq == b.seq == 5
            assert a.sampled_wall_ms == b.sampled_wall_ms
            assert b.sent_wall_ms - a.sent_wall_ms >= srv.HEARTBEAT_S * 1000 * 0.9
        finally:
            task.cancel()

    serve_and(scenario)


class SickSource(StaticSource):
    """A source that reports a problem."""

    def health(self) -> str | None:
        return "the sim-ctl poller stopped"


def test_an_unhealthy_source_makes_frames_stale_and_healthz_503() -> None:
    async def scenario(hub: Hub, port: int) -> None:
        task = asyncio.create_task(produce(SickSource(frame(9)), hub, 20.0))
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                assert Frame.model_validate_json(await asyncio.wait_for(ws.recv(), 3)).stale
            req = b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
            status, _, body = await in_thread(raw_http, port, req)
            assert status == 503
            assert json.loads(body) == {
                "status": "unhealthy",
                "reason": "the sim-ctl poller stopped",
                "seq": 9,
            }
        finally:
            task.cancel()

    serve_and(scenario)
