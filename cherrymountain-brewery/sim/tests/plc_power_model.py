"""A Python reference model of ``plc/plc-power/plc-power.st`` (DESIGN.md §7.3).

The model is a line-by-line transcription of the program's control logic (load
shed, the clock check, the on-peak window, the monthly demand reset, the weekly
genset exercise, BESS dispatch, demand tracking) with the same variable names,
so it can stand in for the PLC where no strucpp toolchain exists:
``tests/test_plc_power.py`` runs the compiled ST and this model side by side on
the same inputs and asserts every output agrees on every scan, and the
federation tests drive the federates with whichever is available. It leaves out
the generated alarm blocks (``CmAlarm``), which the compiled run covers.

Integer semantics follow the ST: registers are raw (kW and % times 10 on the
RIO, unscaled on the clock and in ``%MW``), ``dispatch`` and ``site_import``
are DINT. A scan reads ``TIME()`` as the nanosecond time given to :meth:`scan`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Final

NS: Final[int] = 1_000_000_000

#: The ST's constants (``VAR CONSTANT``), in the units the ST uses.
FEEDER_STAGE_NS: Final[int] = 60 * NS
CAP_MARGIN: Final[int] = 100
BESS_MAX: Final[int] = 1000
SOC_EMPTY: Final[int] = 100
SOC_FULL: Final[int] = 950
PV_SITE_LIMIT: Final[int] = 1000
ONPEAK_START: Final[int] = 1020
ONPEAK_END: Final[int] = 1260
BREW_START: Final[int] = 300
ONPEAK_KW: Final[int] = 500
RECHARGE_KW: Final[int] = 300
DEMAND_INTERVAL: Final[int] = 15
CLK_GOOD_TICKS: Final[int] = 2
CLK_MAX_STEP: Final[int] = 60
CLK_REJECT_NS: Final[int] = 10 * NS
CLK_STALE_NS: Final[int] = 180 * NS
EX_DOW: Final[int] = 3
EX_START: Final[int] = 600
EX_END: Final[int] = 960
EX_RUN_NS: Final[int] = 32 * 60 * NS


class Ton:
    """IEC 61131-3 TON as strucpp's standard library compiles it."""

    state: int
    prev_in: bool
    start_ns: int
    q: bool

    def __init__(self) -> None:
        self.state = 0
        self.prev_in = False
        self.start_ns = 0
        self.q = False

    def __call__(self, in_: bool, pt_ns: int, now_ns: int) -> bool:
        """One call; returns ``Q``."""
        if self.state == 0 and not self.prev_in and in_:
            self.state, self.q, self.start_ns = 1, False, now_ns
        elif not in_:
            self.q, self.state = False, 0
        elif self.state == 1 and self.start_ns + pt_ns <= now_ns:
            self.state, self.q = 2, True
        self.prev_in = in_
        return self.q


def _clamp(value: int, lo: int, hi: int) -> int:
    return min(max(value, lo), hi)


class PlcPowerModel:
    """The program's variables and one scan of its logic."""

    vars: defaultdict[str, int]
    stage: int
    started: bool
    outage_hold: bool
    stage_timer: Ton
    last_min: int
    have_last: bool
    good_ticks: int
    clk_reject_timer: Ton
    clk_stale_timer: Ton
    month_known: bool
    last_month: int
    hold_until: int
    ex_cmd: bool
    ex_day: int
    ex_timer: Ton
    now_min: int
    clk_ok: bool

    def __init__(self) -> None:
        self.vars = defaultdict(int)
        self.stage = 3
        self.started = False
        self.outage_hold = False
        self.stage_timer = Ton()
        self.last_min = 0
        self.have_last = False
        self.good_ticks = 0
        self.clk_reject_timer = Ton()
        self.clk_stale_timer = Ton()
        self.month_known = False
        self.last_month = 0
        self.hold_until = 0
        self.ex_cmd = False
        self.ex_day = 0
        self.ex_timer = Ton()
        self.now_min = 0
        self.clk_ok = False

    # -- the engine interface (the same as st_engine.CompiledPlc) ---------------------

    def set(self, name: str, value: int | bool) -> None:
        """Set a located input or operator memory."""
        self.vars[name] = int(value)

    def get(self, name: str) -> int:
        """A located variable."""
        return int(self.vars[name])

    def scan(self, time_ns: int) -> None:
        """Run the program once at scan-cycle time ``time_ns``."""
        v = self.vars
        if not self.started:
            v["PWR_DMD_JY01_SP"] = 250
            v["PWR_DMD_EY01_SP"] = 50
            v["PWR_DMD_JY02_PV"] = 0
            self.started = True
        v["PWR_DMD_JY01_SP"] = _clamp(v["PWR_DMD_JY01_SP"], 50, 600)
        v["PWR_DMD_EY01_SP"] = min(v["PWR_DMD_EY01_SP"], 100)

        shed = self._outage(v)
        self._restore(v, shed, time_ns)
        self._clock(v, time_ns)
        minute = v["PWR_GEN_KI01_PV"]
        dow = (v["PWR_GEN_KI02_PV"] + 4) % 7
        onpeak = self.clk_ok and 1 <= dow <= 5 and ONPEAK_START <= minute < ONPEAK_END
        v["PWR_DMD_XS01_ONPEAK"] = int(onpeak)
        overnight = self.clk_ok and (minute >= ONPEAK_END or minute < BREW_START)
        self._month(v)
        self._exercise(v, shed, dow, minute, time_ns)
        v["PWR_PV_HS01_CMD"] = 1
        v["PWR_PV_JC01_SP"] = PV_SITE_LIMIT
        self._dispatch(v, shed, onpeak, overnight)
        self._demand(v)

    # -- the program's sections --------------------------------------------------------

    def _outage(self, v: defaultdict[str, int]) -> bool:
        """Load shed: an outage, or the ATS still on the genset after one."""
        outage = not v["PWR_GEN_ZS02_PV"] or not v["PWR_MAIN_ZS01_PV"]
        if outage:
            self.outage_hold = True
        elif not v["PWR_GEN_ZS01_PV"]:
            self.outage_hold = False
        shed = outage or (bool(v["PWR_GEN_ZS01_PV"]) and self.outage_hold)
        v["PWR_DMD_XS02_SHED"] = int(shed)
        return shed

    def _restore(self, v: defaultdict[str, int], shed: bool, time_ns: int) -> None:
        """Staged feeder restoration."""
        if shed:
            self.stage = 0
        if self.stage_timer(not shed and self.stage < 3, FEEDER_STAGE_NS, time_ns):
            self.stage += 1
            self.stage_timer(False, FEEDER_STAGE_NS, time_ns)
        v["PWR_MAIN_HS01_CMD"] = 1
        v["PWR_MAIN_HS02_CMD"] = int(self.stage >= 1)
        v["PWR_MAIN_HS03_CMD"] = int(self.stage >= 2)
        v["PWR_MAIN_HS04_CMD"] = int(self.stage >= 3)

    def _clock(self, v: defaultdict[str, int], time_ns: int) -> None:
        """Trust the ATS clock only while it is in range, advancing, and in step."""
        minute, day, month = v["PWR_GEN_KI01_PV"], v["PWR_GEN_KI02_PV"], v["PWR_GEN_KI03_PV"]
        self.now_min = day * 1440 + minute
        in_range = minute < 1440 and 1 <= month <= 12 and day > 0
        fits = (
            in_range
            and self.have_last
            and self.now_min >= self.last_min
            and self.now_min - self.last_min <= CLK_MAX_STEP
        )
        tick = fits and self.now_min > self.last_min
        miss = in_range and self.have_last and not fits
        idle = in_range and self.have_last and not tick
        if not in_range:
            self.have_last, self.good_ticks = False, 0
        elif not self.have_last:
            self.last_min, self.have_last, self.good_ticks = self.now_min, True, 0
        elif tick:
            self.last_min = self.now_min
            self.good_ticks = min(self.good_ticks + 1, CLK_GOOD_TICKS)
        rejected = self.clk_reject_timer(miss, CLK_REJECT_NS, time_ns)
        stale = self.clk_stale_timer(idle, CLK_STALE_NS, time_ns)
        if rejected or stale:
            self.have_last, self.good_ticks = False, 0
        self.clk_ok = fits and self.good_ticks >= CLK_GOOD_TICKS
        v["PWR_DMD_XS05_CLKOK"] = int(self.clk_ok)

    def _month(self, v: defaultdict[str, int]) -> None:
        """The billing-month rollover resets the peak (only on a trusted clock)."""
        if not self.clk_ok:
            return
        month = v["PWR_GEN_KI03_PV"]
        if not self.month_known:
            self.last_month, self.month_known = month, True
        elif month != self.last_month:
            self.last_month = month
            v["PWR_DMD_JY02_PV"] = 0
            self.hold_until = self.now_min + DEMAND_INTERVAL

    def _exercise(
        self, v: defaultdict[str, int], shed: bool, dow: int, minute: int, time_ns: int
    ) -> None:
        """The weekly genset exercise: start in its window, stop on an outage."""
        day = v["PWR_GEN_KI02_PV"]
        if (
            self.clk_ok
            and dow == EX_DOW
            and EX_START <= minute < EX_END
            and day != self.ex_day
            and not self.ex_cmd
            and not shed
            and self.stage >= 3
            and v["PWR_GEN_ZS02_PV"]
            and v["PWR_MAIN_ZS01_PV"]
            and not v["PWR_GEN_ZS01_PV"]
            and not v["PWR_GEN_YS01_PV"]
        ):
            self.ex_cmd, self.ex_day = True, day
        done = self.ex_timer(self.ex_cmd, EX_RUN_NS, time_ns)
        if self.ex_cmd and (done or shed):
            self.ex_cmd = False
            self.ex_timer(False, EX_RUN_NS, time_ns)
        v["PWR_GEN_HS01_CMD"] = int(self.ex_cmd)
        v["PWR_DMD_XS04_EXERCISE"] = int(
            self.ex_cmd or (bool(v["PWR_GEN_ZS01_PV"]) and not self.outage_hold)
        )

    def _dispatch(
        self, v: defaultdict[str, int], shed: bool, onpeak: bool, overnight: bool
    ) -> None:
        """BESS: demand cap, on-peak shift, PV surplus, overnight recharge."""
        v["PWR_BESS_HS01_CMD"] = 1
        soc = v["PWR_BESS_EY01_PV"]
        reserve = v["PWR_DMD_EY01_SP"] * 10
        shift_floor = SOC_EMPTY
        if v["PWR_DMD_XS03_BREWDAY"]:
            shift_floor = max(SOC_EMPTY, reserve)
        site_import = _signed(v["PWR_MAIN_JT01_PV"]) + _signed(v["PWR_BESS_JT01_PV"])
        cap = v["PWR_DMD_JY01_SP"] * 10 - CAP_MARGIN
        discharge = charge = 0
        if site_import > cap and soc > SOC_EMPTY:
            discharge = min(site_import - cap, BESS_MAX)
        if onpeak and soc > shift_floor:
            discharge = max(discharge, min(ONPEAK_KW, max(site_import, 0)))
        if site_import < 0 and soc < SOC_FULL:
            charge = min(-site_import, BESS_MAX)
        if overnight and soc < SOC_FULL:
            rate = RECHARGE_KW
            if v["PWR_DMD_XS03_BREWDAY"] and soc < reserve:
                rate = BESS_MAX
            charge = max(charge, min(rate, max(cap - site_import, 0)))
        dispatch = discharge - charge
        if shed:
            dispatch = 0
        v["PWR_BESS_JC01_SP"] = dispatch

    def _demand(self, v: defaultdict[str, int]) -> None:
        """Month-to-date peak 15-minute demand."""
        demand_kw = v["PWR_MAIN_JQ01_PV"] // 10
        holding = self.clk_ok and self.now_min < self.hold_until
        if demand_kw > v["PWR_DMD_JY02_PV"] and not holding:
            v["PWR_DMD_JY02_PV"] = demand_kw


def _signed(raw: int) -> int:
    """A 16-bit register read as INT (the located variable is INT)."""
    return raw - 0x10000 if raw >= 0x8000 else raw
