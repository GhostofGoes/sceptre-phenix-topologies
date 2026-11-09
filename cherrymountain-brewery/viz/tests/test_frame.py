"""Frames from snapshots: the scene model and the findings."""

from __future__ import annotations

from pathlib import Path

import pytest

from cherrymountain_viz.frame import Frame, Value, build_frame, operator_from_values
from cherrymountain_viz.sources import load_buses

HELICS_FULL: Path = Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"
KPA: float = 12.1 / 0.1450377  # site ambient as the weather federate publishes it


def snapshot(**over: Value) -> dict[str, Value]:
    """A small but complete snapshot: two vessels, the cellar air, power."""
    v: dict[str, Value] = {
        "weather/pressure.kpa": KPA,
        "weather/ghi.wm2": 640.0,
        "cellar/fv101.pressure_psia": 13.1,
        "cellar/fv101.spund_sp_psia": 13.1,
        "cellar/fv101.spund_open": True,
        "cellar/fv101.gravity_plato": 10.2,
        "cellar/fv101-side.temp_f": 66.2,
        "cellar/fv101-cone.temp_f": 65.9,
        "cellar/fv101-side.valve": True,
        "cellar/fv101-cone.valve": False,
        "cellar/fv101.level_pct": 100.0,
        "cellar/fv101.spund_gps": 0.6,
        "cellar/fv101.psv_gps": 0.0,
        "cellar/fv101.leak_gps": 0.0,
        "cellar/fv101-psv.lifted": False,
        "cellar/bbt201.pressure_psia": 24.9,
        "cellar/bbt201.temp_f": 34.0,
        "cellar/bbt201.level_pct": 90.0,
        "cellar/bbt201.co2_vol": 2.4,
        "cellar/bbt201.valve": False,
        "cellar/bbt201.co2_valve_pct": 0.0,
        "cellar/bbt201.psv_gps": 0.0,
        "cellar/bbt201.leak_gps": 0.0,
        "cellar/bbt201-psv.lifted": False,
        "cellar/gasrelay.co2_ok": True,
        "cellar/co2.vent_gps": 0.6,
        "cellar/clock.sim_hours": 50.0,
        "building/cellar.co2_ppm": 1800.0,
        "building/cellar.temp_f": 66.0,
        "building/vent.co2_relay_ok": 1.0,
        **{f"building/bas-vent.co2_{i}_ppm": 2250.0 for i in range(1, 5)},
        **{f"building/bas-vent.co2_tier_{i}": False for i in range(1, 4)},
        **{f"building/bas-vent.purge_fan_{i}": False for i in (1, 2)},
        **{f"building/bas-vent.purge_fan_{i}_status": False for i in (1, 2)},
        "power/gen-ats.utility_ok": True,
        "power/relay-main.breaker_closed": True,
        "power/relay-main.tripped": False,
        "power/grid.kw": 120.0,
        "power/pv-inverter.p_kw": 40.0,
        "power/bess-pcs.p_kw": -20.0,
        "power/bess-pcs.soc_pct": 60.0,
        "power/gen-ats.gen_running": False,
        "power/gen-ats.on_emergency": False,
        "power/bus.msb.energized": True,
        "power/bus.ep1.energized": True,
        "power/bus.boiler.energized": True,
        "utilities/load.boiler.kw": 80.0,
        "building/load.fans.kw": 1.0,
        "power/tariff.onpeak": False,
        "power/tariff.rate_usd_kwh": 0.099,
    }
    v.update(over)
    return v


def frame(values: dict[str, Value]) -> Frame:
    return build_frame(
        values,
        seq=1,
        source="test",
        sim_t_s=10.0,
        sim_time="2026-07-15T12:00:00-06:00",
        sampled_wall_ms=0.0,
        load_buses=load_buses(HELICS_FULL),
    )


def test_normal_running_has_no_findings() -> None:
    f = frame(snapshot())
    fv, bbt = f.cellar.vessels
    assert (fv.id, fv.label, fv.kind, bbt.id) == ("FV101", "FV-101", "fv", "BBT201")
    assert fv.pressure_psig == pytest.approx(1.0)
    assert bbt.pressure_psig == pytest.approx(12.8)
    assert fv.glycol_valves == [True, False] and fv.spund_open and not fv.level_measured
    assert bbt.level_measured and bbt.co2_vol == 2.4
    assert f.cellar.air.sensors_ppm == [2250.0] * 4 and f.cellar.air.tiers == [False] * 3
    assert f.cellar.air.inhibit_effective is None  # the relay is closed
    assert f.cellar.kinetic_hours == 50.0
    assert f.power.loads and {ld.name: ld.bus for ld in f.power.loads}["boiler"] == "boiler"
    assert f.power.load_kw == pytest.approx(81.0)
    assert f.findings == []


def test_fans_overridden_during_a_release_are_flagged() -> None:
    """The co2-attack case: tier 1 active, fans commanded off by a write."""
    f = frame(
        snapshot(
            **{"building/bas-vent.co2_tier_1": True, "cellar/fv101.leak_gps": 40.0},
        )
    )
    texts = [x.text for x in f.findings]
    assert sum("commanded off while CO2 tier 1" in t for t in texts) == 2
    assert any("CO2 release at FV-101" in t for t in texts)


def test_relief_lift_overpressure_and_defeated_inhibit() -> None:
    f = frame(
        snapshot(
            **{
                "cellar/fv101.spund_sp_psia": 12.1 + 30.0,
                "cellar/fv101-psv.lifted": True,
                "cellar/fv101.psv_gps": 25.0,
                "building/vent.co2_relay_ok": 0.0,
                "cellar/bbt201.co2_valve_pct": 40.0,
            }
        )
    )
    assert f.cellar.vessels[0].psv_lifted
    assert f.cellar.air.inhibit_effective is False
    texts = " ".join(x.text for x in f.findings)
    assert "FV-101 relief valve lifted" in texts
    assert "above the 15 psig MAWP" in texts
    assert "inhibit is defeated" in texts


def test_outage_and_operator_view() -> None:
    f = frame(
        snapshot(
            **{
                "power/gen-ats.utility_ok": False,
                "power/gen-ats.on_emergency": True,
                "power/gen-ats.gen_running": True,
                "power/bus.boiler.energized": False,
            }
        )
    )
    assert f.power.ats_on_emergency and f.power.gen_running
    assert f.power.load_kw == pytest.approx(1.0)  # the boiler's bus is dead
    assert {x.scene for x in f.findings} == {"power"}
    op = operator_from_values(snapshot(), "test", frozen=True)
    assert op.frozen and op.co2_sensors_ppm == [2250.0] * 4
    assert op.vessel_pressure_psig["BBT201"] == pytest.approx(12.8)
    assert op.vessel_temp_f == {"FV101": 66.2, "BBT201": 34.0}


def test_an_empty_snapshot_still_builds() -> None:
    f = build_frame({}, seq=0, source="t", sim_t_s=0, sim_time=None, sampled_wall_ms=0)
    assert f.cellar.vessels == [] and f.power.loads == [] and f.findings == []
