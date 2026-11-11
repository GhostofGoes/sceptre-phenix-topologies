"""The bridge: the server with a fixed source, the sim-ctl source against the
real event API, and the offline harness's scenarios."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from cherrymountain_viz.frame import Frame, build_frame
from cherrymountain_viz.server import Hub, bound_port, csp, make_server, produce
from cherrymountain_viz.sources import LocalSource, SimctlSource, StaticSource

GENERATED: Path = Path(__file__).resolve().parents[2] / "plant" / "generated"


def fixed_frame(seq: int = 7, stale: bool = False) -> Frame:
    return build_frame(
        {"building/cellar.co2_ppm": 1234.0},
        seq=seq,
        source="fixed",
        sim_t_s=1.0,
        sim_time="2026-07-15T04:30:01-06:00",
        sampled_wall_ms=time.time() * 1000.0,
        stale=stale,
    )


def get(url: str) -> tuple[int, dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_server_pages_websocket_and_health() -> None:
    """GET serves the page, the frame, and health; /ws pushes the frame and
    ignores what a client sends; nothing outside web/ is served."""

    async def scenario() -> None:
        hub = Hub()
        async with make_server(hub, "127.0.0.1", 0) as server:
            port = bound_port(server)
            base = f"http://127.0.0.1:{port}"
            loop = asyncio.get_running_loop()
            code, _, body = await loop.run_in_executor(None, get, f"{base}/healthz")
            assert code == 503 and json.loads(body)["status"] == "waiting"
            task = asyncio.create_task(produce(StaticSource(fixed_frame()), hub, 20.0))
            try:
                async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    await ws.send("please open the purge fans")  # dropped
                    frame = Frame.model_validate_json(await asyncio.wait_for(ws.recv(), 5))
                    assert frame.seq == 7 and frame.cellar.air.co2_mixed_ppm == 1234.0
                    assert frame.sent_wall_ms > 0
                code, _, body = await loop.run_in_executor(None, get, f"{base}/healthz")
                assert code == 200 and json.loads(body)["seq"] == 7
                code, headers, body = await loop.run_in_executor(None, get, f"{base}/")
                assert code == 200 and b"importmap" in body
                assert "sha256-" in headers["Content-Security-Policy"]
                assert headers["X-Frame-Options"] == "SAMEORIGIN"
                code, headers, _ = await loop.run_in_executor(
                    None, get, f"{base}/vendor/three/three.module.js"
                )
                assert code == 200 and headers["Content-Type"].startswith("text/javascript")
                for path in ("/../server.py", "/%2e%2e/server.py", "/nope.html"):
                    assert (await loop.run_in_executor(None, get, base + path))[0] == 404
                code, _, body = await loop.run_in_executor(None, get, f"{base}/frame.json")
                assert json.loads(body)["seq"] == 7
                hub.publish(fixed_frame(8, stale=True).model_dump_json(), 8, True)
                code, _, body = await loop.run_in_executor(None, get, f"{base}/healthz")
                assert code == 503 and json.loads(body)["status"] == "stale"
            finally:
                task.cancel()

    asyncio.run(scenario())


def test_csp_allows_only_the_page_import_map() -> None:
    page = b'<script type="importmap">{"imports": {}}</script>'
    assert csp(page).count("sha256-") == 1
    assert "sha256-" not in csp(b"")
    assert "'unsafe-eval'" not in csp(page)


def test_simctl_source_reads_the_event_api_and_goes_stale() -> None:
    """SimctlSource polls GET /api/v1/state; a frozen seq or a dead API marks
    the last frame stale."""
    from cherrymountain_sim.simctl import EventApi, Kpis, State

    api = EventApi("full")
    port = api.serve("127.0.0.1", 0)
    try:
        src = SimctlSource(f"http://127.0.0.1:{port}", buses={"fans": "ep1"})
        state = State(
            seq=3,
            t_s=2.0,
            sim_time="2026-07-15T04:30:02-06:00",
            wall_time=time.time(),
            variant="full",
            values={"building/load.fans.kw": 1.5, "building/cellar.co2_ppm": 900.0},
        )
        api.update(state.sim_time or "", Kpis(), state)
        src.poll_once()
        f = src.frame()
        assert f is not None and f.seq == 3 and not f.stale and f.variant == "full"
        assert f.power.loads[0].bus == "ep1" and f.cellar.air.co2_mixed_ppm == 900.0
        assert f.operator is None  # not connected in the range yet
        src.last_change -= 60.0  # the snapshot stopped advancing a minute ago
        src.poll_once()
        assert src.frame() is not None and src.frame().stale  # type: ignore[union-attr]
    finally:
        api.close()
    src2 = SimctlSource(f"http://127.0.0.1:{port}", timeout_s=0.5)
    src2.poll_once()  # nothing listens: no frame, no exception
    assert src2.frame() is None


@pytest.mark.parametrize(
    ("scenario", "seconds", "check"),
    [
        ("co2-attack", 200, "attack"),
        ("outage", 60, "outage"),
    ],
)
def test_local_source_scenarios(scenario: str, seconds: float, check: str) -> None:
    """The offline harness plays a scenario and frames show its consequences."""
    src = LocalSource(GENERATED / "full" / "helics.json", scenario=scenario, fast_forward_s=seconds)
    f = src.frame()
    assert f is not None and f.operator is not None
    if check == "attack":
        assert f.operator.frozen
        assert not any(fan.commanded for fan in f.cellar.air.fans)
        assert any(v.leak_gps == 40.0 for v in f.cellar.vessels)
        assert any("BACnet write" in n for n in f.notes)
    else:
        assert f.power.ats_on_emergency and f.power.gen_running
        assert f.power.buses["boiler"] is False


def test_poller_survives_any_error_and_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A truncated reply (http.client.IncompleteRead, not an OSError), a bad
    document, or a bug past the fetch must not kill the poller: it keeps
    polling, recovers, and reports the problem through health() (which
    /healthz serves) once it has lasted STALE_AFTER_S."""
    import http.client

    from cherrymountain_viz import sources

    monkeypatch.setattr(sources, "STALE_AFTER_S", 0.3)
    src = SimctlSource("http://127.0.0.1:9", poll_s=0.02)
    state = {"n": 0, "broken": False}

    def fetch() -> sources.StateSnapshot:
        state["n"] += 1
        if state["broken"] or state["n"] <= 3:
            raise http.client.IncompleteRead(b"partial")
        if state["n"] == 4:
            raise RuntimeError("bad snapshot")
        return sources.StateSnapshot(
            seq=state["n"], t_s=float(state["n"]), wall_time=time.time(), values={}
        )

    real_build = sources.build_frame
    builds = {"n": 0}

    def flaky_build(*args: object, **kwargs: object) -> Frame:
        builds["n"] += 1
        if builds["n"] == 1:
            raise ValueError("a bug past the fetch")
        return real_build(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(src, "fetch", fetch)
    monkeypatch.setattr(sources, "build_frame", flaky_build)
    src.start()
    try:
        deadline = time.monotonic() + 5
        while (f := src.frame()) is None or f.seq < 8:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert src.is_alive() and src.health() is None and not f.stale
        state["broken"] = True
        time.sleep(0.6)
        assert src.is_alive()
        problem = src.health()
        assert problem is not None and "IncompleteRead" in problem
        assert src.frame() is not None and src.frame().stale  # type: ignore[union-attr]
    finally:
        src.stop.set()
        src.join(5)
    assert src.health() == "the sim-ctl poller stopped"
