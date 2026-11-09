"""Where frames come from.

- :class:`SimctlSource` (the range): polls the sim-ctl event API's
  ``GET /api/v1/state`` on ``127.0.0.1`` and builds a frame whenever the
  snapshot's ``seq`` moves. It never writes to the API. When the API stops
  answering or the snapshot stops advancing, the last frame is re-sent marked
  ``stale``, so the page greys out instead of looking healthy.
- :class:`LocalSource` (offline): runs every federate in-process with the
  offline harness (``cherrymountain_sim.local``, no HELICS), paced against the
  wall clock, and plays a scripted :mod:`~cherrymountain_viz.scenarios`
  scenario. Its frames carry an operator view (the published field values,
  which a scenario can freeze to stand in for a replay attack on the HMI). Used
  for development, the screenshots, and the browser test.
- :class:`StaticSource`: one fixed frame, for tests.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field, ValidationError

from cherrymountain_viz.frame import Frame, OperatorView, Value, build_frame, operator_from_values
from cherrymountain_viz.scenarios import SCENARIOS, Step

if TYPE_CHECKING:
    from cherrymountain_sim.local import LocalFederation

LOG: logging.Logger = logging.getLogger("cherrymountain_viz.sources")
#: A snapshot older than this (wall seconds) is stale: the federate steps every
#: second, so a few missed steps mean it stopped.
STALE_AFTER_S: float = 5.0


class Source(Protocol):
    """Anything that can produce the latest frame."""

    def frame(self) -> Frame | None:
        """The latest frame, or None before the first one."""
        ...


class StateSnapshot(BaseModel):
    """``GET /api/v1/state`` (``cherrymountain_sim.simctl.State``)."""

    seq: int = 0
    t_s: float = 0.0
    sim_time: str | None = None
    wall_time: float = 0.0
    variant: str = ""
    values: dict[str, float | bool | str] = Field(default_factory=dict)


def load_buses(helics_json: Path | None) -> dict[str, str]:
    """Load name -> bus, from the power federate's ``loads`` in plantgen's
    helics.json (the power scene draws each load on its bus)."""
    if helics_json is None or not helics_json.is_file():
        return {}
    doc = json.loads(helics_json.read_text(encoding="utf-8"))
    loads = doc.get("federates", {}).get("power", {}).get("loads", [])
    return {str(ld["name"]): str(ld["bus"]) for ld in loads}


class StaticSource:
    """Always the same frame."""

    fixed: Frame

    def __init__(self, fixed: Frame) -> None:
        self.fixed = fixed

    def frame(self) -> Frame:
        """The fixed frame."""
        return self.fixed


class SimctlSource(threading.Thread):
    """Polls the sim-ctl snapshot in its own thread."""

    url: str
    poll_s: float
    timeout_s: float
    buses: dict[str, str]
    latest: Frame | None
    last_seq: int
    last_change: float
    lock: threading.Lock
    stop: threading.Event

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        *,
        poll_s: float = 0.5,
        timeout_s: float = 2.0,
        buses: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(name="simctl-poll", daemon=True)
        self.url = base_url.rstrip("/") + "/api/v1/state"
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.buses = dict(buses or {})
        self.latest = None
        self.last_seq = -1
        self.last_change = 0.0
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def fetch(self) -> StateSnapshot:
        """One GET of the snapshot."""
        with urllib.request.urlopen(self.url, timeout=self.timeout_s) as resp:  # noqa: S310 - fixed local URL
            return StateSnapshot.model_validate_json(resp.read())

    def poll_once(self) -> None:
        """Fetch, and publish a new frame (or mark the last one stale)."""
        now = time.time()
        try:
            snap = self.fetch()
        except (OSError, urllib.error.URLError, ValidationError) as exc:
            LOG.debug("snapshot fetch failed: %s", exc)
            self._mark_stale()
            return
        if snap.seq != self.last_seq:
            self.last_seq = snap.seq
            self.last_change = now
            frame = build_frame(
                snap.values,
                seq=snap.seq,
                source="sim-ctl",
                variant=snap.variant,
                sim_t_s=snap.t_s,
                sim_time=snap.sim_time,
                sampled_wall_ms=snap.wall_time * 1000.0,
                load_buses=self.buses,
                stale=now - snap.wall_time > STALE_AFTER_S,
                notes=["Operator view: not connected in the range yet (FUXA beside this window)"],
            )
            with self.lock:
                self.latest = frame
        elif now - self.last_change > STALE_AFTER_S:
            self._mark_stale()

    def _mark_stale(self) -> None:
        with self.lock:
            if self.latest is not None and not self.latest.stale:
                self.latest = self.latest.model_copy(update={"stale": True})

    def frame(self) -> Frame | None:
        """The latest frame."""
        with self.lock:
            return self.latest

    def run(self) -> None:
        """Poll until stopped."""
        while not self.stop.is_set():
            self.poll_once()
            self.stop.wait(self.poll_s)


class LocalSource(threading.Thread):
    """The offline harness, paced against the wall clock in its own thread."""

    fed: LocalFederation
    speed: float
    steps: list[Step]
    notes: list[str]
    frozen: OperatorView | None
    buses: dict[str, str]
    latest: Frame | None
    seq: int
    lock: threading.Lock
    stop: threading.Event

    def __init__(
        self,
        config: Path,
        *,
        speed: float = 1.0,
        scenario: str = "none",
        fast_forward_s: float = 0.0,
        scenario_at_s: float = 0.0,
    ) -> None:
        """``scenario_at_s`` shifts the scenario's steps later (an outage at
        midday instead of at the 04:30 start)."""
        super().__init__(name="local-federation", daemon=True)
        # The simulation is an optional dependency: the range's cm-viz reads
        # sim-ctl and does not need it.
        from cherrymountain_sim.federate import HelicsConfig  # noqa: PLC0415
        from cherrymountain_sim.local import LocalFederation  # noqa: PLC0415

        self.fed = LocalFederation(HelicsConfig.load(config))
        self.speed = speed
        self.steps = sorted(
            (s.model_copy(update={"at_s": s.at_s + scenario_at_s}) for s in SCENARIOS[scenario]),
            key=lambda s: s.at_s,
        )
        self.notes = []
        self.frozen = None
        self.buses = load_buses(config)
        self.latest = None
        self.seq = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.advance_to(fast_forward_s)
        self.publish()

    def _apply(self, step: Step) -> None:
        """Apply one scenario step; a repeating step queues its next run and
        is noted only the first time."""
        from cherrymountain_sim.federate import ScenarioEvent  # noqa: PLC0415

        if step.every_s:
            again = step.model_copy(update={"at_s": step.at_s + step.every_s})
            self.steps = sorted([*self.steps, again], key=lambda s: s.at_s)
        if not any(n.endswith(step.note) for n in self.notes):
            LOG.info("t=%.0f s: %s", self.fed.t, step.note)
            self.notes.append(f"t={self.fed.t:.0f}s {step.note}")
        match step.kind:
            case "event":
                event = ScenarioEvent.model_validate(step.payload)
                if not self.fed.feds[step.federate].dispatch_event(event):
                    raise RuntimeError(f"{step.federate} rejected {step.payload}")
            case "write":
                if not self.fed.feds[step.federate].handle_message(json.dumps(step.payload)):
                    raise RuntimeError(f"{step.federate} rejected {step.payload}")
            case "freeze-hmi":
                self.frozen = operator_from_values(self.values(), "HMI (replayed)", frozen=True)

    def values(self) -> dict[str, Value]:
        """Every published value, as the weather federate's snapshot holds them."""
        return dict(self.fed.values)

    def advance_to(self, t_s: float) -> None:
        """Step the federation to ``t_s`` federate seconds, applying due steps."""
        while self.fed.t < t_s:
            while self.steps and self.steps[0].at_s <= self.fed.t:
                self._apply(self.steps.pop(0))
            self.fed.step(1.0)

    def publish(self) -> None:
        """Build the frame for the current state."""
        self.seq += 1
        values = self.values()
        operator = self.frozen or operator_from_values(values, "HMI (field values)")
        weather = self.fed.feds.get("weather")
        frame = build_frame(
            values,
            seq=self.seq,
            source="offline harness",
            variant=self.fed.config.variant,
            sim_t_s=self.fed.t,
            sim_time=weather.clock.at(self.fed.t).isoformat() if weather else None,
            sampled_wall_ms=time.time() * 1000.0,
            load_buses=self.buses,
            operator=operator,
            speed=self.speed,
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
                self.advance_to(math.floor(target))
                self.publish()
            self.stop.wait(0.02)
