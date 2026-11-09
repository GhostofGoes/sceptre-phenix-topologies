"""Power flow (ANSI C84.1 Range A), outage ride-through, and the SV-SGT tariff (§17)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from cherrymountain_sim.federate import HelicsConfig, build
from cherrymountain_sim.power import RANGE_A, PowerFederate
from cherrymountain_sim.tariff import Meter, Tariff

FULL: Path = (
    Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"
)


@pytest.fixture
def power() -> PowerFederate:
    """The power federate of the full variant."""
    pytest.importorskip("opendssdirect")
    fed = build(HelicsConfig.load(FULL), "power")
    assert isinstance(fed, PowerFederate)
    return fed


def _load(fed: PowerFederate, fraction: float) -> None:
    for ld in fed.cfg.loads:
        fed.apply_input(ld.key, ld.rated_kw * fraction)


@pytest.mark.parametrize("fraction", [0.0, 0.5, 1.0])
def test_voltages_within_range_a(power: PowerFederate, fraction: float) -> None:
    """MSB, MCC-1, LP-1 (208 V), and EP-1 stay within ±5 % from no load to the
    329 kW nameplate (no PV, no battery)."""
    power.settings = power.settings.model_copy(update={"demand_target_kw": 1e6})
    power.grid.soc_pct = 50.0
    _load(power, fraction)
    power.auto["bess-pcs.enable"] = False
    for _ in range(3):
        power.step(1.0)
    flow = power.flow_model.solve(
        power.energized,
        {
            "utility": True,
            "main": True,
            "fdr_boiler": True,
            "fdr_pkg": True,
            "fdr_rtu": True,
            "ats_normal": True,
            "ats_emergency": False,
        },
        {ld.name: ld.rated_kw * fraction for ld in power.cfg.loads},
        0.0,
        0.0,
        False,
    )
    for bus in ("msb", "mcc1", "lp1", "ep1"):
        assert RANGE_A[0] <= flow.v_pu[bus] <= RANGE_A[1], (bus, flow.v_pu[bus])
    assert flow.p_kw == pytest.approx(329.0 * fraction, rel=0.03, abs=0.5)


def test_bess_shaves_the_peak(power: PowerFederate) -> None:
    """With 329 kW of load and no sun the battery holds import near 240 kW."""
    power.grid.soc_pct = 80.0
    _load(power, 1.0)
    for _ in range(20):
        power.step(1.0)
    assert power.grid.bess_kw == pytest.approx(89.0, abs=2.0)
    assert power.grid.p_kw == pytest.approx(240.0, abs=3.0)


def test_utility_outage_and_return(power: PowerFederate) -> None:
    """§7.3: the MSB drops, PV and BESS trip, the genset carries EP-1 within
    about 10 s, and the ATS retransfers after the stable-source timer."""
    _load(power, 0.5)
    power.apply_input("weather/ghi.wm2", 800.0)
    for _ in range(5):
        power.step(1.0)
    assert power.grid.pv_kw > 0
    power.handle_message(
        json.dumps({"event": {"type": "utility-breaker", "params": {"action": "open"}}})
    )
    power.step(1.0)
    assert (
        not power.energized["msb"] and power.grid.pv_kw == 0 and power.grid.bess_kw == 0
    )
    for _ in range(12):
        power.step(1.0)
    assert (
        power.grid.on_emergency
        and power.energized["ep1"]
        and not power.energized["boiler"]
    )
    assert power.values()["power/bus.ep1.energized"] is True
    power.handle_message(
        json.dumps(
            {"event": {"type": "utility-breaker", "params": {"action": "close"}}}
        )
    )
    for _ in range(int(power.settings.retransfer_s) + 5):
        power.step(1.0)
    assert not power.grid.on_emergency and power.energized["msb"]
    assert not power.grid.pv_connected  # IEEE 1547 return-to-service delay


def test_tariff_numbers_from_the_design() -> None:
    """§7.4: each 50 kW of shaved peak saves about $19k/yr; summer demand is
    $36.80/kW-month; on-peak is weekdays 17:00-21:00."""
    t = Tariff()
    assert 50 * t.annual_demand_rate() == pytest.approx(19140.0)
    assert t.demand_rate(7) == pytest.approx(36.80)
    wed = dt.datetime(2026, 7, 15, 18, 0)
    assert t.onpeak(wed) and t.energy_rate(wed) == pytest.approx(0.38375)
    assert not t.onpeak(wed.replace(day=18))  # Saturday
    assert t.energy_rate(dt.datetime(2026, 1, 14, 18)) == pytest.approx(0.23371)


def test_meter_demand_and_bill() -> None:
    """A month at a flat 200 kW: 15-minute demand 200 kW; the bill is the fixed
    charge plus TOU energy plus 200 kW x $36.80."""
    m = Meter()
    start = dt.datetime(2026, 7, 1)
    for i in range(0, 30 * 86400, 60):
        m.add(i, start + dt.timedelta(seconds=i), 200.0, 60.0)
    assert m.month_peak_kw == pytest.approx(200.0)
    assert m.month_kwh == pytest.approx(200.0 * 720)
    onpeak_hours = 4 * 22  # 22 weekdays in July 1-30, 2026
    energy = 200 * (onpeak_hours * 0.38375 + (720 - onpeak_hours) * 0.09927)
    assert m.month_to_date_usd == pytest.approx(91.79 + energy + 200 * 36.80, rel=1e-6)


def test_an_explicit_zero_kw_utility_dispatch_holds_the_bess(power: PowerFederate) -> None:
    """A utility dispatch of 0 kW is a command, not "no command": it holds the
    BESS at 0 while the site would discharge, and the site dispatch resumes once
    the hold (utility_dispatch_hold_s) passes without another utility write.
    Before any utility write the site dispatch runs."""
    _load(power, 1.0)
    for _ in range(5):
        power.step(1.0)
    assert power.grid.bess_kw > 10.0  # peak shaving, no utility command yet
    power.write("bess-pcs.utility_p_sp_kw", 0.0)
    for _ in range(5):
        power.step(1.0)
    assert power.grid.bess_kw == 0.0
    power.settings.utility_dispatch_hold_s = 10.0
    for _ in range(15):
        power.step(1.0)
    assert power.grid.bess_kw > 10.0
