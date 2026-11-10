"""plc-brewhouse's mash step and its deviation alarm, in the program itself.

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
