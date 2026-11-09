"""viz3d spike bridge: push CherryMountain cellar state to browsers over WebSocket.

A proof of concept for the 3D-visualization spike (README.md one level up). It
serves the pages in ``web/`` and a read-only WebSocket (``/ws``) on one port,
so one autotunnel forward would carry both. Browsers only receive; anything
they send is ignored.

Two sources:

- ``local`` runs the real federates in-process with the offline harness
  (``cherrymountain_sim.local.LocalFederation``, no HELICS), paced at
  ``--speed`` simulated seconds per wall second, and plays a scripted
  scenario (a CO2 release, a malicious BACnet write that stops the purge fans,
  a replay that freezes what the operator sees).
- ``fake`` is a synthetic publisher with no simulation dependencies.

Each frame carries two views of the plant:

- ``truth``: the model state, including values no sensor measures (fermenter
  fill, the well-mixed room CO2, the fans' real state).
- ``operator``: what the HMI would show. In the range that is FUXA's data (the
  historian's ``scada`` bucket); here it is the published sensor values, which
  the scenario can freeze to stand in for a replay attack.

Run (from this directory, in a venv with ``../../../sim[power]`` and
``websockets==15.0.1`` installed)::

    python bridge.py --source local --scenario co2-attack --speed 10
    # then open http://127.0.0.1:8090/
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import mimetypes
import resource
import threading
import time
from collections.abc import Sequence
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

if TYPE_CHECKING:
    from cherrymountain_sim.local import LocalFederation

LOG: logging.Logger = logging.getLogger("viz3d.bridge")
HERE: Path = Path(__file__).resolve().parent
WEB: Path = HERE / "web"
TOPOLOGY: Path = HERE.parents[2]  # poc -> viz3d -> spikes -> cherrymountain-brewery
DEFAULT_CONFIG: Path = TOPOLOGY / "plant" / "generated" / "full" / "helics.json"
FERMENTERS: tuple[str, ...] = ("FV101", "FV102", "FV103", "FV104")
BRIGHT_TANKS: tuple[str, ...] = ("BBT201", "BBT202")
PURGE_FANS: tuple[str, ...] = ("bas-vent.purge_fan_1", "bas-vent.purge_fan_2")
SITE_PSIA: float = 12.1  # DESIGN.md §3.1; the fake source uses it for gauge pressure
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")


# -- the frame a browser receives ---------------------------------------------------


class Vessel(BaseModel):
    """One tank as the simulation knows it."""

    id: str
    kind: Literal["fv", "bbt"]
    level_pct: float  # fill; FVs have no level transmitter, so this is truth only
    temp_f: float
    pressure_psig: float
    spund_open: bool = False
    vent_gps: float = 0.0  # CO2 the spunding valve vents into the room
    leak_gps: float = 0.0  # a scenario CO2 release at this vessel


class CellarAir(BaseModel):
    """Cellar room CO2 and ventilation, as the simulation knows them."""

    co2_mixed_ppm: float  # the well-mixed room value; no sensor reads it
    co2_sensors_ppm: list[float]  # what the four low-mounted sensors see
    tiers: list[bool]  # 5,000 / 15,000 / 30,000 ppm, from the sensed value
    purge_fans_running: list[bool]  # the fans' real state (command and EP-1)
    relay_ok: bool  # the bas-vent gas-detection relay into rio-cellar


class Truth(BaseModel):
    """Ground truth: the model state."""

    vessels: list[Vessel]
    air: CellarAir


class OperatorView(BaseModel):
    """What the operator's HMI shows (published values; frozen when spoofed)."""

    co2_sensors_ppm: list[float]
    tiers: list[bool]
    purge_fans_running: list[bool]
    relay_ok: bool
    fv_temp_f: dict[str, float]
    fv_pressure_psig: dict[str, float]
    spoofed: bool = False


class Frame(BaseModel):
    """One state snapshot pushed to every browser."""

    seq: int
    source: str
    sim_t_s: float  # federate seconds since start
    sim_time: str  # simulated local date and time (ISO 8601)
    sampled_wall_ms: float  # wall clock when this state was taken (epoch ms)
    sent_wall_ms: float = 0.0  # wall clock when the bridge sent it (epoch ms)
    speed: float
    truth: Truth
    operator: OperatorView
    notes: list[str] = Field(default_factory=list)


class Source(Protocol):
    """Anything that can produce the latest frame."""

    def frame(self) -> Frame | None:
        """The latest frame, or None before the first one."""
        ...


# -- the scripted scenario for the local source -------------------------------------


class ScenarioStep(BaseModel):
    """One timed action against the offline federation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    at_s: float
    kind: Literal["event", "write", "spoof"]
    federate: str = ""
    payload: dict[str, Any] | list[dict[str, Any]] = Field(default_factory=dict)
    note: str


#: Release at FV-102 (the SCORCH cellar-co2-leak run uses the same event).
LEAK: ScenarioStep = ScenarioStep(
    at_s=60.0,
    kind="event",
    federate="cellar",
    payload={
        "type": "co2-release",
        "params": {"fv": "FV-102", "rate": "high", "duration": 3600},
    },
    note="co2-release FV-102 rate=high (40 g/s)",
)
SCENARIOS: dict[str, tuple[ScenarioStep, ...]] = {
    "none": (),
    "co2-leak": (LEAK,),
    "co2-attack": (
        LEAK,
        # A BACnet WriteProperty from a compromised bas-fuxa: the building
        # federate then follows the written value instead of its local logic.
        ScenarioStep(
            at_s=90.0,
            kind="write",
            federate="building",
            payload=[{"tag": t, "value": False} for t in PURGE_FANS],
            note="BACnet write: purge fans off",
        ),
        # A replay on the supervisory path: the HMI keeps showing these values.
        ScenarioStep(at_s=90.0, kind="spoof", note="operator view frozen (replay)"),
    ),
}


class LocalSource(threading.Thread):
    """The offline harness, paced against the wall clock in its own thread."""

    fed: LocalFederation
    speed: float
    steps: list[ScenarioStep]
    notes: list[str]
    frozen: OperatorView | None
    latest: Frame | None
    lock: threading.Lock
    seq: int
    stop: threading.Event

    def __init__(
        self, config: Path, speed: float, scenario: str, fast_forward_s: float
    ) -> None:
        super().__init__(name="local-federation", daemon=True)
        # The simulation is an optional dependency of this file (the fake
        # source runs without it), so it is imported here.
        from cherrymountain_sim.federate import HelicsConfig  # noqa: PLC0415
        from cherrymountain_sim.local import LocalFederation  # noqa: PLC0415

        self.fed = LocalFederation(HelicsConfig.load(config))
        self.speed = speed
        self.steps = sorted(SCENARIOS[scenario], key=lambda s: s.at_s)
        self.notes = []
        self.frozen = None
        self.latest = None
        self.lock = threading.Lock()
        self.seq = 0
        self.stop = threading.Event()
        self._advance_to(fast_forward_s)
        self._publish()

    def _apply(self, step: ScenarioStep) -> None:
        from cherrymountain_sim.federate import ScenarioEvent  # noqa: PLC0415

        LOG.info("t=%.0f s: %s", self.fed.t, step.note)
        self.notes.append(f"t={self.fed.t:.0f}s {step.note}")
        if step.kind == "event":
            event = ScenarioEvent.model_validate(step.payload)
            self.fed.feds[step.federate].dispatch_event(event)
        elif step.kind == "write":
            applied = self.fed.feds[step.federate].handle_message(
                json.dumps(step.payload)
            )
            if not applied:
                raise RuntimeError(f"{step.federate} rejected {step.payload}")
        else:
            self.frozen = self._operator().model_copy(update={"spoofed": True})

    def _advance_to(self, t_s: float) -> None:
        while self.fed.t < t_s:
            while self.steps and self.steps[0].at_s <= self.fed.t:
                self._apply(self.steps.pop(0))
            self.fed.step(1.0)

    def _truth(self) -> Truth:
        cellar = self.fed.feds["cellar"].cellar  # type: ignore[attr-defined]
        building = self.fed.feds["building"]
        ambient = cellar.env.ambient_psia
        vessels = [
            Vessel(
                id=u,
                kind="fv",
                level_pct=100.0 * fv.liquid_l / fv.cfg.volume_l,
                temp_f=fv.zone_temp_f("side"),
                pressure_psig=fv.pressure_psia - ambient,
                spund_open=fv.spund_open,
                vent_gps=fv.fluxes.co2_vent_gps,
                leak_gps=sum(lk.gps for lk in cellar.leaks if lk.unit == u),
            )
            for u, fv in cellar.fermenters.items()
        ]
        vessels += [
            Vessel(
                id=u,
                kind="bbt",
                level_pct=float(b.read("level_pct")),
                temp_f=float(b.read("temp_f")),
                pressure_psig=float(b.read("pressure_psia")) - ambient,
            )
            for u, b in cellar.bbts.items()
        ]
        cel = building.cel  # type: ignore[attr-defined]
        air = CellarAir(
            co2_mixed_ppm=cel.co2_ppm,
            co2_sensors_ppm=[building.sensor_ppm(i) for i in range(1, 5)],  # type: ignore[attr-defined]
            tiers=list(cel.tiers),
            purge_fans_running=[
                building.on(k) and building.bus("ep1")  # type: ignore[attr-defined]
                for k in PURGE_FANS
            ],
            relay_ok=float(self.fed.values.get("building/vent.co2_relay_ok", 0.0))
            >= 0.5,
        )
        return Truth(vessels=vessels, air=air)

    def _operator(self) -> OperatorView:
        v = self.fed.values
        ambient = self.fed.feds["cellar"].cellar.env.ambient_psia  # type: ignore[attr-defined]
        return OperatorView(
            co2_sensors_ppm=[
                float(v[f"building/bas-vent.co2_{i}_ppm"]) for i in range(1, 5)
            ],
            tiers=[bool(v[f"building/bas-vent.co2_tier_{i}"]) for i in range(1, 4)],
            purge_fans_running=[
                bool(v[f"building/bas-vent.purge_fan_{i}_status"]) for i in (1, 2)
            ],
            relay_ok=bool(v["cellar/gasrelay.co2_ok"]),
            fv_temp_f={
                u: float(v[f"cellar/{u.lower()}-side.temp_f"]) for u in FERMENTERS
            },
            fv_pressure_psig={
                u: float(v[f"cellar/{u.lower()}.pressure_psia"]) - ambient
                for u in FERMENTERS
            },
        )

    def _publish(self) -> None:
        self.seq += 1
        frame = Frame(
            seq=self.seq,
            source="local",
            sim_t_s=self.fed.t,
            sim_time=self.fed.feds["cellar"].now.isoformat(),
            sampled_wall_ms=time.time() * 1000.0,
            speed=self.speed,
            truth=self._truth(),
            operator=self.frozen or self._operator(),
            notes=self.notes[-5:],
        )
        with self.lock:
            self.latest = frame

    def frame(self) -> Frame | None:
        """The latest frame."""
        with self.lock:
            return self.latest

    def run(self) -> None:
        """Step the federation to keep pace with the wall clock."""
        t0_wall = time.monotonic()
        t0_sim = self.fed.t
        while not self.stop.is_set():
            target = t0_sim + (time.monotonic() - t0_wall) * self.speed
            if self.fed.t + 1.0 <= target:
                self._advance_to(math.floor(target))
                self._publish()
            self.stop.wait(0.02)


class FakeSource:
    """A synthetic publisher: sinusoids, no simulation needed."""

    t0: float
    seq: int

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.seq = 0

    def frame(self) -> Frame:
        """A frame computed from the time since start."""
        t = time.monotonic() - self.t0
        self.seq += 1
        ppm = 1500.0 + 14000.0 * (0.5 - 0.5 * math.cos(t / 20.0))
        sensors = [ppm * 1.25 * k for k in (0.97, 1.02, 1.0, 1.04)]
        tiers = [max(sensors) >= x for x in (5000.0, 15000.0, 30000.0)]
        vessels = [
            Vessel(
                id=u,
                kind="fv",
                level_pct=50.0 + 45.0 * math.sin(t / 15.0 + i),
                temp_f=50.0 + 20.0 * math.sin(t / 10.0 + i),
                pressure_psig=7.0 + 6.0 * math.sin(t / 8.0 + i),
                spund_open=math.sin(t / 8.0 + i) > 0.6,
                vent_gps=max(0.0, math.sin(t / 8.0 + i)),
            )
            for i, u in enumerate(FERMENTERS)
        ] + [
            Vessel(id=u, kind="bbt", level_pct=70.0, temp_f=34.0, pressure_psig=12.8)
            for u in BRIGHT_TANKS
        ]
        air = CellarAir(
            co2_mixed_ppm=ppm,
            co2_sensors_ppm=sensors,
            tiers=tiers,
            purge_fans_running=[tiers[0], tiers[0]],
            relay_ok=not tiers[1],
        )
        operator = OperatorView(
            co2_sensors_ppm=sensors,
            tiers=tiers,
            purge_fans_running=air.purge_fans_running,
            relay_ok=air.relay_ok,
            fv_temp_f={v.id: v.temp_f for v in vessels if v.kind == "fv"},
            fv_pressure_psig={v.id: v.pressure_psig for v in vessels if v.kind == "fv"},
        )
        return Frame(
            seq=self.seq,
            source="fake",
            sim_t_s=t,
            sim_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
            sampled_wall_ms=time.time() * 1000.0,
            speed=1.0,
            truth=Truth(vessels=vessels, air=air),
            operator=operator,
        )


# -- the server -------------------------------------------------------------------


class Stats(BaseModel):
    """Counters for /stats.json (the measurement script reads them)."""

    frames_built: int = 0
    messages_sent: int = 0
    bytes_sent: int = 0
    clients: int = 0
    last_frame_bytes: int = 0
    cpu_s: float = 0.0
    max_rss_kib: int = 0


class Hub:
    """Holds the latest frame and wakes each client; slow clients skip frames."""

    text: str
    waiters: set[asyncio.Event]
    stats: Stats

    def __init__(self) -> None:
        self.text = ""
        self.waiters = set()
        self.stats = Stats()

    def publish(self, text: str) -> None:
        """Make ``text`` the latest frame and wake every client."""
        self.text = text
        self.stats.frames_built += 1
        self.stats.last_frame_bytes = len(text.encode())
        for event in self.waiters:
            event.set()

    async def client(self, ws: ServerConnection) -> None:
        """Send each client the latest frame whenever one is ready (read-only)."""
        wake = asyncio.Event()
        self.waiters.add(wake)
        self.stats.clients = len(self.waiters)
        if self.text:
            wake.set()
        drain = asyncio.create_task(self._ignore_input(ws))
        try:
            while True:
                await wake.wait()
                wake.clear()
                text = self.text
                await ws.send(text)
                self.stats.messages_sent += 1
                self.stats.bytes_sent += len(text.encode())
        except ConnectionClosed:
            pass
        finally:
            drain.cancel()
            self.waiters.discard(wake)
            self.stats.clients = len(self.waiters)

    @staticmethod
    async def _ignore_input(ws: ServerConnection) -> None:
        """Read and drop anything a browser sends: the view cannot write."""
        with contextlib.suppress(ConnectionClosed):
            async for _ in ws:
                pass


async def produce(source: Source, hub: Hub, rate_hz: float) -> None:
    """Push the source's latest frame to the hub at ``rate_hz``."""
    last_seq = -1
    while True:
        frame = source.frame()
        if frame is not None and frame.seq != last_seq:
            last_seq = frame.seq
            frame.sent_wall_ms = time.time() * 1000.0
            hub.publish(frame.model_dump_json())
        await asyncio.sleep(1.0 / rate_hz)


def static_response(path: str, hub: Hub) -> Response:
    """Serve a file under web/, /frame.json, or /stats.json."""
    if path == "/frame.json":
        return _response(HTTPStatus.OK, "application/json", hub.text.encode() or b"{}")
    if path == "/stats.json":
        use = resource.getrusage(resource.RUSAGE_SELF)
        hub.stats.cpu_s = use.ru_utime + use.ru_stime
        hub.stats.max_rss_kib = use.ru_maxrss
        return _response(
            HTTPStatus.OK, "application/json", hub.stats.model_dump_json().encode()
        )
    rel = path.split("?", 1)[0].lstrip("/") or "index.html"
    target = (WEB / rel).resolve()
    if not target.is_relative_to(WEB) or not target.is_file():
        return _response(HTTPStatus.NOT_FOUND, "text/plain", b"not found\n")
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return _response(HTTPStatus.OK, ctype, target.read_bytes())


def _response(status: HTTPStatus, ctype: str, body: bytes) -> Response:
    headers = Headers(
        [
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("Connection", "close"),
        ]
    )
    return Response(status.value, status.phrase, headers, body)


async def serve_forever(source: Source, host: str, port: int, rate_hz: float) -> None:
    """Serve web/ and /ws on one port until cancelled."""
    hub = Hub()

    def process_request(_conn: ServerConnection, request: Request) -> Response | None:
        if request.path.split("?", 1)[0] == "/ws":
            return None  # continue with the WebSocket handshake
        return static_response(request.path, hub)

    async with serve(hub.client, host, port, process_request=process_request):
        LOG.info("serving http://%s:%d/ (WebSocket /ws, %.1f Hz)", host, port, rate_hz)
        await produce(source, hub, rate_hz)


def main(argv: Sequence[str] | None = None) -> int:
    """Command line."""
    ap = argparse.ArgumentParser(prog="bridge.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", choices=("local", "fake"), default="local")
    ap.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="plantgen helics.json"
    )
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="none")
    ap.add_argument("--speed", type=float, default=1.0, help="simulated s per wall s")
    ap.add_argument(
        "--fast-forward",
        type=float,
        default=0.0,
        help="simulated s to run before serving",
    )
    ap.add_argument(
        "--rate", type=float, default=2.0, help="frames per second to browsers"
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    if not (WEB / "vendor" / "three.module.js").is_file():
        LOG.warning("web/vendor is empty: run ./fetch_vendor.sh first")
    source: Source
    if args.source == "local":
        local = LocalSource(args.config, args.speed, args.scenario, args.fast_forward)
        local.start()
        source = local
    else:
        source = FakeSource()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve_forever(source, args.host, args.port, args.rate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
