"""plc-brewhouse's mash-in, mash step, and deviation alarm, in the program itself.

``plc/plc-brewhouse/plc-brewhouse.st`` is compiled by strucpp and g++
(``st_engine.py``) and scanned here with the mash tun's temperature set by
hand. The tests skip where the toolchain is not installed.
"""

from __future__ import annotations

import pytest
import st_engine

SCAN_NS: int = 100_000_000  # the task interval: 100 ms
MASH_STEP: int = 4
START: int = 1
RUNNING: int = 1
RAMP_MIN: int = 15   # MASH_RAMP_ALLOW
DELAY_MIN: int = 10  # the alarm's on-delay (alarms.json)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> st_engine.CompiledPlc:
    gap = st_engine.toolchain_gap()
    if gap:
        pytest.skip(f"compiled ST unavailable: {gap}")
    return st_engine.CompiledPlc.build("plc-brewhouse", "full", tmp_path_factory.mktemp("plc-brewhouse"))


class Bench:
    """A cold-started plc-brewhouse in a mash step, with the mash at ``below``
    degF under the step's setpoint."""

    def __init__(self, template: st_engine.CompiledPlc, below: float) -> None:
        self.plc = template.fresh()
        self.ns = 0
        self.scan(3)
        self.sp: int = self.plc.get("BH_MLT_TT01_SP")  # degF x 10
        self.mash(below)
        self.scan(2, BH_BATCH_XC01_PHASE=MASH_STEP)
        self.scan(2, BH_BATCH_XC01_CMD=START)
        assert self.plc.get("BH_BATCH_XS01_STATE") == RUNNING

    def scan(self, scans: int = 1, **inputs: int) -> None:
        for name, value in inputs.items():
            self.plc.set(name, value)
        for _ in range(scans):
            self.plc.scan(self.ns)
            self.ns += SCAN_NS

    def minutes(self, m: float) -> None:
        self.scan(round(m * 600))

    def mash(self, below: float) -> None:
        self.plc.set("BH_MLT_TT01_PV", self.sp - round(below * 10))

    @property
    def alarm(self) -> int:
        return self.plc.get("BH_MLT_TT01_ALM_DEVIATION")

    @property
    def enabled(self) -> int:
        return self.plc.get("BH_MLT_XS01_ACTIVE")

    @property
    def rest_ms(self) -> int:
        return self.plc.get("STEP_MS")


def test_a_mash_that_stays_cold_alarms(template: st_engine.CompiledPlc) -> None:
    """A mash more than 2 degF under its setpoint used to keep the alarm
    suppressed for good: a mash tun that lost its steam waited in silence."""
    b = Bench(template, below=5.0)
    assert b.plc.get("BH_MLT_TV01_OUT") > 0  # the jacket is asked for steam
    b.minutes(RAMP_MIN - 1)
    assert (b.enabled, b.alarm) == (0, 0)  # still within the time a ramp may take
    b.minutes(2)
    assert (b.enabled, b.alarm) == (1, 0)  # enabled; the on-delay runs
    b.minutes(DELAY_MIN - 2)
    assert b.alarm == 0
    b.minutes(2)
    assert b.alarm == 1
    assert b.rest_ms == 0  # and the rest has not counted a second of it
    # steam is back: the mash reaches its temperature, the alarm clears, the rest counts
    b.mash(0.0)
    b.minutes(1)
    assert b.alarm == 0 and b.rest_ms > 0


def test_a_mash_that_ramps_in_time_does_not_alarm(template: st_engine.CompiledPlc) -> None:
    b = Bench(template, below=16.0)  # a mash-out step: 152 to 168 degF
    for minute in range(14):
        b.mash(16.0 - minute * 1.1)
        b.minutes(1)
        assert b.alarm == 0
    b.mash(0.5)
    b.minutes(RAMP_MIN + DELAY_MIN)
    assert (b.enabled, b.alarm) == (1, 0)
    assert b.rest_ms > 0


def test_a_mash_at_temperature_alarms_when_it_runs_hot_or_cold(template: st_engine.CompiledPlc) -> None:
    """What worked before still does: at temperature the alarm is enabled at once."""
    b = Bench(template, below=0.0)
    b.minutes(1)
    assert (b.enabled, b.alarm) == (1, 0)
    b.mash(-3.0)  # 3 degF over
    b.minutes(DELAY_MIN + 1)
    assert b.alarm == 1
    b.mash(0.0)
    b.minutes(1)
    assert b.alarm == 0


def test_the_ramp_allowance_starts_over_with_each_step(template: st_engine.CompiledPlc) -> None:
    """The second mash step (the mash-out) is a new phase start: it gets its own
    fifteen minutes."""
    b = Bench(template, below=0.0)
    b.minutes(RAMP_MIN + 1)
    b.scan(2, BH_BATCH_XC01_CMD=4)  # abort the step
    b.scan(2, BH_BATCH_XC01_CMD=5)  # reset to Idle
    assert b.plc.get("BH_BATCH_XS01_STATE") == 0
    b.mash(10.0)
    b.scan(2, BH_BATCH_XC01_PHASE=MASH_STEP)
    b.scan(2, BH_BATCH_XC01_CMD=START)
    assert b.plc.get("BH_BATCH_XS01_STATE") == RUNNING
    b.minutes(RAMP_MIN - 1)
    assert (b.enabled, b.alarm) == (0, 0)


class Cold:
    """A cold-started plc-brewhouse, idle."""

    def __init__(self, template: st_engine.CompiledPlc) -> None:
        self.plc = template.fresh()
        self.ns = 0
        self.scan(3)

    def scan(self, scans: int = 1, **inputs: int) -> None:
        for name, value in inputs.items():
            self.plc.set(name, value)
        for _ in range(scans):
            self.plc.scan(self.ns)
            self.ns += SCAN_NS

    def start(self, phase: int, param: int = 0) -> None:
        self.scan(2, BH_BATCH_XC01_PHASE=phase, BH_BATCH_XC03_PARAM=param)
        self.scan(2, BH_BATCH_XC01_CMD=START)
        assert self.plc.get("BH_BATCH_XS01_STATE") == RUNNING

    def finish(self) -> None:
        self.scan(2, BH_BATCH_XC01_CMD=4)  # abort
        self.scan(2, BH_BATCH_XC01_CMD=5)  # reset
        assert self.plc.get("BH_BATCH_XS01_STATE") == 0

    def mash_in_ends_at(self, lo: int = 400, hi: int = 800) -> int:
        """The tun level (% x 10) at which a running mash-in completes."""
        for level in range(lo, hi):
            self.scan(1, BH_MLT_LT01_PV=level)
            if self.plc.get("BH_BATCH_XS01_STATE") == 5:
                return level
        raise AssertionError("the mash-in did not complete")


MASH_IN: int = 3


def test_the_mash_in_takes_the_recipes_strike_liquor(template: st_engine.CompiledPlc) -> None:
    """The mash-in used to fill to 60 %: 140 L more liquor than the recipe's 1,310
    L, all of it at strike temperature, so the mash came out hot. The level is now
    the strike liquor plus what the grist takes up (0.7 L/kg) in the 3,000 L tun."""
    b = Cold(template)
    b.start(MASH_IN)  # the program's own recipe: 1,310 L on 503 kg
    assert b.plc.get("BH_MLT_XV01_CMD") == 1
    assert b.mash_in_ends_at() == 554  # (1310 + 0.7 x 503) / 3000 = 55.4 %
    b.scan(1)
    assert b.plc.get("BH_MLT_XV01_CMD") == 0
    b.finish()
    b.scan(2, BH_MLT_LT01_PV=0)
    b.start(MASH_IN, param=1500)  # the engine's parameter, in liters
    assert b.mash_in_ends_at() == 617
    b.finish()
    b.scan(2, BH_MLT_LT01_PV=0)
    b.start(1, param=400)  # a smaller grain bill was milled
    b.finish()
    b.start(MASH_IN, param=1040)
    assert b.mash_in_ends_at() == 440  # (1040 + 0.7 x 400) / 3000
    b.finish()
    b.scan(2, BH_MLT_LT01_PV=0)
    b.start(MASH_IN, param=60000)  # more than the tun holds: capped
    assert b.mash_in_ends_at(hi=901) <= 900


def test_the_hlt_takes_no_cold_liquor_during_a_mash_in(template: st_engine.CompiledPlc) -> None:
    """A refill that started while the strike liquor was drawn chilled the last
    of it by 15 degF."""
    b = Cold(template)
    b.scan(5, BH_CLT_LT01_PV=800, BH_HLT_LT01_PV=500)  # the HLT is low: it fills
    assert b.plc.get("BH_HLT_XV01_CMD") == 1
    b.start(MASH_IN)
    assert b.plc.get("BH_HLT_XV01_CMD") == 0
    b.scan(50)
    assert b.plc.get("BH_HLT_XV01_CMD") == 0
    b.finish()
    b.scan(2)
    assert b.plc.get("BH_HLT_XV01_CMD") == 1  # and fills again when the mash is in
