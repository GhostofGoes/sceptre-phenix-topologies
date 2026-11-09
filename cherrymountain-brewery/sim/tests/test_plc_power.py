"""plc-power's schedule: clock trust, on-peak window, demand reset, exercise, BESS.

The program (``plc/plc-power/plc-power.st``) runs here two ways. ``model`` is
``plc_power_model.PlcPowerModel``, a Python transcription that always runs.
``st`` is the program's strucpp C++ built with g++ and driven scan by scan
(``st_engine.py``), which runs only where Node, a built strucpp checkout, and
g++ exist (``plc/common/build_zip.sh`` builds the checkout); the test skips
otherwise. ``test_compiled_st_agrees_with_the_model`` ties the two together, so
the model's results hold for the program wherever the compiled run is available.
What neither proves is the OpenPLC runtime (image-table binding, the Modbus
master's polling and tearing, scan jitter); see ``sim/README.md``.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import plc_power_model as model
import pytest
import st_engine

from cherrymountain_sim.plcloop import PlcEngine
from cherrymountain_sim.power import ClockRegisters
from cherrymountain_sim.tariff import Tariff

TOPOLOGY: Path = Path(__file__).resolve().parents[2]
PROGRAM: Path = TOPOLOGY / "plc" / "plc-power" / "plc-power.st"
GENERATED: Path = TOPOLOGY / "plant" / "generated"
NS: int = 1_000_000_000

# plc-power's field and memory variables the tests read.
SHED = "PWR_DMD_XS02_SHED"
ONPEAK = "PWR_DMD_XS01_ONPEAK"
EXERCISE = "PWR_DMD_XS04_EXERCISE"
CLOCK_OK = "PWR_DMD_XS05_CLKOK"
GEN_START = "PWR_GEN_HS01_CMD"
DISPATCH = "PWR_BESS_JC01_SP"
PEAK = "PWR_DMD_JY02_PV"
OUTPUTS: tuple[str, ...] = (
    "PWR_MAIN_HS01_CMD", "PWR_MAIN_HS02_CMD", "PWR_MAIN_HS03_CMD", "PWR_MAIN_HS04_CMD",
    "PWR_PV_HS01_CMD", "PWR_PV_JC01_SP", "PWR_BESS_HS01_CMD", DISPATCH, GEN_START,
    "PWR_DMD_JY01_SP", "PWR_DMD_EY01_SP", PEAK, ONPEAK, SHED, EXERCISE, CLOCK_OK,
)  # fmt: skip


class Rig:
    """One PLC scanned against a simulated ATS clock and a few plant inputs."""

    plc: PlcEngine
    now: dt.datetime
    t_ns: int
    clock_running: bool
    regs: tuple[int, int, int] | None

    def __init__(
        self, plc: PlcEngine, start: str, *, soc: int = 800, import_x10: int = 300
    ) -> None:
        self.plc = plc
        self.now = dt.datetime.fromisoformat(start)
        self.t_ns = 0
        self.clock_running = True
        self.regs = None  # a raw override of (minute, day, month)
        for name, value in {
            "PWR_GEN_ZS02_PV": 1,  # utility available at the ATS
            "PWR_MAIN_ZS01_PV": 1,  # 52-M closed
            "PWR_BESS_EY01_PV": soc,
            "PWR_MAIN_JT01_PV": import_x10,
        }.items():
            plc.set(name, value)

    def scan(self, seconds: float = 1.0) -> None:
        """Advance ``seconds`` (the clock too, while it runs), then scan once."""
        self.t_ns += round(seconds * NS)
        if self.clock_running:
            self.now += dt.timedelta(seconds=seconds)
        minute, day, month = self.regs or self._clock()
        self.plc.set("PWR_GEN_KI01_PV", minute)
        self.plc.set("PWR_GEN_KI02_PV", day)
        self.plc.set("PWR_GEN_KI03_PV", month)
        self.plc.scan(self.t_ns)

    def _clock(self) -> tuple[int, int, int]:
        regs = ClockRegisters.of(self.now)
        return regs.minute, regs.day, regs.month

    def run(self, seconds: float, step: float = 1.0) -> None:
        """Scan every ``step`` seconds for ``seconds``."""
        for _ in range(round(seconds / step)):
            self.scan(step)

    def run_until(self, when: str, step: float = 1.0) -> None:
        """Scan until the simulation clock reaches ``when`` (local, ISO)."""
        target = dt.datetime.fromisoformat(when)
        while self.now < target:
            self.scan(step)

    def trust(self) -> None:
        """Scan until the PLC trusts the clock (two steady minutes)."""
        for _ in range(200):
            self.scan(1.0)
            if self.plc.get(CLOCK_OK):
                return
        raise AssertionError("the clock was never trusted")

    def get(self, name: str) -> int:
        """A PLC variable."""
        return self.plc.get(name)


# -- structure: the program is wired the way the plant and the design say (always runs) ----


def _located(variant: str) -> dict[str, str]:
    """Located variable -> IEC address, from plantgen's located_vars.st."""
    text = (GENERATED / variant / "openplc" / "plc-power" / "located_vars.st").read_text(
        encoding="utf-8"
    )
    return dict(re.findall(r"^\s*(\w+) AT (%\w+(?:\.\d)?) :", text, re.MULTILINE))


@pytest.mark.parametrize("variant", ["full", "lite"])
def test_program_checks_pass_and_every_schedule_tag_is_wired(variant: str) -> None:
    """plcbuild's checks (every RIO output and PLC-computed tag assigned, no
    alarm bit assigned) pass, and the program reads the three clock registers and
    writes the on-peak, exercise-running, and clock-trusted tags and the genset
    start. No Node needed."""
    done = subprocess.run(  # noqa: S603
        ["python3", "-I", str(TOPOLOGY / "plc/common/plcbuild.py"), "plc-power",
         "--variant", variant, "--check-only"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    located = _located(variant)
    code = re.sub(r"\(\*.*?\*\)", "", PROGRAM.read_text(encoding="utf-8"), flags=re.DOTALL)
    for tag in ("PWR_GEN_KI01_PV", "PWR_GEN_KI02_PV", "PWR_GEN_KI03_PV"):
        assert located[tag].startswith("%IW"), tag  # read-only registers
        assert re.search(rf"UINT_TO_DINT\({tag}\)", code), tag
    for tag in (ONPEAK, EXERCISE, CLOCK_OK, GEN_START, DISPATCH, PEAK):
        assert re.search(rf"^\s*{tag} :=", code, re.MULTILINE), tag
    # The clock is read from the ATS controller, never from the cellar's clock.
    assert "CEL_CLK" not in code


def test_reference_model_uses_only_located_variables() -> None:
    """Every ``PWR_*`` name in the Python model is a variable plantgen locates, so
    the model cannot drift to a name the PLC does not have."""
    names = set(re.findall(r'"(PWR_\w+)"', Path(model.__file__).read_text(encoding="utf-8")))
    assert names
    assert names <= set(_located("full")), sorted(names - set(_located("full")))


def test_clock_alarm_comes_from_alarms_json() -> None:
    """The clock alarm is a state alarm on the trusted bit at limit 0 (alarm when
    untrusted) with a 5-minute on-delay, and the outage alarm is suppressed unless
    the plant is shedding (so a weekly exercise is not an outage)."""
    for variant in ("full", "lite"):
        alarms = json.loads((GENERATED / variant / "alarms.json").read_text(encoding="utf-8"))
        by_name = {a["name"]: a for a in alarms["alarms"]}
        clk = by_name["PWR.DMD.XS05.ALM_STATE"]
        assert (clk["kind"], clk["limit"], clk["delay_s"]) == ("state", 0, 300)
        assert clk["source"] == "PWR.DMD.XS05.CLKOK" and clk["plc"] == "plc-power"
        assert by_name["PWR.GEN.ZS01.ALM_STATE"]["suppress"] == "PWR.DMD.XS02.SHED"


def test_exercise_window_ends_before_the_on_peak_window() -> None:
    """The last exercise start plus the command still ends before 17:00, and the
    window is off-peak (the design's "fixed off-peak time")."""
    assert model.EX_END + model.EX_RUN_NS // (60 * NS) < model.ONPEAK_START
    assert model.EX_RUN_NS >= 30 * 60 * NS  # NFPA 110: 30 minutes under load, plus warm-up
    tariff = Tariff()
    for minute in range(model.EX_START, model.EX_END + model.EX_RUN_NS // (60 * NS)):
        at = dt.datetime(2026, 7, 15, minute // 60, minute % 60)  # a Wednesday
        assert at.weekday() == 2 and not tariff.onpeak(at)


# -- the clock: trusted only while steady -------------------------------------------------


def test_cold_start_loads_the_design_defaults(make_plc: Callable[[], PlcEngine]) -> None:
    """The first scan sets the cap (250 kW) and the 50 % reserve and clears the peak."""
    plc = make_plc()
    plc.scan(0)
    assert (plc.get("PWR_DMD_JY01_SP"), plc.get("PWR_DMD_EY01_SP"), plc.get(PEAK)) == (250, 50, 0)


def test_the_clock_is_trusted_after_two_steady_minutes(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """The first reading is only a baseline; each later minute is a step; two steady
    steps restore trust. Until then the on-peak flag stays off, even at 18:00."""
    rig = Rig(make_plc(), "2026-07-15T18:00:20")
    second_tick = dt.datetime(2026, 7, 15, 18, 2, 0)
    while True:
        rig.scan()
        if rig.now >= second_tick:
            break
        assert not rig.get(CLOCK_OK) and not rig.get(ONPEAK), rig.now
    assert rig.get(CLOCK_OK) and rig.get(ONPEAK)


def test_the_on_peak_flag_is_the_tariffs_window(make_plc: Callable[[], PlcEngine]) -> None:
    """Over two weeks that cross a month and a leap day, scanned once a simulated
    minute, the on-peak flag equals ``Tariff.onpeak`` at every minute once the clock
    is trusted: weekdays 17:00 up to 21:00, nothing on weekends."""
    tariff = Tariff()
    rig = Rig(make_plc(), "2028-02-22T00:00:00")
    rig.run(180, 60)
    seen_on = 0
    for _ in range(14 * 1440):
        rig.scan(60)
        assert rig.get(CLOCK_OK)
        assert bool(rig.get(ONPEAK)) == tariff.onpeak(rig.now), rig.now
        seen_on += rig.get(ONPEAK)
    assert seen_on == 10 * 240  # ten weekdays, four hours each


def test_on_peak_flag_over_a_whole_year_with_the_model() -> None:
    """The same agreement across 2027 with the Python model, read every 5 minutes
    (both window edges are multiples of 5; the test above reads every minute)."""
    tariff = Tariff()
    rig = Rig(model.PlcPowerModel(), "2027-01-01T00:00:00")
    rig.run(900, 300)
    for _ in range(365 * 288):
        rig.scan(300)
        assert rig.get(CLOCK_OK)
        assert bool(rig.get(ONPEAK)) == tariff.onpeak(rig.now), rig.now


def test_a_stopped_clock_is_distrusted_and_recovers(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """A clock that stops advancing is trusted for CLK_STALE (3 minutes) and then
    not; the schedule goes to its fallback (no on-peak flag, no shift). When it
    runs again, two steady minutes restore it."""
    rig = Rig(make_plc(), "2026-07-15T17:30:00", import_x10=400)
    rig.trust()
    assert rig.get(ONPEAK) and rig.get(DISPATCH) == 400
    rig.clock_running = False
    rig.run(175)
    assert rig.get(CLOCK_OK)
    rig.run(10)
    assert not rig.get(CLOCK_OK) and not rig.get(ONPEAK) and rig.get(DISPATCH) == 0
    rig.clock_running = True
    rig.run(200)
    assert rig.get(CLOCK_OK) and rig.get(ONPEAK)


@pytest.mark.parametrize(
    "jump_minutes", [-1440, -1, 61, 120, 600], ids=["day back", "1 min back", "61 ahead", "2 h", "10 h"]
)
def test_a_jump_is_not_acted_on_and_is_rebaselined(
    make_plc: Callable[[], PlcEngine], jump_minutes: int
) -> None:
    """A reading that goes backwards or more than an hour ahead is not trusted at
    once, and after CLK_REJECT (10 s) the PLC takes the new time as its baseline and
    wants two steady minutes again. (One minute back is a clock that stepped back.)"""
    rig = Rig(make_plc(), "2026-07-15T17:30:00", import_x10=400)
    rig.trust()
    rig.now += dt.timedelta(minutes=jump_minutes)
    rig.scan()
    assert not rig.get(CLOCK_OK) and not rig.get(ONPEAK) and rig.get(DISPATCH) == 0
    rig.run(15)
    assert not rig.get(CLOCK_OK)  # re-baselined, not yet trusted
    rig.run(150)
    assert rig.get(CLOCK_OK)
    assert bool(rig.get(ONPEAK)) == Tariff().onpeak(rig.now)


def test_a_torn_read_costs_one_scan_not_two_minutes(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """If the minute register wraps to 0 a scan before the date register counts on
    (or the other way round), that one reading does not fit and is ignored; the next
    consistent reading is trusted at once."""
    rig = Rig(make_plc(), "2026-07-15T23:55:00")
    rig.trust()
    rig.run_until("2026-07-15T23:59:59")
    day = ClockRegisters.of(rig.now).day
    rig.regs = (0, day, 7)  # minute wrapped, date not yet
    rig.scan()
    assert not rig.get(CLOCK_OK)
    rig.regs = None
    rig.scan()  # the time is now 00:00:01 on the next day, consistently
    assert rig.get(CLOCK_OK)
    rig.regs = (1439, day + 1, 7)  # date counted on, minute not yet wrapped
    rig.scan()
    assert not rig.get(CLOCK_OK)
    rig.regs = None
    rig.scan()
    assert rig.get(CLOCK_OK)


@pytest.mark.parametrize(
    "regs", [(0, 0, 0), (1440, 20649, 7), (600, 20649, 0), (600, 20649, 13), (600, 0, 7)],
    ids=["unwritten", "minute 1440", "month 0", "month 13", "day 0"],
)  # fmt: skip
def test_garbage_registers_are_never_trusted(
    make_plc: Callable[[], PlcEngine], regs: tuple[int, int, int]
) -> None:
    """Registers that read zero before the federate publishes, or out of range,
    leave the clock untrusted: nothing is scheduled from them."""
    rig = Rig(make_plc(), "2026-07-15T17:30:00")
    rig.regs = regs
    rig.run(600, 10)
    assert not rig.get(CLOCK_OK) and not rig.get(ONPEAK) and not rig.get(EXERCISE)
    assert rig.get(GEN_START) == 0


def test_a_fast_clock_is_followed_up_to_an_hour_per_reading(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """Under ``clock.speedup`` the clock moves several minutes per reading. The PLC
    follows it up to CLK_MAX_STEP (60 minutes) per reading and no further."""
    for step_min, trusted in ((10, True), (60, True), (61, False)):
        rig = Rig(make_plc(), "2026-07-14T00:00:00")
        for _ in range(6):
            rig.now += dt.timedelta(minutes=step_min)
            rig.scan()
        assert bool(rig.get(CLOCK_OK)) is trusted, step_min


# -- the monthly demand reset --------------------------------------------------------------


def test_the_peak_resets_at_the_month_rollover(make_plc: Callable[[], PlcEngine]) -> None:
    """The peak follows the meter's 15-minute demand; at the billing-month rollover it
    resets once, and the meter's last interval (the old month's, still on the register)
    is ignored for one demand interval, then the peak follows the meter again."""
    plc = make_plc()
    rig = Rig(plc, "2026-06-30T23:30:00")
    plc.set("PWR_MAIN_JQ01_PV", 2400)  # 240 kW, an interval of June's
    rig.trust()
    assert rig.get(PEAK) == 240
    rig.run_until("2026-06-30T23:59:40", 10)
    assert rig.get(PEAK) == 240
    rig.run_until("2026-07-01T00:00:30", 10)
    assert rig.get(PEAK) == 0, "reset at the rollover"
    rig.run_until("2026-07-01T00:14:00", 10)
    assert rig.get(PEAK) == 0, "the old month's last interval is not July's"
    plc.set("PWR_MAIN_JQ01_PV", 1300)  # the first July interval: 130 kW
    rig.run_until("2026-07-01T00:16:00", 10)
    assert rig.get(PEAK) == 130
    plc.set("PWR_MAIN_JQ01_PV", 900)
    rig.run(3600, 10)
    assert rig.get(PEAK) == 130, "a lower interval does not lower the peak, and no second reset"


def test_the_peak_resets_at_a_year_rollover_too(make_plc: Callable[[], PlcEngine]) -> None:
    """December to January changes the month register 12 -> 1, which is a rollover."""
    plc = make_plc()
    rig = Rig(plc, "2026-12-31T23:40:00")
    plc.set("PWR_MAIN_JQ01_PV", 1800)
    rig.trust()
    assert rig.get(PEAK) == 180
    rig.run_until("2027-01-01T00:01:00", 10)
    assert rig.get(PEAK) == 0


def test_no_reset_on_an_untrusted_clock_and_one_when_it_returns(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """A rollover is never taken from a clock the PLC does not trust: while the
    registers are garbage the peak stands, and the reset happens once the clock is
    steady again and shows the new month."""
    plc = make_plc()
    rig = Rig(plc, "2026-06-30T23:50:00")
    plc.set("PWR_MAIN_JQ01_PV", 2000)
    rig.trust()
    assert rig.get(PEAK) == 200
    rig.regs = (600, 20649, 13)  # garbage
    rig.run(120, 10)
    assert rig.get(PEAK) == 200 and not rig.get(CLOCK_OK)
    rig.regs = None
    rig.now = dt.datetime(2026, 7, 1, 6, 0)  # the real clock: July, a long jump
    rig.run(300, 10)
    assert rig.get(CLOCK_OK)
    assert rig.get(PEAK) == 0


def test_the_peak_is_not_reset_at_startup(make_plc: Callable[[], PlcEngine]) -> None:
    """The first trusted reading only records the month (the program's own startup
    already clears the peak); the meter's reading is taken at once, not held."""
    plc = make_plc()
    rig = Rig(plc, "2026-07-15T10:00:00")
    plc.set("PWR_MAIN_JQ01_PV", 700)
    rig.run(300, 10)
    assert rig.get(CLOCK_OK) and rig.get(PEAK) == 70


# -- BESS dispatch ---------------------------------------------------------------------------


def _dispatch(
    make_plc: Callable[[], PlcEngine],
    when: str,
    *,
    soc: int,
    import_x10: int,
    brewday: bool = False,
    reserve: int | None = None,
    trusted: bool = True,
) -> int:
    """The BESS setpoint (kW x 10, discharge positive) at ``when`` in a given state."""
    plc = make_plc()
    start = dt.datetime.fromisoformat(when) - dt.timedelta(minutes=3)
    rig = Rig(plc, start.isoformat(), soc=soc, import_x10=import_x10)
    plc.set("PWR_DMD_XS03_BREWDAY", brewday)
    rig.scan()  # the first scan loads the defaults, so a set point goes in after it
    if reserve is not None:
        plc.set("PWR_DMD_EY01_SP", reserve)
    if trusted:
        rig.run_until(when)
        assert rig.get(CLOCK_OK)
    else:
        rig.regs = (0, 0, 0)  # an unwritten clock
        rig.run(180)
    return rig.get(DISPATCH)


WED_EVENING = "2026-07-15T18:00:00"
SAT_EVENING = "2026-07-18T18:00:00"
WED_NIGHT = "2026-07-15T23:00:00"
THU_PRE_BREW = "2026-07-16T03:00:00"


@pytest.mark.parametrize(
    ("when", "state", "expected"),
    [
        # Priority 2, the on-peak shift: 50 kW, never more than the site imports.
        (WED_EVENING, {"soc": 800, "import_x10": 900}, 500),
        (WED_EVENING, {"soc": 800, "import_x10": 300}, 300),
        (WED_EVENING, {"soc": 800, "import_x10": 0}, 0),
        (WED_EVENING, {"soc": 100, "import_x10": 900}, 0),  # empty
        (WED_EVENING, {"soc": 120, "import_x10": 900}, 500),
        (SAT_EVENING, {"soc": 800, "import_x10": 900}, 0),  # weekend: off-peak
        ("2026-07-15T21:00:00", {"soc": 800, "import_x10": 900}, -300),  # over: recharge
        # The brew-day reserve (50 % set point): the shift stops at it, not at 10 %.
        (WED_EVENING, {"soc": 510, "import_x10": 900, "brewday": True}, 500),
        (WED_EVENING, {"soc": 500, "import_x10": 900, "brewday": True}, 0),
        (WED_EVENING, {"soc": 300, "import_x10": 900, "brewday": False}, 500),
        (WED_EVENING, {"soc": 700, "import_x10": 900, "brewday": True, "reserve": 80}, 0),
        # Priority 1, the demand cap (250 kW less a 10 kW margin), at any hour.
        ("2026-07-15T10:00:00", {"soc": 800, "import_x10": 2600}, 200),
        ("2026-07-15T10:00:00", {"soc": 800, "import_x10": 4000}, 1000),
        ("2026-07-15T10:00:00", {"soc": 100, "import_x10": 4000}, 0),
        (WED_EVENING, {"soc": 800, "import_x10": 2500}, 500),  # shift beats a small shave
        (WED_EVENING, {"soc": 800, "import_x10": 3000}, 600),  # shave beats the shift
        # PV surplus is stored at any hour.
        ("2026-07-15T12:00:00", {"soc": 500, "import_x10": -600}, -600),
        ("2026-07-15T12:00:00", {"soc": 950, "import_x10": -600}, 0),
        # Overnight (21:00 to 05:00) grid recharge at 30 kW, never past the cap.
        (WED_NIGHT, {"soc": 500, "import_x10": 300}, -300),
        (WED_NIGHT, {"soc": 950, "import_x10": 300}, 0),
        (WED_NIGHT, {"soc": 500, "import_x10": 2350}, -50),
        (WED_NIGHT, {"soc": 500, "import_x10": 2450}, 50),  # over the cap: the shave wins
        ("2026-07-15T12:00:00", {"soc": 500, "import_x10": 300}, 0),  # not by day
        # A brew day below its reserve before 05:00 charges at full rate.
        (THU_PRE_BREW, {"soc": 300, "import_x10": 300, "brewday": True}, -1000),
        (THU_PRE_BREW, {"soc": 300, "import_x10": 300}, -300),
        (THU_PRE_BREW, {"soc": 600, "import_x10": 300, "brewday": True}, -300),
        (THU_PRE_BREW, {"soc": 300, "import_x10": 2000, "brewday": True}, -400),
        ("2026-07-16T05:00:00", {"soc": 300, "import_x10": 300, "brewday": True}, 0),
    ],
)  # fmt: skip
def test_bess_dispatch_priorities(
    make_plc: Callable[[], PlcEngine], when: str, state: dict[str, int | bool], expected: int
) -> None:
    """The dispatch in each regime of DESIGN.md 7.3 (kW x 10, discharge positive)."""
    assert _dispatch(make_plc, when, **state) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("when", "state", "expected"),
    [
        # No trusted clock: no on-peak shift and no grid recharge, ...
        (WED_EVENING, {"soc": 800, "import_x10": 900}, 0),
        (WED_NIGHT, {"soc": 500, "import_x10": 300}, 0),
        (THU_PRE_BREW, {"soc": 300, "import_x10": 300, "brewday": True}, 0),
        # ... but the demand cap and the PV surplus are the clock-free priorities.
        (WED_EVENING, {"soc": 800, "import_x10": 3000}, 600),
        (WED_EVENING, {"soc": 500, "import_x10": -600}, -600),
    ],
)  # fmt: skip
def test_bess_dispatch_falls_back_without_a_clock(
    make_plc: Callable[[], PlcEngine], when: str, state: dict[str, int | bool], expected: int
) -> None:
    """The conservative fallback on an untrusted clock."""
    assert _dispatch(make_plc, when, trusted=False, **state) == expected  # type: ignore[arg-type]


# -- load shed, and the weekly exercise ---------------------------------------------------------


def test_an_exercise_transfer_is_not_a_load_shed(make_plc: Callable[[], PlcEngine]) -> None:
    """The ATS on the genset with the utility healthy (the exercise, or a
    genset-exercise event) does not open the boiler, packaging, and RTU feeders;
    an outage does, and they stay open until the ATS retransfers, then reclose in
    stages a minute apart."""
    plc = make_plc()
    rig = Rig(plc, "2026-07-15T08:00:00")
    rig.trust()
    plc.set("PWR_GEN_YS01_PV", 1)
    plc.set("PWR_GEN_ZS01_PV", 1)
    rig.run(120)
    assert not rig.get(SHED) and rig.get(EXERCISE)
    assert [rig.get(f"PWR_MAIN_HS0{i}_CMD") for i in (2, 3, 4)] == [1, 1, 1]
    plc.set("PWR_GEN_ZS01_PV", 0)
    plc.set("PWR_GEN_YS01_PV", 0)
    rig.run(10)
    assert not rig.get(EXERCISE) and not rig.get(SHED)
    plc.set("PWR_GEN_ZS02_PV", 0)  # a real outage
    rig.run(2)
    assert rig.get(SHED) and not rig.get(EXERCISE)
    assert [rig.get(f"PWR_MAIN_HS0{i}_CMD") for i in (2, 3, 4)] == [0, 0, 0]
    plc.set("PWR_GEN_ZS01_PV", 1)  # the genset carries EP-1
    plc.set("PWR_GEN_YS01_PV", 1)
    rig.run(30)
    plc.set("PWR_GEN_ZS02_PV", 1)  # the utility is back; the ATS has not retransferred
    rig.run(200)
    assert rig.get(SHED) and not rig.get(EXERCISE)
    assert [rig.get(f"PWR_MAIN_HS0{i}_CMD") for i in (2, 3, 4)] == [0, 0, 0]
    plc.set("PWR_GEN_ZS01_PV", 0)
    plc.set("PWR_GEN_YS01_PV", 0)
    rig.run(2)
    assert not rig.get(SHED)
    seen = []
    for _ in range(4):
        rig.run(61)
        seen.append([rig.get(f"PWR_MAIN_HS0{i}_CMD") for i in (2, 3, 4)])
    assert seen == [[1, 0, 0], [1, 1, 0], [1, 1, 1], [1, 1, 1]]


def _ats_on(plc: PlcEngine, on: bool) -> None:
    """Put the ATS on the genset (with the utility healthy) or back on the utility."""
    plc.set("PWR_GEN_YS01_PV", on)
    plc.set("PWR_GEN_ZS01_PV", on)


def test_the_weekly_exercise_runs_once_in_its_window(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """Wednesday at 10:00 the PLC commands the genset for 32 minutes (30 under load
    plus the warm-up), then not again that day, and again the next Wednesday; never
    on another weekday and not before 10:00."""
    plc = make_plc()
    rig = Rig(plc, "2026-07-13T09:00:00")  # a Monday
    rig.trust()
    starts: list[dt.datetime] = []
    ends: list[dt.datetime] = []
    last = 0
    for _ in range(15 * 24 * 60):  # to the Tuesday after next
        rig.scan(60)
        cmd = rig.get(GEN_START)
        _ats_on(plc, bool(cmd))
        if cmd and not last:
            starts.append(rig.now)
        if last and not cmd:
            ends.append(rig.now)
        last = cmd
    assert [s.isoformat(sep=" ") for s in starts] == ["2026-07-15 10:00:00", "2026-07-22 10:00:00"]
    assert [(e - s).total_seconds() / 60 for s, e in zip(starts, ends, strict=True)] == [32.0, 32.0]


@pytest.mark.parametrize(
    ("name", "block", "unblock"),
    [
        ("utility lost", ("PWR_GEN_ZS02_PV", 0), ("PWR_GEN_ZS02_PV", 1)),
        ("52-M open", ("PWR_MAIN_ZS01_PV", 0), ("PWR_MAIN_ZS01_PV", 1)),
        ("ATS on the genset", ("PWR_GEN_ZS01_PV", 1), ("PWR_GEN_ZS01_PV", 0)),
        ("genset running", ("PWR_GEN_YS01_PV", 1), ("PWR_GEN_YS01_PV", 0)),
    ],
)  # fmt: skip
def test_the_exercise_does_not_start_in_a_blocked_plant(
    make_plc: Callable[[], PlcEngine],
    name: str,
    block: tuple[str, int],
    unblock: tuple[str, int],
) -> None:
    """During an outage or load shed (utility lost, 52-M open), with the ATS on the
    genset, or with the set already running, the exercise does not start inside its
    window; once the plant allows it (feeders restored after an outage), it starts."""
    plc = make_plc()
    rig = Rig(plc, "2026-07-15T09:55:00")
    rig.trust()
    plc.set(*block)
    rig.run_until("2026-07-15T10:20:00", 5)
    assert rig.get(GEN_START) == 0, name
    plc.set(*unblock)
    rig.run(300, 5)  # three feeder stages of a minute each after an outage
    assert rig.get(GEN_START) == 1, name


def test_the_exercise_does_not_start_on_an_untrusted_clock(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """With garbage on the clock registers inside the window nothing starts; once the
    clock is steady and still inside the window, it does."""
    plc = make_plc()
    rig = Rig(plc, "2026-07-15T09:55:00")
    rig.trust()
    rig.regs = (0, 0, 0)
    rig.run_until("2026-07-15T10:20:00", 5)
    assert rig.get(GEN_START) == 0
    rig.regs = None
    rig.run(200, 5)
    assert rig.get(CLOCK_OK) and rig.get(GEN_START) == 1


def test_the_exercise_waits_for_the_feeders_to_reclose(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """A utility loss just before the window: the exercise does not start when the
    window opens or when the utility returns, but after the three feeder stages (a
    minute apart) have restored the load."""
    plc = make_plc()
    rig = Rig(plc, "2026-07-15T09:50:00")
    rig.trust()
    rig.run_until("2026-07-15T09:58:00")
    plc.set("PWR_GEN_ZS02_PV", 0)  # an outage; the ATS retransfers the moment it ends
    rig.run_until("2026-07-15T09:59:00")
    plc.set("PWR_GEN_ZS02_PV", 1)
    rig.run_until("2026-07-15T10:01:30")
    assert rig.get(GEN_START) == 0 and rig.get("PWR_MAIN_HS04_CMD") == 0
    rig.run_until("2026-07-15T10:02:30")
    assert rig.get("PWR_MAIN_HS04_CMD") == 1 and rig.get(GEN_START) == 1


def test_the_exercise_stops_for_an_outage_and_does_not_resume(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """A utility loss during the exercise drops the start command at once (the ATS
    starts the set itself), and the PLC does not exercise again that day."""
    plc = make_plc()
    rig = Rig(plc, "2026-07-15T09:57:00")
    rig.trust()
    rig.run_until("2026-07-15T10:05:00")
    assert rig.get(GEN_START) == 1
    plc.set("PWR_GEN_ZS02_PV", 0)
    rig.scan()
    assert rig.get(GEN_START) == 0 and rig.get(SHED)
    plc.set("PWR_GEN_ZS02_PV", 1)
    rig.run_until("2026-07-15T11:30:00", 5)
    assert rig.get(GEN_START) == 0


def test_a_running_exercise_finishes_on_its_own_timer_without_a_clock(
    make_plc: Callable[[], PlcEngine],
) -> None:
    """If the clock fails during the exercise, the command still ends on the 32-minute
    timer (the run is bounded and does not depend on the clock), and no new one starts."""
    plc = make_plc()
    rig = Rig(plc, "2026-07-15T09:57:00")
    rig.trust()
    rig.run_until("2026-07-15T10:10:00")
    assert rig.get(GEN_START) == 1
    rig.regs = (0, 0, 0)
    rig.run(10 * 60)
    assert rig.get(GEN_START) == 1
    rig.run(25 * 60)
    assert rig.get(GEN_START) == 0


# -- the alarms, from the generated logic (compiled ST only) -------------------------------------


def test_clock_alarm_rises_and_clears(st_plc: st_engine.CompiledPlc) -> None:
    """A stopped clock raises the clock alarm through the generated CmAlarm logic
    (3 minutes stale plus its 5-minute on-delay), and a steady clock clears it."""
    rig = Rig(st_plc, "2026-07-15T08:00:00")
    rig.trust()
    assert not rig.get("PWR_DMD_XS05_ALM_STATE")
    rig.clock_running = False
    rig.run(7 * 60)
    assert not rig.get("PWR_DMD_XS05_ALM_STATE")
    rig.run(2 * 60)
    assert rig.get("PWR_DMD_XS05_ALM_STATE")
    rig.clock_running = True
    rig.run(240)
    assert rig.get(CLOCK_OK) and not rig.get("PWR_DMD_XS05_ALM_STATE")


def test_clock_alarm_does_not_fire_at_a_normal_start(st_plc: st_engine.CompiledPlc) -> None:
    """A PLC that boots beside a healthy clock is trusted in two minutes, well inside
    the alarm's 5-minute on-delay, so a normal start raises nothing."""
    rig = Rig(st_plc, "2026-07-15T08:00:07")
    for _ in range(10 * 60):
        rig.scan()
        assert not rig.get("PWR_DMD_XS05_ALM_STATE")
    assert rig.get(CLOCK_OK)


def test_outage_alarm_is_for_outages_not_exercises(st_plc: st_engine.CompiledPlc) -> None:
    """The ATS on the genset raises the utility-outage alarm during an outage, and not
    during an exercise (the ATS on the genset with the utility healthy)."""
    rig = Rig(st_plc, "2026-07-15T08:00:00")
    rig.trust()
    _ats_on(st_plc, True)
    rig.run(30)
    assert not rig.get("PWR_GEN_ZS01_ALM_STATE")  # an exercise
    _ats_on(st_plc, False)
    st_plc.set("PWR_GEN_ZS02_PV", 0)
    rig.run(10)
    _ats_on(st_plc, True)
    rig.run(10)
    assert rig.get("PWR_GEN_ZS01_ALM_STATE")  # an outage


# -- the program against its model --------------------------------------------------------------


def test_compiled_st_agrees_with_the_model(st_plc: st_engine.CompiledPlc) -> None:
    """Both run 40,000 scans of the same random plant: a clock that runs, freezes,
    jumps, tears, or reads garbage; the utility and the ATS (a small model of
    each); load, battery, meter, and operator inputs. Every output agrees on
    every scan, and the run covers a month rollover, a Wednesday exercise, the
    on-peak window, outages, and every clock fault."""
    rng = random.Random(2026)
    ref = model.PlcPowerModel()
    now = dt.datetime(2026, 6, 29, 20, 0)
    t_ns = 0
    clock_mode, mode_left = "run", 0
    state: dict[str, int] = {
        "PWR_GEN_ZS02_PV": 1, "PWR_MAIN_ZS01_PV": 1, "PWR_GEN_ZS01_PV": 0, "PWR_GEN_YS01_PV": 0,
        "PWR_BESS_EY01_PV": 700, "PWR_MAIN_JT01_PV": 300, "PWR_BESS_JT01_PV": 0,
        "PWR_MAIN_JQ01_PV": 300, "PWR_DMD_XS03_BREWDAY": 0,
    }  # fmt: skip
    seen: dict[str, set[int]] = {name: set() for name in OUTPUTS}
    ats_start = ats_end = -1.0
    for i in range(40_000):
        step = rng.choice([1, 1, 1, 1, 2, 10, 30, 59, 61])
        t_ns += step * NS
        if clock_mode == "run":
            now += dt.timedelta(seconds=step)
        mode_left -= 1
        if mode_left <= 0:
            clock_mode = rng.choices(
                ["run", "freeze", "back", "ahead", "tear", "garbage"], [30, 1, 1, 1, 1, 1]
            )[0]
            mode_left = rng.randint(1, 300) if clock_mode != "run" else rng.randint(200, 4000)
            if clock_mode in ("back", "ahead"):
                now += dt.timedelta(minutes=rng.choice([-1440, -90, -5, 61, 200]))
                clock_mode = "run"
        regs = ClockRegisters.of(now)
        reading = [regs.minute, regs.day, regs.month]
        if clock_mode == "tear":
            reading[rng.choice([0, 1])] += rng.choice([-1, 1]) * rng.choice([1, 1439, 1])
        elif clock_mode == "garbage":
            reading = [rng.choice([0, 1440, 65535]), rng.choice([0, 20649]), rng.choice([0, 13, 7])]
        # Plant inputs: slow random walks and rare events, and a small ATS.
        # Outages are rare and short: lost at 0.04 % a scan, back at 1 %.
        if rng.random() < (0.0004 if state["PWR_GEN_ZS02_PV"] else 0.01):
            state["PWR_GEN_ZS02_PV"] ^= 1
        if rng.random() < (0.0002 if state["PWR_MAIN_ZS01_PV"] else 0.01):
            state["PWR_MAIN_ZS01_PV"] ^= 1
        if rng.random() < 0.002:
            state["PWR_DMD_XS03_BREWDAY"] ^= 1
        state["PWR_BESS_EY01_PV"] = min(max(state["PWR_BESS_EY01_PV"] + rng.randint(-40, 40), 50), 990)
        state["PWR_MAIN_JT01_PV"] = rng.choice([-800, 0, 300, 900, 2300, 2600, 3500])
        state["PWR_BESS_JT01_PV"] = rng.choice([-300, 0, 500])
        if rng.random() < 0.1:
            state["PWR_MAIN_JQ01_PV"] = rng.randint(0, 3200)
        if rng.random() < 0.0002:
            ref.set("PWR_DMD_JY01_SP", rng.choice([10, 150, 250, 700]))
            st_plc.set("PWR_DMD_JY01_SP", ref.get("PWR_DMD_JY01_SP"))
            ref.set("PWR_DMD_EY01_SP", rng.choice([0, 50, 80, 200]))
            st_plc.set("PWR_DMD_EY01_SP", ref.get("PWR_DMD_EY01_SP"))
        outage = not state["PWR_GEN_ZS02_PV"] or not state["PWR_MAIN_ZS01_PV"]
        want_set = outage or bool(st_plc.get(GEN_START))
        if want_set and ats_start < 0:
            ats_start, ats_end = t_ns / NS, -1.0
        if not want_set and ats_start >= 0 and ats_end < 0:
            ats_end = t_ns / NS
        if ats_end >= 0 and t_ns / NS - ats_end > 120:
            ats_start = -1.0
        state["PWR_GEN_YS01_PV"] = int(ats_start >= 0)
        state["PWR_GEN_ZS01_PV"] = int(ats_start >= 0 and t_ns / NS - ats_start >= 9)
        for engine in (ref, st_plc):
            for name, value in state.items():
                engine.set(name, value)
            engine.set("PWR_GEN_KI01_PV", reading[0] & 0xFFFF)
            engine.set("PWR_GEN_KI02_PV", reading[1] & 0xFFFF)
            engine.set("PWR_GEN_KI03_PV", reading[2] & 0xFFFF)
            engine.scan(t_ns)
        for name in OUTPUTS:
            got, want = st_plc.get(name), ref.get(name)
            assert got == want, (i, now, name, got, want)
            seen[name].add(got)
    # The run exercised what it claims to.
    assert seen[ONPEAK] == {0, 1} and seen[SHED] == {0, 1} and seen[EXERCISE] == {0, 1}
    assert seen[CLOCK_OK] == {0, 1} and seen[GEN_START] == {0, 1}
    assert min(seen[DISPATCH]) < 0 < max(seen[DISPATCH])
    assert 0 in seen[PEAK] and max(seen[PEAK]) > 100
