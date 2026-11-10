"""plc-cellar's fermentation profile, in the program itself.

The full and lite plants' program (``plc/plc-cellar/plc-cellar-plant.st``) is
compiled by strucpp and g++ (``st_engine.py``) and scanned here: first alone,
with the fermenters' inputs set by hand, then in control of the cellar federate
with the crew at work, which is the whole way from a fill to a racked fermenter.
The tests skip where the toolchain is not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import st_engine

from cherrymountain_sim.federate import CellarFederate, HelicsConfig, build
from cherrymountain_sim.plcloop import PlcLink

FULL: Path = Path(__file__).resolve().parents[2] / "plant" / "generated" / "full"
SCAN_NS: int = 100_000_000  # the task interval: 100 ms
SETTLE: int = 600  # scans a reading must hold before it ends a stage (60 s)
EMPTY, FERMENTING, REST, CRASH = 0, 1, 2, 3


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> st_engine.CompiledPlc:
    gap = st_engine.toolchain_gap()
    if gap:
        pytest.skip(f"compiled ST unavailable: {gap}")
    return st_engine.CompiledPlc.build("plc-cellar", "full", tmp_path_factory.mktemp("plc-cellar"))


class Bench:
    """A cold-started plc-cellar whose inputs the test sets."""

    def __init__(self, template: st_engine.CompiledPlc) -> None:
        self.plc = template.fresh()
        self.ns = 0

    def scan(self, scans: int = 1, **inputs: int) -> None:
        for name, value in inputs.items():
            self.plc.set(name, value)
        for _ in range(scans):
            self.plc.scan(self.ns)
            self.ns += SCAN_NS

    def beer(self, n: int, extract: float, temp_f: float) -> None:
        """What fermenter ``n``'s density meter and side probe read."""
        self.scan(
            2,
            **{f"CEL_FV10{n}_DT01_PV": round(extract * 100), f"CEL_FV10{n}_TT01_PV": round(temp_f * 100)},
        )

    def stage(self, n: int) -> int:
        return self.plc.get(f"CEL_PROFILE_KS0{n}_STAGE")

    def setpoints(self, n: int) -> tuple[int, int]:
        return self.plc.get(f"CEL_FV10{n}_TT01_SP"), self.plc.get(f"CEL_FV10{n}_TT02_SP")


def test_the_recipe_is_loaded_and_every_fermenter_starts_on_the_profile(template: st_engine.CompiledPlc) -> None:
    b = Bench(template)
    b.scan(3)
    recipe = [b.plc.get(f"CEL_PROFILE_{tag}_SP") for tag in ("TC01", "TC02", "TC03", "KC01")]
    assert recipe == [6600, 7000, 3400, 180]
    assert [b.plc.get(f"CEL_PROFILE_HS0{n}_AUTO") for n in range(1, 5)] == [1, 1, 1, 1]
    # nothing read yet: every vessel looks empty and waits at the fermentation temperature
    assert [b.stage(n) for n in range(1, 5)] == [EMPTY] * 4
    assert [b.setpoints(n) for n in range(1, 5)] == [(6600, 6600)] * 4


def test_a_start_of_the_plc_finds_each_fermenters_stage(template: st_engine.CompiledPlc) -> None:
    """The plant starts mid-production (DESIGN.md 9.4), and the program has not
    seen any of this beer being filled."""
    b = Bench(template)
    b.scan(3)
    b.beer(2, 9.0, 66.0)  # day 2: still fermenting
    b.beer(3, 3.2, 68.0)  # day 5, warm: near terminal gravity, so its rest starts now
    b.beer(4, 3.0, 50.0)  # day 11, already cold: it was crashed before the start
    assert [b.stage(n) for n in range(1, 5)] == [EMPTY, FERMENTING, REST, CRASH]
    assert [b.setpoints(n) for n in range(1, 5)] == [(6600, 6600), (6600, 6600), (7000, 7000), (3400, 3400)]


def test_a_fill_ferments_rests_for_its_minutes_and_is_crashed(template: st_engine.CompiledPlc) -> None:
    b = Bench(template)
    b.scan(3)
    b.plc.set("CEL_PROFILE_KC01_SP", 2)  # a two-minute rest
    b.beer(1, 14.0, 64.0)
    assert (b.stage(1), b.setpoints(1)) == (FERMENTING, (6600, 6600))
    b.beer(1, 5.2, 66.0)
    assert b.stage(1) == FERMENTING
    b.beer(1, 4.9, 66.0)
    b.scan(SETTLE - 3)
    assert b.stage(1) == FERMENTING  # under terminal gravity, but not for a minute yet
    b.scan(1)
    assert (b.stage(1), b.setpoints(1)) == (REST, (7000, 7000))
    b.beer(1, 5.1, 70.0)  # the meter's noise at the threshold does not restart the rest
    b.scan(1190)  # 119.4 s of the rest so far
    assert b.stage(1) == REST
    b.scan(10)
    assert (b.stage(1), b.setpoints(1)) == (CRASH, (3400, 3400))
    b.beer(1, 2.8, 34.0)
    b.scan(3000)
    assert b.stage(1) == CRASH  # it stays crashed until the vessel is empty


def test_an_emptied_fermenter_goes_back_to_the_fermentation_temperature(template: st_engine.CompiledPlc) -> None:
    """After racking the meter reads 0: the jackets must not go on cooling an
    empty vessel, and the next wort must not be chilled on its way in."""
    b = Bench(template)
    b.scan(3)
    b.beer(4, 3.0, 36.0)
    assert (b.stage(4), b.setpoints(4)) == (CRASH, (3400, 3400))
    b.beer(4, 0.0, 36.0)
    b.scan(SETTLE)
    assert (b.stage(4), b.setpoints(4)) == (EMPTY, (6600, 6600))
    assert b.plc.get("CEL_FV104_TV01_CMD") == 0 and b.plc.get("CEL_FV104_TV02_CMD") == 0
    b.beer(4, 14.2, 64.0)
    assert b.stage(4) == FERMENTING


def test_a_density_meter_that_drops_out_does_not_end_a_stage(template: st_engine.CompiledPlc) -> None:
    """One scan of 0 from the meter used to make a crashed fermenter "empty": its
    setpoint went back to 66 degF, and when the reading returned the beer was
    warmed for a second rest. A reading now has to hold for a minute."""
    b = Bench(template)
    b.scan(3)
    b.plc.set("CEL_PROFILE_KC01_SP", 2)  # a two-minute rest
    b.beer(2, 12.0, 66.0)  # fermenting
    b.beer(3, 3.2, 68.0)   # resting
    b.beer(4, 3.0, 36.0)   # crashed
    assert [b.stage(n) for n in (2, 3, 4)] == [FERMENTING, REST, CRASH]
    # every meter reads 0 for 30 s
    b.scan(300, CEL_FV102_DT01_PV=0, CEL_FV103_DT01_PV=0, CEL_FV104_DT01_PV=0)
    assert [b.stage(n) for n in (2, 3, 4)] == [FERMENTING, REST, CRASH]
    assert b.setpoints(4) == (3400, 3400) and b.setpoints(2) == (6600, 6600)
    # the readings come back: nothing started over
    b.scan(2, CEL_FV102_DT01_PV=1200, CEL_FV103_DT01_PV=320, CEL_FV104_DT01_PV=300)
    assert [b.stage(n) for n in (2, 3, 4)] == [FERMENTING, REST, CRASH]
    # the rest's clock waited through the dropout and did not restart: about two
    # minutes of rest in all, not two minutes after the dropout
    b.scan(1150)
    assert b.stage(3) == REST
    b.scan(60)
    assert b.stage(3) == CRASH
    # a low reading for a few seconds does not end a fermentation either
    b.scan(50, CEL_FV102_DT01_PV=300)
    b.scan(2, CEL_FV102_DT01_PV=1200)
    b.scan(SETTLE)
    assert b.stage(2) == FERMENTING


def test_a_fermenter_taken_off_the_profile_keeps_the_operators_setpoints(template: st_engine.CompiledPlc) -> None:
    b = Bench(template)
    b.scan(3)
    b.beer(3, 3.2, 68.0)
    b.plc.set("CEL_PROFILE_HS03_AUTO", 0)
    b.plc.set("CEL_FV103_TT01_SP", 4500)
    b.plc.set("CEL_FV103_TT02_SP", 4800)
    b.scan(20)
    assert b.setpoints(3) == (4500, 4800)
    assert b.stage(3) == REST  # the stage is still shown
    b.plc.set("CEL_PROFILE_HS03_AUTO", 1)
    b.scan(2)
    assert b.setpoints(3) == (7000, 7000)


def test_the_recipes_values_are_the_operators_within_limits(template: st_engine.CompiledPlc) -> None:
    b = Bench(template)
    b.scan(3)
    b.beer(2, 9.0, 66.0)
    b.beer(4, 3.0, 40.0)
    b.plc.set("CEL_PROFILE_TC01_SP", 6400)
    b.plc.set("CEL_PROFILE_TC03_SP", 3600)
    b.scan(2)
    assert b.setpoints(2) == (6400, 6400) and b.setpoints(4) == (3600, 3600)
    b.plc.set("CEL_PROFILE_TC03_SP", 100)  # 1 degF would freeze the tank
    b.plc.set("CEL_PROFILE_TC02_SP", 20000)
    b.plc.set("CEL_PROFILE_KC01_SP", 65000)
    b.scan(2)
    assert b.plc.get("CEL_PROFILE_TC03_SP") == 2000 and b.plc.get("CEL_PROFILE_TC02_SP") == 9000
    assert b.plc.get("CEL_PROFILE_KC01_SP") == 6000
    assert b.setpoints(4) == (2000, 2000)


def test_the_profile_and_the_crew_take_beer_from_a_fill_to_a_racked_fermenter(template: st_engine.CompiledPlc) -> None:
    """Nine hours of the full plant's cellar with plc-cellar in control: before
    the profile, its fixed setpoints left every fermenter but FV-104 warm for
    good, and the crew racks only cold beer."""
    fed = build(HelicsConfig.load(FULL / "helics.json"), "cellar")
    assert isinstance(fed, CellarFederate)
    plc = template.fresh()
    link = PlcLink("plc-cellar", plc, FULL / "tags.json")
    fed.apply_input("building/vent.co2_relay_ok", 1.0)  # the CO2 relay, so the tanks carbonate
    seen: dict[int, list[int]] = {n: [] for n in range(1, 5)}
    ns = 0
    for second in range(9 * 3600):
        hour = fed.now.hour + fed.now.minute / 60
        on_line = fed.cellar.beer_supply_psia > 0
        fed.apply_input("packaging/beer.draw_gpm", 4.4 if 6 <= hour < 14.5 and on_line else 0.0)
        # a brew's knockout into FV-101 at 09:00: 15 bbl at 30 gal/min
        fed.apply_input("brewhouse/knockout.gpm", 30.0 if 9.0 <= hour < 9.26 else 0.0)
        if second == 5:
            plc.set("CEL_PROFILE_HS02_AUTO", 0)  # FV-102 is left to the operator, who does nothing
        for _ in range(9):  # ten scans a second; the RIO is polled once (plcloop.py)
            plc.scan(ns)
            ns += SCAN_NS
        link.scan(fed.values(), {"cellar": fed}, ns / 1e9)
        ns += SCAN_NS
        fed.step(1.0)
        for n in range(1, 5):
            stage = plc.get(f"CEL_PROFILE_KS0{n}_STAGE")
            if not seen[n] or seen[n][-1] != stage:
                seen[n].append(stage)
    assert fed.plc_driving and not fed.autopilot
    fvs = fed.cellar.fermenters
    # FV-104 was cold at the start: crashed, racked, and back to an empty vessel at 66 degF
    assert seen[4] == [CRASH, EMPTY]
    assert not fvs["FV104"].filled and plc.get("CEL_FV104_TT01_SP") == 6600
    # FV-103 rested for its three hours, was crashed, and is cold
    assert seen[3][:2] == [REST, CRASH]
    assert fvs["FV103"].zone_temp_f("side") < 40.0 or not fvs["FV103"].filled
    # FV-101 was empty, took the knockout, and ferments
    assert seen[1][:2] == [EMPTY, FERMENTING] and fvs["FV101"].filled
    # FV-102, off the profile, is where the operator left it: warm, so nobody racks it
    assert plc.get("CEL_FV102_TT01_SP") == 6600 and fvs["FV102"].zone_temp_f("side") > 60.0
    assert fvs["FV102"].filled and not fed.crew.ready(fvs["FV102"])
    truth = fed.crew.truth()
    assert truth["racked_bbl"] >= 27.0 and truth["dirty_fills"] == {}
