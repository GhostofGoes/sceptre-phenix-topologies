"""Building, utilities, weather, brewhouse, ground truth, and the whole federation."""

from __future__ import annotations

import datetime as dt
import http.client
import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from cherrymountain_sim.building import BuildingFederate
from cherrymountain_sim.federate import HelicsConfig, build
from cherrymountain_sim.groundtruth import TruthWriter
from cherrymountain_sim.local import LocalFederation
from cherrymountain_sim.simctl import EventRequest
from cherrymountain_sim.utilities import UtilitiesFederate, ph_of
from cherrymountain_sim.weather import (
    WeatherFederate,
    barometric_correction,
    groundwater_f,
    standard_pressure_kpa,
    synthetic,
)

GENERATED: Path = Path(__file__).resolve().parents[2] / "plant" / "generated"


@pytest.fixture(scope="module")
def full() -> HelicsConfig:
    """The full variant's helics.json."""
    return HelicsConfig.load(GENERATED / "full" / "helics.json")


def test_cellar_co2_steady_state_hand_calc(full: HelicsConfig) -> None:
    """V·dC/dt = G·1e6 + Q·(C_out - C): 1 g/s of CO2 at ρ = 1.51 kg/m³ into
    0.3 m³/s of exhaust and infiltration settles at 420 + 6.62e-4/0.3·1e6 =
    2,627 ppm; the sensors read 1.25x (3,284 ppm), below the 3,500 ppm purge
    stop, so normal venting never cycles tier 1."""
    fed = build(full, "building")
    assert isinstance(fed, BuildingFederate)
    fed.commands["bas-vent.purge_fan_1"] = False  # hold the fans off
    fed.commands["bas-vent.purge_fan_2"] = False
    fed.apply_input("cellar/co2.vent_gps", 1.0)
    for _ in range(int(12 * 3600 / 10)):
        fed.step(10.0)
    assert fed.cel.co2_ppm == pytest.approx(420 + 1e-3 / 1.51 / 0.3 * 1e6, rel=0.01)
    assert fed.values()["building/bas-vent.co2_tier_1"] is False


def test_purge_fans_start_at_tier_1(full: HelicsConfig) -> None:
    """A CO2 release trips tier 1 (5,000 ppm), the purge fans run, and the
    room recovers once the release stops."""
    fed = build(full, "building")
    fed.apply_input("cellar/co2.vent_gps", 40.0)
    for _ in range(60):
        fed.step(10.0)
    vals = fed.values()
    assert vals["building/bas-vent.co2_tier_1"] is True
    assert vals["building/bas-vent.purge_fan_1_status"] is True
    peak = fed.cel.co2_ppm
    fed.apply_input("cellar/co2.vent_gps", 0.0)
    for _ in range(360):
        fed.step(10.0)
    assert fed.cel.co2_ppm < peak / 3  # fans stop at 3,500 ppm sensed
    # a BACnet write overrides the local logic (priority array)
    fed.handle_message(json.dumps([{"tag": "bas-vent.purge_fan_1", "value": True}]))
    fed.step(10.0)
    assert fed.values()["building/bas-vent.purge_fan_1"] is True


def test_co2_relay_trips_at_tier_2_and_on_power_loss(full: HelicsConfig) -> None:
    """The hardwired gas-detection relay to rio-cellar (DESIGN.md §6.6 #6) is
    energized below tier 2 with EP-1 powered, drops at 15,000 ppm, and drops
    when EP-1 dies; the cellar federate republishes it as the rio-cellar
    discrete input that plc-cellar's carbonation interlock reads."""
    bld = build(full, "building")
    assert bld.values()["building/vent.co2_relay_ok"] == 1.0
    bld.apply_input("cellar/co2.vent_gps", 400.0)
    while not bld.cel.tiers[1]:
        bld.step(10.0)
    assert bld.values()["building/vent.co2_relay_ok"] == 0.0
    bld2 = build(full, "building")
    bld2.apply_input("power/bus.ep1.energized", False)
    bld2.step(10.0)
    assert bld2.values()["building/vent.co2_relay_ok"] == 0.0

    cel = build(full, "cellar")
    # De-energize to trip: open until the building federate first reports.
    assert cel.values()["cellar/gasrelay.co2_ok"] is False
    cel.apply_input("building/vent.co2_relay_ok", 1.0)
    assert cel.values()["cellar/gasrelay.co2_ok"] is True
    cel.apply_input("building/vent.co2_relay_ok", 0.0)
    assert cel.values()["cellar/gasrelay.co2_ok"] is False


def test_taproom_and_cold_room_hold_setpoints(full: HelicsConfig) -> None:
    """On a hot afternoon RTU-1 holds the taproom near 74 °F and the cold room
    stays near 36 °F."""
    fed = build(full, "building")
    fed.apply_input("weather/ambient.temp_f", 95.0)
    fed.apply_input("weather/ghi.wm2", 800.0)
    fed.clock = fed.clock.model_copy(update={"start": fed.clock.start.replace(hour=12)})
    for _ in range(int(6 * 3600 / 10)):
        fed.step(10.0)
    vals = fed.values()
    assert 72.0 < vals["building/bas-rtu-taproom.zone_temp_f"] < 77.0
    assert 33.0 < vals["building/bas-coldroom.room_temp_f"] < 39.5
    assert vals["building/load.rtu.kw"] > 1.0


def test_weather_models() -> None:
    """Barometric correction from Golden (1,829 m) to the site (1,585 m), the
    standard site pressure, and a July afternoon in the synthetic model."""
    p = barometric_correction(82.1, 1829.0, 1585.0, 15.0)
    assert p == pytest.approx(82.1 * 1.0298, rel=0.002)
    assert standard_pressure_kpa(1585.0) == pytest.approx(83.7, abs=0.1)
    noon = synthetic(dt.datetime.fromisoformat("2026-07-15T13:00:00-06:00"))
    night = synthetic(dt.datetime.fromisoformat("2026-07-15T03:00:00-06:00"))
    assert noon.ghi_wm2 > 800 and night.ghi_wm2 == 0
    assert 80 < noon.temp_c * 1.8 + 32 < 95 and night.temp_c < noon.temp_c
    assert groundwater_f(dt.datetime(2026, 9, 22)) > groundwater_f(
        dt.datetime(2026, 3, 22)
    )


def test_glycol_holds_and_a_chiller_fault_warms_it(full: HelicsConfig) -> None:
    """Two stages hold 28 °F under a 20 kW load; with one compressor failed the
    supply drifts up past the 34 °F alarm."""
    fed = build(full, "utilities")
    assert isinstance(fed, UtilitiesFederate)
    fed.apply_input("cellar/glycol.load_kw", 20.0)
    fed.apply_input("weather/ambient.temp_f", 95.0)
    for _ in range(3600):
        fed.step(1.0)
    assert fed.output_value("glycol.supply_f") == pytest.approx(28.0, abs=2.0)
    fed.apply_input("cellar/glycol.load_kw", 40.0)
    fed.handle_message(
        json.dumps({"event": {"type": "chiller-fault", "params": {"compressor": 2}}})
    )
    for _ in range(3 * 3600):
        fed.step(1.0)
    assert fed.output_value("glycol.supply_f") > 34.0
    assert fed.values()["utilities/chiller.fault"] is True


def test_wastewater_is_neutralized(full: HelicsConfig) -> None:
    """A caustic dump raises the pH; the dosing brings it back into 6-9 and the
    discharge runs."""
    fed = build(full, "utilities")
    fed.wwn.base_mol = 0.05 * fed.wwn.liters  # 0.05 M NaOH
    assert ph_of(0.05) > 11
    for _ in range(2 * 3600):
        fed.step(1.0)
    assert 6.0 <= fed.ph <= 9.0
    assert fed.wwn.discharged_l > 0
    assert ph_of(0.0) == pytest.approx(7.0)


def test_brewhouse_brew_day(full: HelicsConfig) -> None:
    """The autopilot runs a double brew: knockout at 64 °F, steam within the
    200 kW boiler, two brews completed by mid-afternoon."""
    fed = build(full, "brewhouse")
    knockout: list[float] = []
    steam: list[float] = []
    for _ in range(10 * 3600):
        fed.step(1.0)
        steam.append(float(fed.output_value("steam.demand_kw")))
        if fed.output_value("knockout.gpm") > 0:
            knockout.append(float(fed.values()["brewhouse/knockout.wort_out_f"]))
    assert fed.output_value("batch.completed") == 2.0
    assert max(steam) <= 200.0
    tail = knockout[len(knockout) // 4 :]
    assert sum(tail) / len(tail) == pytest.approx(64.0, abs=1.0)


def test_truth_documents(full: HelicsConfig) -> None:
    """Ground truth: one bulk index line plus one document per record."""
    fed = build(full, "cellar")
    writer = TruthWriter(None, "cellar", "full")
    index, doc = writer.document(fed)
    assert index.startswith("cherrymountain-truth-cellar-2026.07.15")
    assert doc["federate"] == "cellar" and "fermenters" in doc
    body = TruthWriter.bulk_body([(index, doc)]).decode().splitlines()
    assert json.loads(body[0]) == {"index": {"_index": index}}


def test_event_durations_accept_sane_values() -> None:
    """Durations the federates can use pass: the defaults (absent), a day, and a
    zero weather-step (hold the step until the next one)."""
    good: list[dict[str, object]] = [
        {"event": "co2-release", "fv": "FV-102", "duration": 30},
        {"event": "jam", "duration": 86400},
        {"event": "jam", "machine": "filler", "duration": 12.5},
        {"event": "weather-step", "delta_f": -20, "duration": 0},
        {"event": "weather-step", "delta_f": 5.5, "duration": 3600},
    ]
    for body in good:
        EventRequest.model_validate(body)


def test_a_bad_event_or_message_does_not_stop_the_federate(
    full: HelicsConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """An event whose parameters the federate cannot use, a malformed event, and
    a bad write are logged and dropped; the federate keeps stepping."""
    fed = build(full, "cellar")
    fed.step(1.0)
    t0 = fed.t
    bad_payloads: list[str] = [
        json.dumps({"event": {"type": "co2-release", "params": {"fv": "FV-102", "duration": "ten"}}}),
        json.dumps({"event": 5}),
        json.dumps({"event": {"params": {}}}),
        json.dumps([5, "x", None]),
        json.dumps([{"tag": "fv101.spund_sp_psia", "value": "abc"}]),
        json.dumps(7),
    ]
    with caplog.at_level("WARNING"):
        for payload in bad_payloads:
            fed.handle_message(payload)
    assert not fed.events
    fed.step(1.0)
    fed.step(1.0)
    assert fed.t == t0 + 2.0
    # A good event after the bad ones still works.
    good = json.dumps(
        {"event": {"type": "co2-release", "params": {"fv": "FV-102", "duration": 30}}}
    )
    assert fed.handle_message(good) == ["event:co2-release"]
    assert len(fed.events) == 1


def test_event_api_contract(full: HelicsConfig) -> None:
    """GET /api/v1/health, POST /api/v1/events and /events, GET /api/v1/kpis,
    and 400 for a bad event (apps/cherrymountain_apps/simctl.py contract)."""
    fed = build(full, "weather")
    assert isinstance(fed, WeatherFederate)
    fed.settings = fed.settings.model_copy(update={"event_api_port": 0})
    port = fed.start_api("full")
    base = f"http://127.0.0.1:{port}"
    try:
        fed.step(1.0)

        def call(
            path: str, body: dict[str, object] | None = None
        ) -> tuple[int, dict[str, object]]:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(
                base + path, data=data, method="POST" if data else "GET"
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

        assert call("/api/v1/health")[1]["status"] == "ok"
        code, reply = call(
            "/api/v1/events", {"event": "co2-release", "fv": "FV-102", "rate": "high"}
        )
        assert code == 202 and reply["target"] == "cellar"
        assert call("/events", {"event": "weather-step", "delta_f": 15})[0] == 202
        assert call("/api/v1/events", {"event": "utility-breaker"})[0] == 400
        assert call("/api/v1/events", {"event": "meteor"})[0] == 400
        bad_params: list[dict[str, object]] = [
            {"event": "chiller-fault", "compressor": 3},
            {"event": "start-batch", "recipe": "pilsner"},
            {"event": "start-batch", "brews": 0},
            {"event": "start-batch", "brews": "two"},
            {"event": "co2-release", "fv": "FV-102", "duration": 0},
            {"event": "co2-release", "fv": "FV-102", "duration": "ten"},
            {"event": "co2-release", "fv": "FV-102", "duration": True},
            {"event": "co2-release", "fv": "FV-102", "duration": 86401},
            {"event": "jam", "machine": "filler", "duration": -5},
            {"event": "jam", "duration": None},
            {"event": "jam", "duration": float("nan")},
            {"event": "jam", "duration": 1e9},
            {"event": "weather-step", "delta_f": 10, "duration": -1},
            {"event": "weather-step", "delta_f": 10, "duration": False},
            {"event": "weather-step", "delta_f": 10, "duration": "1h"},
            {"event": "weather-step", "delta_f": True},
        ]
        for bad in bad_params:
            assert call("/api/v1/events", bad)[0] == 400, bad
        fed.step(1.0)
        assert fed.outbox and fed.outbox[0][0] == "cellar/updates"
        assert fed.step_f == 15.0
        kpis = call("/api/v1/kpis")[1]
        assert set(kpis) >= {
            "batches_completed",
            "bottles_packaged",
            "energy_kwh",
            "peak_demand_kw",
        }
        assert len(call("/api/v1/events")[1]["events"]) == 2
        for bad in ("abc", "-1"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.putrequest("POST", "/api/v1/events")
            conn.putheader("Content-Length", bad)
            conn.endheaders()
            assert conn.getresponse().status == 400
            conn.close()
    finally:
        assert fed.api is not None
        fed.api.close()


def test_whole_federation_offline(full: HelicsConfig) -> None:
    """All seven federates for 30 minutes with the real pub/sub routing: every
    subscription receives a value, an event through sim-ctl reaches the cellar
    and raises the room CO2, and a utility outage puts EP-1 on the genset."""
    lf = LocalFederation(full)
    lf.run(600)
    for name, cfg in full.federates.items():
        for sub in cfg.subscriptions:
            assert sub.key in lf.values, (name, sub.key)
    weather = lf.feds["weather"]
    assert isinstance(weather, WeatherFederate)
    weather.outbox.append(
        (
            "cellar/updates",
            json.dumps({"event": {"type": "co2-release", "params": {"fv": "FV-102"}}}),
        )
    )
    weather.outbox.append(
        (
            "power/updates",
            json.dumps(
                {"event": {"type": "utility-breaker", "params": {"action": "open"}}}
            ),
        )
    )
    co2_before = lf.values["building/cellar.co2_ppm"]
    lf.run(600)
    assert lf.values["building/cellar.co2_ppm"] > co2_before + 2000
    assert lf.values["power/bus.ep1.energized"] is True
    assert lf.values["power/bus.boiler.energized"] is False
    assert lf.values["power/gen-ats.on_emergency"] is True


def test_autopilot_takes_back_control_when_writes_stop(full: HelicsConfig) -> None:
    """The brewhouse hands over on a PLC write and takes control back after
    HANDOFF_TIMEOUT_S federate seconds without another write."""
    fed = build(full, "brewhouse")
    assert fed.autopilot
    tag = next(iter(fed.cfg.accepted_updates))
    fed.handle_message(json.dumps([{"tag": tag, "value": 0}]))
    assert not fed.autopilot
    for _ in range(int(fed.HANDOFF_TIMEOUT_S / 10) - 1):
        fed.step(10.0)
    assert not fed.autopilot  # still inside the window
    fed.step(20.0)
    assert fed.autopilot


def test_cellar_autopilot_runs_until_a_plc_writes(full: HelicsConfig) -> None:
    """Without a PLC the cellar federate's operator holds the fermenters and
    bright tanks (DESIGN.md 9.6); the first write hands the cellar to the PLC,
    and carbonation stops while the CO2 relay is open (§6.7)."""
    from cherrymountain_sim.federate import CellarFederate

    cel = build(full, "cellar")
    assert isinstance(cel, CellarFederate)
    assert cel.autopilot
    cel.apply_input("building/vent.co2_relay_ok", 1.0)
    fv104 = cel.cellar.fermenters["FV104"]  # cold crashing at the start
    start_f = fv104.zone_temp_f("side")
    carbonating = [u for u, b in cel.cellar.bbts.items() if float(b.read("co2_vol")) < 2.5]
    stone_ran: set[str] = set()
    for _ in range(600):
        cel.step(1.0)
        stone_ran |= {
            u for u in carbonating if float(cel.cellar.bbts[u].read("co2_valve_pct")) > 0
        }
    assert fv104.valve("side") and fv104.valve("cone")  # cooling toward 34 °F
    assert fv104.zone_temp_f("side") < start_f
    bbt = next(iter(cel.cellar.bbts.values()))
    assert 32.0 <= float(bbt.read("temp_f")) <= 37.0
    # The stone runs, cycling against the head-pressure limit, and the relief
    # valve never lifts in normal carbonation.
    assert carbonating and stone_ran == set(carbonating)
    assert all(b.psv.lifts == 0 for b in cel.cellar.bbts.values())
    cel.apply_input("building/vent.co2_relay_ok", 0.0)
    cel.step(1.0)
    assert all(float(b.read("co2_valve_pct")) == 0.0 for b in cel.cellar.bbts.values())
    cel.write("fv101-side.valve", True)  # a PLC write: hands over
    assert not cel.autopilot


def test_state_snapshot_and_kpis_come_from_the_subscriptions(full: HelicsConfig) -> None:
    """The weather federate subscribes to the snapshot keys (plant.yaml
    ``snapshot``): GET /api/v1/state serves the federation's values, and the
    KPIs, which were zeros while it subscribed to nothing, are real."""
    weather_cfg = full.federates["weather"]
    subs = {s.key for s in weather_cfg.subscriptions}
    assert {
        "building/cellar.co2_ppm",
        "cellar/fv102.level_pct",
        "cellar/bbt201-psv.lifted",
        "power/bess-pcs.soc_pct",
        "power/energy.kwh",
        "packaging/bottles.total",
        "utilities/cip.verified",
    } <= subs
    assert not any(k.startswith("weather/") for k in subs)

    lf = LocalFederation(full)
    weather = lf.feds["weather"]
    assert isinstance(weather, WeatherFederate)
    weather.settings = weather.settings.model_copy(update={"event_api_port": 0})
    port = weather.start_api("full")
    try:
        lf.run(900)

        def get(path: str) -> dict[str, Any]:
            url = f"http://127.0.0.1:{port}{path}"
            with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
                doc: dict[str, Any] = json.loads(resp.read())
                return doc

        state = get("/api/v1/state")
        values = state["values"]
        assert state["variant"] == "full" and state["seq"] == 900
        assert state["sim_time"] == weather.clock.at(899.0).isoformat()
        assert subs <= set(values)
        assert "weather/ambient.temp_f" in values  # its own outputs too
        assert values["building/cellar.co2_ppm"] == lf.values["building/cellar.co2_ppm"]
        kpis = get("/api/v1/kpis")
        assert kpis["energy_kwh"] > 0 and kpis["demand_kw"] > 0
        assert {"ttb_bbl_packaged", "cip_runs_verified", "genset_test_hours_year"} <= set(kpis)
    finally:
        assert weather.api is not None
        weather.api.close()
