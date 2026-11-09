"""Building, utilities, weather, brewhouse, ground truth, and the whole federation."""

from __future__ import annotations

import datetime as dt
import http.client
import json
import urllib.request
from pathlib import Path

import pytest

from cherrymountain_sim.building import BuildingFederate
from cherrymountain_sim.federate import HelicsConfig, build
from cherrymountain_sim.groundtruth import TruthWriter
from cherrymountain_sim.local import LocalFederation
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
