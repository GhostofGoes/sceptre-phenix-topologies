"""Power flow (ANSI C84.1 Range A), outage ride-through, and the SV-SGT tariff (§17)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from cherrymountain_sim.federate import HelicsConfig, SimClock, build
from cherrymountain_sim.power import RANGE_A, ClockRegisters, PowerFederate
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


# -- the ATS controller clock (DESIGN.md §7.3, §9.4) --------------------------------------

MDT: dt.timezone = dt.timezone(dt.timedelta(hours=-6))
CLOCK_KEYS: tuple[str, str, str] = (
    "power/gen-ats.clock_min",
    "power/gen-ats.clock_day",
    "power/gen-ats.clock_month",
)


def _clocked(start: str, speedup: float = 1.0) -> PowerFederate:
    """A power federate whose simulation clock starts at ``start`` (ISO, with offset)."""
    cfg = HelicsConfig.load(FULL).federates["power"]
    clock = SimClock(start=dt.datetime.fromisoformat(start), speedup=speedup)
    return PowerFederate(cfg, clock)


def _registers(fed: PowerFederate) -> tuple[int, int, int]:
    """The clock registers a reader of gen-ats sees: minute, day, month."""
    values = fed.values()
    minute, day, month = (values[k] for k in CLOCK_KEYS)
    assert isinstance(minute, int) and isinstance(day, int) and isinstance(month, int)
    return minute, day, month


def test_clock_registers_at_the_default_start(power: PowerFederate) -> None:
    """The design's default clock is Wednesday 2026-07-15 04:30 local: minute 270,
    day 20649 (days since 1970-01-01), month 7, and the weekday from the day is
    Wednesday (3, with Sunday 0)."""
    assert _registers(power) == (270, 20649, 7)
    assert power.clock_registers.weekday == 3
    cfg = HelicsConfig.load(FULL).federates["power"]
    published = {p.key: p.tag for p in cfg.publications if p.key in CLOCK_KEYS}
    assert published == {
        "power/gen-ats.clock_min": "PWR.GEN.KI01.PV",
        "power/gen-ats.clock_day": "PWR.GEN.KI02.PV",
        "power/gen-ats.clock_month": "PWR.GEN.KI03.PV",
    }


@pytest.mark.parametrize(
    ("start", "seconds", "before", "after"),
    [
        # a day boundary: 23:59 on Wednesday to 00:00 on Thursday
        ("2026-07-15T23:59:00-06:00", 60, (1439, 20649, 7, 3), (0, 20650, 7, 4)),
        # a week boundary: Sunday 23:59 to Monday 00:00
        ("2026-07-19T23:59:00-06:00", 60, (1439, 20653, 7, 0), (0, 20654, 7, 1)),
        # a month boundary
        ("2026-07-31T23:59:00-06:00", 60, (1439, 20665, 7, 5), (0, 20666, 8, 6)),
        # a year boundary: the month register goes 12 -> 1 and the date keeps counting
        ("2026-12-31T23:59:00-07:00", 60, (1439, 20818, 12, 4), (0, 20819, 1, 5)),
        # a leap day, then March
        ("2028-02-28T23:59:00-07:00", 60, (1439, 21242, 2, 1), (0, 21243, 2, 2)),
        ("2028-02-29T23:59:00-07:00", 60, (1439, 21243, 2, 2), (0, 21244, 3, 3)),
    ],
)
def test_clock_registers_across_a_boundary(
    start: str,
    seconds: int,
    before: tuple[int, int, int, int],
    after: tuple[int, int, int, int],
) -> None:
    """Each boundary the schedule cares about: the minute wraps to 0, the date
    counts on, the month register changes, and the weekday follows the date."""
    fed = _clocked(start)
    assert (*_registers(fed), fed.clock_registers.weekday) == before
    for _ in range(seconds):
        fed.step(1.0)
    assert (*_registers(fed), fed.clock_registers.weekday) == after


def test_clock_registers_use_the_local_offset_of_the_start() -> None:
    """``clock.start`` carries a fixed UTC offset, so the registers are local civil
    time in that offset with no daylight-saving shift: 2026-11-02 is after the
    real Denver fall-back, and the clock still runs on the start's -06:00."""
    fed = _clocked("2026-11-01T22:00:00-06:00")
    assert _registers(fed) == (22 * 60, 20758, 11)
    for _ in range(3 * 3600):
        fed.step(1.0)
    assert _registers(fed) == (60, 20759, 11)  # 01:00, 2026-11-02, not 00:00 or 02:00


@pytest.mark.parametrize("speedup", [1.0, 10.0, 60.0, 600.0])
def test_clock_registers_under_time_compression(speedup: float) -> None:
    """``clock.speedup`` multiplies the calendar, not the physics: one federate
    second advances the registers by ``speedup`` seconds, a minute of calendar
    takes ``60 / speedup`` seconds, and the day register rolls with the minute."""
    fed = _clocked("2026-07-15T23:30:00-06:00", speedup)
    assert _registers(fed)[0] == 23 * 60 + 30
    minutes_seen: list[int] = []
    seconds = int(1800 / speedup)  # 30 simulated minutes
    for _ in range(seconds):
        fed.step(1.0)
        minutes_seen.append(_registers(fed)[0])
    assert _registers(fed) == (0, 20650, 7)
    # Monotonic until the midnight wrap on the last step, and never more than
    # ceil(speedup / 60) minutes per step (the PLC accepts up to 60 minutes
    # between two readings, so it follows a clock up to 3,600 times real time).
    before_wrap = minutes_seen[:-1]
    assert minutes_seen[-1] == 0 and before_wrap == sorted(before_wrap)
    assert max(b - a for a, b in zip(before_wrap, before_wrap[1:])) <= -(-speedup // 60)
    assert fed.now == fed.clock.at(fed.t)


def test_clock_registers_agree_with_the_tariff_calendar() -> None:
    """The window the PLC derives from the registers (weekday 1 to 5, minute 1020
    to 1259) is the window the meter bills (``Tariff.onpeak``), for every minute
    of a leap year; the registers always fit 16 bits, to the last day (2149-06-06)."""
    tariff = Tariff()
    when = dt.datetime(2028, 1, 1, tzinfo=MDT)
    for _ in range(366 * 1440):
        regs = ClockRegisters.of(when)
        derived = 1 <= regs.weekday <= 5 and 1020 <= regs.minute < 1260
        assert derived == tariff.onpeak(when), when
        assert regs.weekday == (when.weekday() + 1) % 7
        assert regs.month == when.month
        when += dt.timedelta(minutes=1)
    last = ClockRegisters.of(dt.datetime(2149, 6, 6, 23, 59, tzinfo=MDT))
    assert (last.minute, last.day) == (1439, 65535)
