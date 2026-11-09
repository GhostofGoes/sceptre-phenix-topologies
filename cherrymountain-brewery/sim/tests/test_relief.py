"""The vessels' pressure-relief valves and the per-vessel truth outputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cherrymountain_sim.carbonation import BrightTank
from cherrymountain_sim.cellar import Cellar, Environment, InitialState
from cherrymountain_sim.federate import CellarFederate, HelicsConfig, build
from cherrymountain_sim.relief import ReliefValve, ReliefValveConfig

GENERATED: Path = Path(__file__).resolve().parents[2] / "plant" / "generated"
AMBIENT_PSIA: float = 12.1


@pytest.fixture(scope="module")
def full() -> HelicsConfig:
    """The full variant's helics.json."""
    return HelicsConfig.load(GENERATED / "full" / "helics.json")


def test_valve_lifts_at_set_and_reseats_after_blowdown() -> None:
    """Pop at 15 psig, flow capped at the rating, closed at 13.5 psig."""
    psv = ReliefValve(ReliefValveConfig(set_psig=15.0, blowdown_pct=10.0, rated_gps=25.0))
    assert psv.cfg.reseat_psig == pytest.approx(13.5)
    assert psv.relieve(14.9, 100.0, 1.0) == 0.0 and not psv.open
    # Above set with 100 g above the reseat pressure: 25 g/s for four steps.
    vented = [psv.relieve(15.2, 100.0 - 25.0 * i, 1.0) for i in range(4)]
    assert vented == [25.0, 25.0, 25.0, 25.0]
    assert psv.lifts == 1 and not psv.lifted and psv.open  # reseated this step
    assert psv.relieve(13.5, 0.0, 1.0) == 0.0 and not psv.open
    assert psv.vented_g == pytest.approx(100.0)


def test_spunding_setpoint_above_mawp_is_held_by_the_psv() -> None:
    """A spunding setpoint written to 30 psig (an attack, or a typo) no longer
    lets the head rise without limit: the PSV holds it between 13.5 and about
    15 psig, lifts repeatedly, and everything it vents goes into the room."""
    cel = Cellar(
        ["FV102"],
        env=Environment(ambient_psia=AMBIENT_PSIA),
        initial={"FV102": InitialState(phase="fermenting", sim_hours=48, temp_f=66)},
    )
    fv = cel.fermenters["FV102"]
    fv.spund_sp_psia = AMBIENT_PSIA + 30.0
    # Start just under the set pressure so the test is short.
    fv.gas_mol = fv.mol_at(AMBIENT_PSIA + 14.9)
    peak = 0.0
    room_g = 0.0
    for _ in range(1800):
        cel.step(1.0)
        peak = max(peak, fv.pressure_psia - AMBIENT_PSIA)
        room_g += cel.co2_vent_gps
    assert fv.psv.lifts >= 2
    assert peak < 15.5
    assert fv.pressure_psia - AMBIENT_PSIA > 13.4
    assert room_g == pytest.approx(fv.psv.vented_g, rel=1e-6)
    assert fv.co2_vented_g == pytest.approx(fv.psv.vented_g)  # spunding stayed shut


def test_forced_carbonation_stone_lifts_the_bbt_psv_into_the_room() -> None:
    """A stone forced fully open (the inhibit defeated) pressurizes the bright
    tank to the PSV, whose vent is counted in the CO2 the room receives."""
    cel = Cellar(
        [],
        env=Environment(ambient_psia=AMBIENT_PSIA),
        bbts=["BBT201"],
        initial={"BBT201": InitialState(phase="carbonating", temp_f=34, level_pct=90, co2_vol=1.8)},
    )
    bbt: BrightTank = cel.bbts["BBT201"]
    bbt.write("co2_valve_pct", 100.0)
    lifted_at = None
    for t in range(3600):
        cel.step(1.0)
        if bbt.psv.open and lifted_at is None:
            lifted_at = t
            assert cel.co2_vent_gps == pytest.approx(bbt.psv.vent_gps)
    assert lifted_at is not None
    assert bbt.pressure_psia - AMBIENT_PSIA < 15.5
    assert bbt.psv.vented_g > 0


def test_federate_publishes_lift_switch_and_vessel_truth(full: HelicsConfig) -> None:
    """The full plant publishes each vessel's lift switch (rio-cellar, tag
    ZSH01.PV) and the truth outputs: fill, spunding and PSV vents, leaks."""
    cel = build(full, "cellar")
    assert isinstance(cel, CellarFederate)
    values = cel.values()
    for unit in ("fv101", "fv102", "fv103", "fv104", "bbt201", "bbt202"):
        assert values[f"cellar/{unit}-psv.lifted"] is False
        assert values[f"cellar/{unit}.psv_gps"] == 0.0
        assert values[f"cellar/{unit}.leak_gps"] == 0.0
    assert values["cellar/fv101.level_pct"] == 0.0  # empty, awaiting knockout
    assert values["cellar/fv102.level_pct"] == pytest.approx(100.0)
    assert "cellar/bbt201.level_pct" in values  # its LT01 point, not a truth output

    cel.write("fv102.spund_sp_psia", AMBIENT_PSIA + 30.0)
    fv = cel.cellar.fermenters["FV102"]
    fv.gas_mol = fv.mol_at(AMBIENT_PSIA + 15.0)
    cel.step(1.0)
    values = cel.values()
    assert values["cellar/fv102-psv.lifted"] is True
    assert values["cellar/fv102.psv_gps"] > 0
    assert values["cellar/co2.vent_gps"] >= values["cellar/fv102.psv_gps"]
    assert cel.truth()["relief_valves"]["FV102"]["lifts"] == 1

    cel.handle_message(
        json.dumps({"event": {"type": "co2-release", "params": {"fv": "FV-103", "rate": "medium"}}})
    )
    assert cel.values()["cellar/fv103.leak_gps"] == 10.0


def test_ci_slice_has_truth_outputs_but_no_lift_switch() -> None:
    """The lift switch is full plant only (the ci register map stays as it is);
    the fermenters' truth outputs exist in every variant."""
    ci = HelicsConfig.load(GENERATED / "ci" / "helics.json")
    keys = {p.key for p in ci.federates["cellar"].publications}
    assert "cellar/fv101.level_pct" in keys
    assert not any(k.endswith("-psv.lifted") for k in keys)
