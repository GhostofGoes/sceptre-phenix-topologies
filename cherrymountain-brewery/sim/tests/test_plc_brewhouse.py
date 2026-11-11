"""The brewhouse federate with a PLC in the loop.

The other tests drive the brewhouse with its built-in autopilot. In the range
plc-brewhouse writes every command instead, so these tests run the federate
against a Python port of the phase logic of ``plc/plc-brewhouse/plc-brewhouse.st``
(the phases and the equipment control; no entry lockout and no alarms), with a
stand-in batch engine that commands phases 1 to 10 in order. Registers are raw,
as in the program: the engineering value x 10, and x 100 for pressures and the
O2 flow.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from cherrymountain_sim.brewhouse import BrewhouseFederate
from cherrymountain_sim.federate import HelicsConfig, Value, build

FULL: Path = Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"

MILL_TIME: float = 300.0
VORLAUF_TIME: float = 600.0
MASH_HOLD: float = 3600.0
WHIRLPOOL_TIME: float = 1200.0
HOP_DOSE_TIME: float = 30.0
BOIL_MINUTES_X10: int = 600
MASH_IN_LEVEL: int = 554  # 1,310 L of strike liquor on 503 kg of grist in the 3,000 L tun
KETTLE_FULL: int = 850
KETTLE_EMPTY: int = 50
LEVEL_LOW: int = 600
LEVEL_HIGH: int = 900
BED_DP_HIGH: int = 120
COMPLETE: int = 5
PHASES: tuple[str, ...] = (
    "MILL",
    "HLT_HEAT",
    "MASH_IN",
    "MASH_STEP",
    "VORLAUF",
    "LAUTER",
    "BOIL",
    "WHIRLPOOL",
    "KNOCKOUT",
    "CIP_READY",
)
#: Raw scale of the written analogs that are not x 10.
WRITE_SCALE: dict[str, float] = {"knockout.o2_sp_slpm": 100.0}


def pvalve(sp: int, pv: int, gain: int) -> int:
    """The program's PValve: (SP - PV) x GAIN, clamped to 0..1000."""
    return max(0, min((sp - pv) * gain, 1000))


class Ton:
    """An IEC TON timer, called once a scan (1 s)."""

    et: float
    q: bool

    def __init__(self) -> None:
        self.et = 0.0
        self.q = False

    def __call__(self, on: bool, pt: float) -> bool:
        if not on:
            self.et, self.q = 0.0, False
        else:
            self.et = min(self.et + 1.0, pt)
            self.q = self.et >= pt
        return self.q


class Plc:
    """plc-brewhouse.st, one call of :meth:`scan` per 1 s federate step."""

    hlt_sp: int
    mlt_sp: int
    ko_sp: int
    phase: int
    cmd: int
    state: int
    active: int
    boil_ki: int
    boil_point: int
    clt_fill: bool
    hlt_fill: bool
    step_timer: Ton
    hop_timer: Ton
    boil_tick: Ton

    def __init__(self) -> None:
        self.hlt_sp, self.mlt_sp, self.ko_sp = 1680, 1520, 640
        self.phase = self.cmd = self.state = self.active = 0
        self.boil_ki = self.boil_point = 0
        self.clt_fill = self.hlt_fill = False
        self.step_timer, self.hop_timer, self.boil_tick = Ton(), Ton(), Ton()

    def scan(self, values: dict[str, Value]) -> dict[str, Value]:
        """One scan: read the field values, return every output."""

        def raw(point: str, scale: int = 10) -> int:
            return max(0, min(round(float(values[f"brewhouse/{point}"]) * scale), 65535))

        def bit(point: str) -> bool:
            return bool(values[f"brewhouse/{point}"])

        cmd, self.cmd = self.cmd, 0
        if cmd == 1 and self.state in (0, 5, 6) and 1 <= self.phase <= 10:
            self.active, self.state = self.phase, 1
            self.step_timer(False, 0.0)
            if self.active == 7:
                self.boil_ki = 0
        elif cmd == 5 and self.state in (5, 6):
            self.state = self.active = 0
        running = self.state == 1
        out: dict[str, int | bool] = {}
        # Liquor tanks, whatever the phase.
        clt, hlt = raw("clt.level_pct"), raw("hlt.level_pct")
        if clt < LEVEL_LOW:
            self.clt_fill = True
        elif clt >= LEVEL_HIGH or bit("clt.high_level"):
            self.clt_fill = False
        out["clt.fill_valve"] = self.clt_fill
        if hlt < LEVEL_LOW:
            self.hlt_fill = True
        elif hlt >= LEVEL_HIGH:
            self.hlt_fill = False
        out["hlt.fill_valve"] = self.hlt_fill and clt > 100 and not (running and self.active == 3)
        # BH.HLT.LSL01: steam only while the jacket is covered.
        out["hlt.steam_valve_pct"] = (
            pvalve(self.hlt_sp, raw("hlt.temp_f"), 20) if bit("hlt.low_level") else 0
        )
        # Boiling point and the boil timer.
        p_kpa = raw("kettle.ambient_psia", 100) / 100.0 * 6.89476
        if p_kpa > 10.0:
            t_k = 1.0 / (1.0 / 373.15 - 8.314 * math.log(p_kpa / 101.325) / 40660.0)
            self.boil_point = int(((t_k - 273.15) * 1.8 + 32.0 + 0.5) * 10.0)
        boil = self.boil_point > 0 and raw("kettle.temp_f") + 5 >= self.boil_point
        tick = running and self.active == 7 and boil and not self.boil_tick.q
        if self.boil_tick(tick, 6.0):
            self.boil_ki += 1
        # Phase logic: outputs default off.
        for point in (
            "mill.run",
            "mill.auger_run",
            "mlt.strike_valve",
            "mlt.rake_run",
            "kettle.whirlpool_valve",
            "kettle.hop_doser",
            "knockout.pump_run",
            "knockout.to_fv_valve",
        ):
            out[point] = False
        for point in (
            "mill.speed_sp_hz",
            "mlt.steam_valve_pct",
            "mlt.runoff_valve_pct",
            "mlt.sparge_valve_pct",
            "mlt.rake_speed_pct",
            "kettle.steam_valve_pct",
            "knockout.pump_speed_pct",
            "knockout.glycol_valve_pct",
            "knockout.o2_sp_slpm",
        ):
            out[point] = 0
        complete = False
        if running:
            match self.active:
                case 1:
                    out["mill.run"] = out["mill.auger_run"] = True
                    out["mill.speed_sp_hz"] = 500
                    complete = self.step_timer(True, MILL_TIME)
                case 2:
                    complete = raw("hlt.temp_f") + 10 >= self.hlt_sp and hlt >= LEVEL_LOW
                case 3:
                    out["mlt.strike_valve"] = out["mlt.rake_run"] = True
                    out["mlt.rake_speed_pct"] = 300
                    complete = raw("mlt.level_pct") >= MASH_IN_LEVEL
                case 4:
                    out["mlt.rake_run"] = True
                    out["mlt.rake_speed_pct"] = 300
                    out["mlt.steam_valve_pct"] = pvalve(self.mlt_sp, raw("mlt.temp_f"), 30)
                    on_temp = raw("mlt.temp_f") + 20 >= self.mlt_sp
                    complete = self.step_timer(on_temp, MASH_HOLD)
                case 5:
                    out["mlt.runoff_valve_pct"] = 300
                    complete = self.step_timer(True, VORLAUF_TIME)
                case 6:
                    out["mlt.runoff_valve_pct"] = 600
                    out["mlt.sparge_valve_pct"] = 500
                    out["mlt.rake_run"] = raw("mlt.bed_dp_psi", 100) > BED_DP_HIGH
                    out["mlt.rake_speed_pct"] = 200
                    complete = raw("kettle.level_pct") >= KETTLE_FULL
                case 7:
                    out["kettle.steam_valve_pct"] = 800
                    complete = self.boil_ki >= BOIL_MINUTES_X10
                case 8:
                    out["kettle.whirlpool_valve"] = True
                    out["kettle.hop_doser"] = not self.hop_timer(True, HOP_DOSE_TIME)
                    complete = self.step_timer(True, WHIRLPOOL_TIME)
                case 9:
                    out["knockout.pump_run"] = out["knockout.to_fv_valve"] = True
                    out["knockout.pump_speed_pct"] = 700
                    out["knockout.glycol_valve_pct"] = pvalve(
                        raw("knockout.wort_out_f"), self.ko_sp, 40
                    )
                    out["knockout.o2_sp_slpm"] = min(raw("knockout.wort_gpm") * 2, 1000)
                    complete = raw("kettle.level_pct") <= KETTLE_EMPTY
                case _:
                    complete = True
        else:
            self.step_timer(False, 0.0)
            self.hop_timer(False, 0.0)
        if complete:
            self.state = COMPLETE
            self.step_timer(False, 0.0)
            self.hop_timer(False, 0.0)
        return {
            point: value if isinstance(value, bool) else value / WRITE_SCALE.get(point, 10.0)
            for point, value in out.items()
        }


class Rig:
    """The brewhouse federate, the PLC, and a batch engine that runs phases."""

    fed: BrewhouseFederate
    plc: Plc
    max_ibu: float

    def __init__(self) -> None:
        fed = build(HelicsConfig.load(FULL), "brewhouse")
        assert isinstance(fed, BrewhouseFederate)
        self.fed = fed
        self.plc = Plc()
        self.max_ibu = 0.0

    def scan(self) -> None:
        """One PLC scan and one federate step, as rio-brewhouse relays them."""
        writes = self.plc.scan(self.fed.values())
        message = json.dumps([{"tag": tag, "value": value} for tag, value in writes.items()])
        assert len(self.fed.handle_message(message)) == len(writes)
        self.fed.step(1.0)
        self.max_ibu = max(self.max_ibu, self.fed.ibu)

    def run_phase(self, name: str, limit_s: float = 4 * 3600.0) -> float:
        """Start a phase and scan until it completes; its duration in seconds."""
        self.plc.phase, self.plc.cmd = PHASES.index(name) + 1, 1
        seconds = 0.0
        while True:
            self.scan()
            seconds += 1.0
            if self.plc.state == COMPLETE:
                break
            assert seconds < limit_s, f"{name} did not complete in {limit_s / 3600:.0f} h"
        self.plc.cmd = 5  # Reset
        self.scan()
        return seconds

    def brew(self) -> dict[str, float]:
        """One brew, phase by phase; the duration of each."""
        return {name: self.run_phase(name) for name in PHASES}


@pytest.fixture(scope="module")
def two_brews() -> tuple[Rig, dict[str, float], dict[str, float], dict[str, float]]:
    """A rig after two PLC-driven brews: the phase durations of each brew and
    the state between the two."""
    rig = Rig()
    first = rig.brew()
    proc = rig.fed.proc
    between = {
        "completed": float(proc.brews_completed),
        "active": float(rig.fed.output_value("batch.active")),
        "kettle_l": proc.kettle.liters,
        "grain_kg": proc.grain_kg,
        "mlt_l": proc.mlt.liters,
        "max_ibu": rig.max_ibu,
    }
    second = rig.brew()
    return rig, first, second, between


def test_the_plc_heats_the_hlt_while_its_jacket_is_covered() -> None:
    """BH.HLT.LSL01 is true while the liquor covers the jacket: plc-brewhouse
    opens the HLT's steam valve only then. With the switch reading the other
    way the HLT stayed cold and HLT_HEAT never completed."""
    rig = Rig()
    assert rig.fed.proc.hlt.level_pct > 50.0
    assert rig.fed.values()["brewhouse/hlt.low_level"] is True
    rig.run_phase("MILL")
    start_f = float(rig.fed.values()["brewhouse/hlt.temp_f"])
    assert rig.run_phase("HLT_HEAT", limit_s=2 * 3600.0) > 60.0
    assert float(rig.fed.values()["brewhouse/hlt.temp_f"]) > start_f + 20.0
    rig.fed.proc.hlt.liters = 0.1 * rig.fed.proc.hlt.capacity_l
    assert rig.fed.values()["brewhouse/hlt.low_level"] is False


def test_a_plc_driven_brew_is_counted_and_leaves_the_vessels_empty(
    two_brews: tuple[Rig, dict[str, float], dict[str, float], dict[str, float]],
) -> None:
    """plc-brewhouse ends KNOCKOUT at 5 % of the kettle and nothing commands
    graining out, so the model's brewer dumps the trub and the spent grain."""
    rig, _first, _second, between = two_brews
    assert not rig.fed.autopilot
    assert between["completed"] == 1.0
    assert between["active"] == 0.0
    assert between["kettle_l"] == 0.0
    assert between["grain_kg"] == 0.0
    assert between["mlt_l"] == 0.0
    assert rig.fed.proc.brews_completed == 2
    assert rig.fed.output_value("batch.active") == 0.0


def test_the_second_plc_brew_mashes_in_like_the_first(
    two_brews: tuple[Rig, dict[str, float], dict[str, float], dict[str, float]],
) -> None:
    """With the first mash still in the tun, the second MASH_IN ended at once
    on the level it found there."""
    _rig, first, second, _between = two_brews
    assert first["MASH_IN"] > 300.0
    assert second["MASH_IN"] == pytest.approx(first["MASH_IN"], rel=0.2)
    assert second["LAUTER"] == pytest.approx(first["LAUTER"], rel=0.2)


def test_the_brewer_adds_the_hops_of_a_plc_driven_boil(
    two_brews: tuple[Rig, dict[str, float], dict[str, float], dict[str, float]],
) -> None:
    """The boil and whirlpool hops are manual additions: with a PLC driving
    the wort had no bitterness at all."""
    rig, _first, _second, between = two_brews
    assert between["max_ibu"] > 5.0
    assert rig.fed.proc.hops == []  # dumped with the trub


def test_an_interrupted_knockout_is_counted_once() -> None:
    """A knockout that stops above the heel and resumes counts one brew."""
    rig = Rig()
    for name in PHASES[:8]:
        rig.run_phase(name)
    rig.plc.phase, rig.plc.cmd = 9, 1
    while rig.fed.proc.kettle.level_pct > 40.0:
        rig.scan()
    rig.plc.cmd = 4  # Abort: the pump stops with wort left in the kettle
    for _ in range(30):
        rig.scan()
    assert rig.fed.proc.brews_completed == 0
    assert rig.fed.proc.kettle.liters > 500.0
    rig.plc.cmd = 5
    rig.scan()
    rig.run_phase("KNOCKOUT")
    assert rig.fed.proc.brews_completed == 1
    assert rig.fed.proc.kettle.liters == 0.0
