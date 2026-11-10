"""The brewhouse panel's batch request: how a brew day (the schedule, the
``start-batch`` event) reaches the autopilot or the FUXA batch engine, and what
happens to a request nobody takes (DESIGN.md 12.2)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from cherrymountain_sim.brewhouse import (
    PANEL_ACK_WARN_S,
    PANEL_COUNTER_MODULUS,
    BrewhouseFederate,
)
from cherrymountain_sim.federate import FederateConfig, HelicsConfig, build

GENERATED: Path = Path(__file__).resolve().parents[2] / "plant" / "generated"


@pytest.fixture(scope="module")
def full() -> HelicsConfig:
    return HelicsConfig.load(GENERATED / "full" / "helics.json")


def brewhouse(cfg: HelicsConfig, **sim: object) -> BrewhouseFederate:
    """The brewhouse federate, with sim settings overridden."""
    fed_cfg: FederateConfig = cfg.federates["brewhouse"].model_copy(deep=True)
    fed_cfg.sim = {**fed_cfg.sim, **sim}
    patched = cfg.model_copy(update={"federates": {**cfg.federates, "brewhouse": fed_cfg}})
    fed = build(patched, "brewhouse")
    assert isinstance(fed, BrewhouseFederate)
    return fed


def start_batch(fed: BrewhouseFederate, brews: int = 2, batch_id: str = "") -> None:
    event: dict[str, object] = {"type": "start-batch", "params": {"brews": brews, "batch_id": batch_id}}
    assert fed.handle_message(json.dumps({"event": event})) == ["event:start-batch"]


def plc_writes(fed: BrewhouseFederate) -> None:
    """What a running PLC does every cycle: write a command."""
    fed.write("mill.run", False)


def test_panel_points_are_published_and_accepted(full: HelicsConfig) -> None:
    cfg = full.federates["brewhouse"]
    keys = {p.key for p in cfg.publications}
    assert {"brewhouse/panel.requests", "brewhouse/panel.accepted"} <= keys
    assert "panel.accepted" in cfg.accepted_updates and "panel.requests" not in cfg.accepted_updates
    fed = build(full, "brewhouse")
    assert fed.values()["brewhouse/panel.requests"] == 0.0
    assert fed.values()["brewhouse/panel.accepted"] == 0.0


def test_with_no_plc_the_event_goes_to_the_autopilot(full: HelicsConfig) -> None:
    fed = brewhouse(full)
    assert fed.autopilot
    start_batch(fed, 2, "B-1")
    assert fed.pilot.queue == 2 and fed.panel_requests == 0
    assert fed.batch_id == "B-1"
    fed.step(1.0)
    assert fed.values()["brewhouse/panel.requests"] == 0.0


def test_with_the_plc_driving_the_event_presses_the_panel(full: HelicsConfig) -> None:
    fed = brewhouse(full)
    plc_writes(fed)
    assert not fed.autopilot
    start_batch(fed, 2)
    assert fed.pilot.queue == 0, "the autopilot is not handed a brew the engine will run"
    assert fed.panel_requests == 2 and fed.panel_pending == 2
    assert fed.values()["brewhouse/panel.requests"] == 2.0
    start_batch(fed, 1)
    assert fed.values()["brewhouse/panel.requests"] == 3.0
    batch = fed.truth()["batch"]
    assert batch["panel"] == {"requested": 3, "accepted": 0, "pending": 3}


def test_the_engine_accepts_by_echoing_the_count(full: HelicsConfig) -> None:
    fed = brewhouse(full)
    plc_writes(fed)
    start_batch(fed, 2)
    fed.handle_message(json.dumps([{"tag": "panel.accepted", "value": 2}]))  # the PLC forwards it
    assert fed.panel_accepted == 2 and fed.panel_pending == 0
    assert fed.values()["brewhouse/panel.accepted"] == 2.0  # the register shows what was written
    assert fed.truth()["batch"]["panel"]["pending"] == 0


def test_the_counters_wrap_at_16_bits(full: HelicsConfig) -> None:
    fed = brewhouse(full)
    plc_writes(fed)
    fed.panel_requests = PANEL_COUNTER_MODULUS - 1
    start_batch(fed, 3)
    assert fed.panel_requests == 2
    assert fed.values()["brewhouse/panel.requests"] == 2.0
    fed.write("panel.accepted", PANEL_COUNTER_MODULUS - 1)
    assert fed.panel_pending == 3, "requests 2 minus accepted 65535 is three, modulo 65536"


def test_a_request_nobody_accepts_is_logged_and_kept(
    full: HelicsConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """PLC present, FUXA down: the request is not lost and not silent."""
    fed = brewhouse(full)
    plc_writes(fed)
    start_batch(fed, 2)
    caplog.set_level(logging.WARNING, logger="cherrymountain_sim.brewhouse")
    for _ in range(int(PANEL_ACK_WARN_S / 10) - 2):
        plc_writes(fed)
        fed.step(10.0)
    assert not [r for r in caplog.records if "not accepted" in r.getMessage()]
    for _ in range(4):
        plc_writes(fed)
        fed.step(10.0)
    warnings = [r for r in caplog.records if "not accepted" in r.getMessage()]
    assert len(warnings) == 1 and "2 brew request(s)" in warnings[0].getMessage()
    for _ in range(60):
        plc_writes(fed)
        fed.step(10.0)
    assert len([r for r in caplog.records if "not accepted" in r.getMessage()]) == 1, "once"
    assert fed.panel_pending == 2 and fed.pilot.queue == 0
    assert fed.truth()["batch"]["panel"]["pending"] == 2
    # FUXA comes back and accepts; a later request warns again after its own wait
    fed.write("panel.accepted", 2)
    plc_writes(fed)
    fed.step(10.0)
    start_batch(fed, 1)
    caplog.clear()
    for _ in range(int(PANEL_ACK_WARN_S / 10) + 1):
        plc_writes(fed)
        fed.step(10.0)
    assert [r for r in caplog.records if "1 brew request(s)" in r.getMessage()]


def test_a_pending_request_is_not_adopted_by_the_autopilot(full: HelicsConfig) -> None:
    """If the PLC stops, the autopilot drives again but does not take the panel's
    requests: they stay for the engine."""
    fed = brewhouse(full)
    plc_writes(fed)
    start_batch(fed, 2)
    for _ in range(int(fed.HANDOFF_TIMEOUT_S / 10) + 2):
        fed.step(10.0)
    assert fed.autopilot and fed.pilot.queue == 0
    assert fed.panel_pending == 2


def test_the_brew_day_schedule_presses_the_panel_too(full: HelicsConfig) -> None:
    """05:00 on a brew day is the brewer asking for the day's brews: with the PLC
    driving, that is a panel request (the sim clock starts at 04:30 on a Wednesday)."""
    fed = brewhouse(full, brews_per_day=2)
    for _ in range(int(40 * 60 / 10)):
        plc_writes(fed)
        fed.step(10.0)
    assert fed.now.hour == 5 and not fed.autopilot
    assert fed.panel_requests == 2 and fed.pilot.queue == 0
    # and without a PLC the same moment queues the autopilot, as before
    alone = brewhouse(full, brews_per_day=2)
    for _ in range(int(40 * 60 / 10)):
        alone.step(10.0)
    assert alone.pilot.queue >= 1 and alone.panel_requests == 0


def test_the_autopilot_setting_on_keeps_the_event_with_the_autopilot(full: HelicsConfig) -> None:
    fed = brewhouse(full, autopilot="on")
    plc_writes(fed)
    assert fed.autopilot
    start_batch(fed, 1)
    assert fed.pilot.queue == 1 and fed.panel_requests == 0


def test_with_the_autopilot_off_the_request_waits_at_the_panel(full: HelicsConfig) -> None:
    fed = brewhouse(full, autopilot="off")
    start_batch(fed, 1)
    assert not fed.autopilot and fed.panel_requests == 1 and fed.pilot.queue == 0
