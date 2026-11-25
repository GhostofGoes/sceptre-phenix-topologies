"""Beer moving through the cellar: transfers, the crew's hand work, the beer line
to the filler, and the knockout that keeps to one fermenter (crew.py, cellar.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cherrymountain_sim.carbonation import BrightTank, equilibrium_volumes
from cherrymountain_sim.cellar import (
    LITERS_PER_BBL,
    RACK_CO2_KEPT,
    Cellar,
    InitialState,
    Transfer,
)
from cherrymountain_sim.crew import CellarCrew, CrewSettings
from cherrymountain_sim.federate import CellarFederate, HelicsConfig, build
from cherrymountain_sim.local import LocalFederation
from cherrymountain_sim.packaging import STOP_SUPPLY, PackagingFederate
from cherrymountain_sim.packml import State
from cherrymountain_sim.timebase import TimeBase

FULL: Path = Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"
CI: Path = Path(__file__).resolve().parents[2] / "plant" / "generated" / "ci" / "helics.json"
FVS: list[str] = ["FV101", "FV102", "FV103", "FV104"]
BBL: float = LITERS_PER_BBL
#: A fermenter a day into its crash, cold: ready to rack.
READY: InitialState = InitialState(phase="fermenting", sim_hours=264, temp_f=34, spund_sp_psig=12.0)


def cellar(**initial: InitialState) -> Cellar:
    """Four fermenters and two bright tanks; what ``initial`` leaves out is empty."""
    # No time compression: an hour of these tests is an hour of fermentation too.
    return Cellar(FVS, tb=TimeBase(compression=1.0), initial=initial, bbts=["BBT201", "BBT202"])


class Hands:
    """A cellar and its crew, stepped a second at a time."""

    def __init__(self, cel: Cellar, **settings: float | bool | str) -> None:
        self.cellar = cel
        self.crew = CellarCrew(cel, CrewSettings(**settings))  # type: ignore[arg-type]
        self.t = 0.0
        self.jobs: list[str] = []

    def run(self, minutes: float) -> None:
        for _ in range(round(minutes * 60)):
            before = self.crew.job
            self.crew.step(self.t)
            job = self.crew.job
            if job is not None and before is None:
                self.jobs.append(f"{job.kind} {job.src}")
            self.cellar.step(1.0)
            self.t += 1.0


# ---------------------------------------------------------------- transfers


def test_a_transfer_moves_beer_at_the_pumps_rate_and_stops_when_the_source_is_dry() -> None:
    cel = cellar(BBT201=InitialState(phase="carbonating", temp_f=36, level_pct=50, co2_vol=2.5))
    src, dst = cel.bbts["BBT201"], cel.bbts["BBT202"]
    head = dst.pressure_psia
    cel.transfer = Transfer(src="BBT201", dst="BBT202", gpm=30.0)
    cel.step(60.0)
    assert cel.transfer_gpm == pytest.approx(30.0)
    assert dst.liquid_l == pytest.approx(30.0 * 3.78541) and not cel.transfer.done
    for _ in range(40):
        cel.step(60.0)
    assert cel.transfer.done and cel.transfer_gpm == 0.0
    assert cel.transfer.moved_l == pytest.approx(15 * BBL)
    assert dst.liquid_l == pytest.approx(15 * BBL) and src.liquid_l == 0.0
    # the beer arrives with its CO2 (less what an empty tank's low head lets out
    # while it fills) and its temperature, against the tank's head
    assert 2.3 < dst.co2_vol <= 2.5
    assert dst.temp_f == pytest.approx(36.0, abs=1.0)
    assert head <= dst.pressure_psia < head + 3.0  # the gas the beer lets out
    # an emptied tank is dirty and holds no dissolved gas
    assert not src.clean and src.co2_vol == 0.0


def test_a_transfer_stops_when_the_destination_is_full() -> None:
    cel = cellar(
        BBT201=InitialState(phase="carbonating", temp_f=34, level_pct=90, co2_vol=2.6),
        BBT202=InitialState(phase="serving", temp_f=34, level_pct=40, co2_vol=2.6),
    )
    cel.env.beer_draw_gpm = 0.0
    cel.transfer = Transfer(src="BBT201", dst="BBT202", gpm=30.0)
    for _ in range(40):
        cel.step(60.0)
    assert cel.transfer.done
    assert cel.bbts["BBT202"].level_pct == pytest.approx(100.0)
    assert cel.bbts["BBT201"].level_pct == pytest.approx(30.0)
    assert cel.bbts["BBT201"].clean  # it still holds beer


def test_racking_leaves_the_yeast_and_keeps_part_of_the_beers_gas() -> None:
    cel = cellar(FV104=READY)
    fv, bbt = cel.fermenters["FV104"], cel.bbts["BBT201"]
    gas = RACK_CO2_KEPT * equilibrium_volumes(fv.zone_temp_f("cone"), fv.pressure_psia)
    cel.transfer = Transfer(src="FV104", dst="BBT201", gpm=30.0, leave_l=2.0 * BBL)
    for _ in range(40):
        cel.step(60.0)
    assert cel.transfer.done
    assert fv.liquid_l == pytest.approx(2.0 * BBL) and fv.filled  # not dumped yet
    assert bbt.liquid_l == pytest.approx(28.0 * BBL)
    assert 1.0 < bbt.co2_vol < 2.6 and bbt.co2_vol == pytest.approx(gas, abs=0.15)
    assert fv.dump() == pytest.approx(2.0 * BBL)
    assert not fv.filled and fv.gravity_plato == 0.0 and fv.level_pct == 0.0
    assert not fv.clean and fv.age_h == 0.0


def test_a_tank_takes_no_more_than_its_room() -> None:
    bbt = BrightTank("BBT201", phase="carbonating", level_pct=95, co2_vol=2.0)
    assert bbt.receive(10 * BBL, 34.0, 2.0) == pytest.approx(1.5 * BBL)
    assert bbt.receive(1.0, 34.0, 2.0) == 0.0
    assert bbt.draw(100 * BBL) == pytest.approx(30 * BBL)
    assert bbt.draw(1.0) == 0.0


# ---------------------------------------------------------------- the crew


def test_the_crew_racks_a_ready_fermenter_dumps_it_and_cleans_it() -> None:
    h = Hands(cellar(FV104=READY, BBT202=InitialState(phase="serving", level_pct=70, co2_vol=2.6)))
    assert h.crew.ready(h.cellar.fermenters["FV104"])
    h.run(5)
    assert h.jobs == ["rack FV104"] and h.crew.job is not None and h.crew.job.stage == "setup"
    assert not h.crew.route_set and not h.crew.pumping and h.cellar.transfer is None
    h.run(10)
    assert h.crew.job.stage == "pump" and h.crew.route_set and h.crew.pumping
    assert h.cellar.transfer_gpm == pytest.approx(30.0)
    h.run(45)  # 27.9 bbl at 30 gal/min is 29 minutes
    fv = h.cellar.fermenters["FV104"]
    assert not fv.filled and not fv.clean
    assert h.jobs == ["rack FV104", "clean FV104"]
    assert h.cellar.bbts["BBT201"].level_pct == pytest.approx(93.0, abs=0.1)
    h.run(50)
    assert fv.clean and h.crew.job is None
    truth = h.crew.truth()
    assert truth["racked_bbl"] == pytest.approx(27.9, abs=0.05)
    assert truth["dumped_bbl"] == pytest.approx(2.1, abs=0.05)
    assert (truth["cleans"], truth["dirty"], truth["job"]) == (1, [], None)


def test_the_oldest_ready_fermenter_goes_first_and_the_next_waits_for_the_tank() -> None:
    older = READY.model_copy(update={"sim_hours": 300})
    h = Hands(cellar(FV102=READY, FV103=older))
    h.run(120)
    assert h.jobs == ["rack FV103", "clean FV103"]
    assert h.crew.truth()["ready_to_rack"] == ["FV102"]  # BBT-201 is full of FV-103's beer


def test_a_fermenter_that_is_not_cold_or_not_old_enough_is_left_alone() -> None:
    warm = READY.model_copy(update={"temp_f": 66})
    young = READY.model_copy(update={"sim_hours": 200})
    h = Hands(cellar(FV101=warm, FV102=young))
    for fv in h.cellar.fermenters.values():
        fv.set_valve("side", fv.unit == "FV102")
        fv.set_valve("cone", fv.unit == "FV102")
    h.run(30)
    assert h.jobs == [] and h.crew.truth()["ready_to_rack"] == []


def test_bright_beer_goes_to_the_filler_when_the_serving_tank_is_low() -> None:
    bright = InitialState(phase="carbonating", temp_f=34, level_pct=90, co2_vol=2.6)
    h = Hands(cellar(BBT201=bright, BBT202=InitialState(phase="serving", level_pct=30, co2_vol=2.6)))
    h.cellar.env.beer_draw_gpm = 4.0  # 9 bbl in the tank: 10 % after 47 minutes
    h.run(40)
    assert h.jobs == []  # not low yet
    h.run(20)
    assert h.jobs == ["to-filler BBT201"]
    h.run(60)
    assert h.jobs == ["to-filler BBT201", "clean BBT201"]
    assert h.cellar.bbts["BBT202"].level_pct > 60 and h.cellar.bbts["BBT201"].liquid_l == 0.0
    assert h.crew.truth()["to_filler_bbl"] == pytest.approx(27.0, abs=0.05)
    h.run(50)
    assert h.cellar.bbts["BBT201"].clean


def test_beer_that_is_not_bright_stays_in_the_conditioning_tank() -> None:
    flat = InitialState(phase="carbonating", temp_f=34, level_pct=90, co2_vol=1.8)
    h = Hands(cellar(BBT201=flat, FV104=READY))
    h.run(60)
    assert h.jobs == []  # nothing for the filler, and no room to rack into
    assert not h.crew.bright(h.cellar.bbts["BBT201"])


def test_the_conditioning_tank_is_cleaned_before_the_next_racking() -> None:
    h = Hands(cellar(FV104=READY))
    h.cellar.bbts["BBT201"].clean = False
    h.run(120)
    assert h.jobs == ["clean BBT201", "rack FV104", "clean FV104"]


def test_a_crew_that_is_off_does_nothing_and_the_ci_slice_has_no_tanks_to_work() -> None:
    h = Hands(cellar(FV104=READY), enabled=False)
    h.run(60)
    assert h.jobs == [] and h.cellar.fermenters["FV104"].filled
    assert h.crew.truth()["enabled"] is False
    fed = build(HelicsConfig.load(CI), "cellar")
    assert isinstance(fed, CellarFederate)
    assert fed.crew.serving is None and fed.crew.conditioning is None
    for _ in range(120):
        fed.step(1.0)
    assert fed.crew.job is None


def test_settings_the_crew_does_not_know_are_refused() -> None:
    with pytest.raises(ValueError, match="clean_minutes"):
        CrewSettings.model_validate({"clean_minutes": 30})


# ---------------------------------------------------------------- the knockout


def test_a_knockout_keeps_to_its_fermenter_and_the_overflow_goes_to_the_drain() -> None:
    """A brew day's second brew overfills its fermenter by a barrel or two. That
    wort used to be routed to the next empty fermenter, where it would have
    started a batch of its own once fermenters empty."""
    cel = cellar()
    cel.env.knockout_gpm = 60.0
    for _ in range(20):  # 1,200 gal: more than FV-101 holds
        cel.step(60.0)
    assert cel.knockout_target == "FV101"
    assert cel.fermenters["FV101"].level_pct == pytest.approx(100.0)
    assert not cel.fermenters["FV102"].filled
    assert cel.knockout_lost_l == pytest.approx(1200 * 3.78541 - 30 * BBL)
    cel.env.knockout_gpm = 0.0
    cel.step(60.0)
    cel.env.knockout_gpm = 60.0  # the next brew day starts a new knockout
    cel.step(60.0)
    assert cel.knockout_target == "FV102" and cel.fermenters["FV102"].filled


def test_wort_into_a_fermenter_that_was_not_cleaned_is_counted() -> None:
    h = Hands(cellar(), enabled=False)
    h.cellar.fermenters["FV101"].clean = False
    h.cellar.env.knockout_gpm = 60.0
    h.run(2)
    assert h.cellar.fermenters["FV101"].dirty_fill
    assert h.crew.truth()["dirty_fills"] == {"FV101": 1}
    h.run(2)
    assert h.crew.truth()["dirty_fills"] == {"FV101": 1}  # once per fill


# ---------------------------------------------------------------- the federate


@pytest.fixture
def fed() -> CellarFederate:
    built = build(HelicsConfig.load(FULL), "cellar")
    assert isinstance(built, CellarFederate)
    return built


def test_the_beer_line_has_pressure_only_with_a_tank_on_line(fed: CellarFederate) -> None:
    serving = fed.cellar.bbts["BBT202"]
    assert fed.output_value("beer.supply_psia") == pytest.approx(serving.pressure_psia)
    assert fed.values()["cellar/beer.supply_psia"] > 20.0
    serving.write("outlet_valve", False)  # plc-cellar closes it under 5 %
    assert fed.output_value("beer.supply_psia") == 0.0
    serving.write("outlet_valve", True)
    serving.draw(serving.liquid_l)  # the tank runs dry with its outlet open
    assert fed.output_value("beer.supply_psia") == 0.0
    truth = fed.truth()
    assert truth["bright_tanks"]["BBT202"]["on_line"] is False
    assert truth["bright_tanks"]["BBT201"]["level_pct"] == pytest.approx(90.0)
    assert truth["crew"]["enabled"] is True and truth["knockout_lost_bbl"] == 0.0


def test_the_transfer_panel_shows_what_the_crew_does(fed: CellarFederate) -> None:
    def panel() -> tuple[bool, bool, float]:
        v = fed.values()
        return (
            bool(v["cellar/transfer.route_ok"]),
            bool(v["cellar/transfer.pump_running"]),
            float(v["cellar/transfer.flow_gpm"]),
        )

    assert panel() == (False, False, 0.0)
    # a pump commanded to run with no route open moves nothing
    fed.write("transfer.pump_run", True)
    fed.step(1.0)
    assert panel() == (False, True, 0.0)
    fed.write("transfer.pump_run", False)
    # the serving tank low and the conditioning tank bright: the crew transfers
    fed.cellar.bbts["BBT201"].co2_vol = 2.6
    fed.cellar.bbts["BBT202"].draw(fed.cellar.bbts["BBT202"].liquid_l * 0.95)
    for _ in range(11 * 60):
        fed.step(1.0)
    route, running, flow = panel()
    assert route and running and flow == pytest.approx(30.0)
    assert fed.truth()["crew"]["job"] == {"kind": "to-filler", "src": "BBT201", "dst": "BBT202", "stage": "pump"}


def test_the_filler_holds_without_beer_and_runs_again_when_a_tank_is_on_line() -> None:
    """Packaging used to read BBT-202's head pressure, which the regulator holds
    in an empty tank too: the line went on counting bottles from nothing."""
    lf = LocalFederation(HelicsConfig.load(FULL), names=["cellar", "packaging"])
    cel, pkg = lf.feds["cellar"], lf.feds["packaging"]
    assert isinstance(cel, CellarFederate) and isinstance(pkg, PackagingFederate)
    pkg.settings = pkg.settings.model_copy(update={"autopilot": "on"})
    pkg.clock = pkg.clock.model_copy(update={"start": pkg.clock.start.replace(hour=7)})
    for machine in pkg.machines.values():
        machine.next_jam = float("inf")
    lf.run(300)
    filler = pkg.machines["filler"]
    assert filler.pml.state == State.EXECUTE and pkg.bottles_total > 50
    serving = cel.cellar.bbts["BBT202"]
    serving.draw(serving.liquid_l)
    lf.run(60)
    assert filler.pml.state == State.HELD and filler.stop_reason == STOP_SUPPLY
    held_at = pkg.bottles_total
    lf.run(300)
    assert pkg.bottles_total <= held_at + 130  # only what was already past the filler
    serving.receive(10 * BBL, 34.0, 2.6)
    lf.run(120)
    assert filler.pml.state == State.EXECUTE
    lf.run(300)
    assert pkg.bottles_total > held_at + 130
