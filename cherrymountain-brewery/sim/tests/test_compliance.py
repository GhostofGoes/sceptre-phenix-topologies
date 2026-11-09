"""Compliance records the federates keep (DESIGN.md §18A): the genset test log
(NFPA 110, 40 CFR 60.4211(f)), the wastewater discharge record (40 CFR 403.5,
the POTW permit), CIP verification records, fill-level inspection against the
NIST Handbook 133 MAV and the 27 CFR 25.142(d) net-contents tolerance, and the
TTB barrel figures of 27 CFR 25.158."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cherrymountain_sim.federate import HelicsConfig, build
from cherrymountain_sim.packaging import (
    BBL_PER_CASE_24X12,
    LABELED_FLOZ,
    MAV_FLOZ,
    TTB_NET_CONTENTS_TOLERANCE_PCT,
    PackagingFederate,
)
from cherrymountain_sim.power import PowerFederate
from cherrymountain_sim.simctl import EventRequest
from cherrymountain_sim.utilities import (
    CIP_MIN_CAUSTIC_F,
    CIP_MIN_CONTACT_S,
    CIP_MIN_RETURN_MS,
    UtilitiesFederate,
)

FULL: Path = (
    Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"
)


@pytest.fixture(scope="module")
def full() -> HelicsConfig:
    """The full variant's helics.json."""
    return HelicsConfig.load(FULL)


def _event(fed: PowerFederate | UtilitiesFederate, kind: str, **params: object) -> None:
    fed.handle_message(json.dumps({"event": {"type": kind, "params": params}}))


def test_genset_exercise_runs_under_load_and_is_logged(full: HelicsConfig) -> None:
    """A remote start (plc-power's weekly exercise) transfers EP-1 to the set,
    so the test runs under load, and the run is logged with its hours. With
    EP-1 at its 33 kW rated load the 30 % of nameplate NFPA 110 asks for is
    met; the hour meter and the calendar-year test hours advance."""
    pytest.importorskip("opendssdirect")
    fed = build(full, "power")
    assert isinstance(fed, PowerFederate)
    for ld in fed.cfg.loads:
        fed.apply_input(ld.key, ld.rated_kw)
    fed.write("gen-ats.gen_start", True)
    for _ in range(40 * 60):
        fed.step(1.0)
    g = fed.grid
    assert g.gen_running and g.on_emergency and g.exercising
    assert fed.energized["ep1"] and fed.energized["msb"]
    assert fed.values()["power/gen-ats.gen_kw"] == pytest.approx(33.0, abs=0.5)
    assert fed.values()["power/gen-ats.utility_ok"] is True
    fed.write("gen-ats.gen_start", False)
    fed.step(1.0)
    assert not g.on_emergency and not g.exercising  # retransfer at once
    for _ in range(301):
        fed.step(1.0)
    assert not g.gen_running  # cool-down done
    assert len(fed.gen_runs) == 1
    run = fed.gen_runs[0]
    assert run.kind == "exercise"
    assert run.nfpa110_duration_ok and run.nfpa110_load_ok
    assert run.loaded_s == pytest.approx(40 * 60 - 8, abs=2.0)
    assert run.avg_kw == pytest.approx(33.0, abs=0.5)
    assert g.gen_hours_total == pytest.approx((40 * 60 + 302) / 3600, abs=0.01)
    assert g.gen_test_hours_year == pytest.approx(g.gen_hours_total)
    truth = fed.truth()["genset"]
    assert truth["test_hours_cap"] == 100.0 and truth["runs"][0]["kind"] == "exercise"


def test_genset_exercise_event_with_a_load_bank(full: HelicsConfig) -> None:
    """At the plant's everyday EP-1 load the monthly test falls short of 30 %
    of nameplate (the annual supplemental load test of NFPA 110 would be due),
    and a timed ``genset-exercise`` event with a load bank makes it up. The
    event ends the exercise by itself, and a utility outage during a test turns
    the run into an emergency run."""
    pytest.importorskip("opendssdirect")
    fed = build(full, "power")
    assert isinstance(fed, PowerFederate)
    for ld in fed.cfg.loads:
        fed.apply_input(ld.key, 12.0 if ld.name == "chiller" else 1.0)
    _event(fed, "genset-exercise", duration=1900)  # 30 min loaded plus warm-up
    for _ in range(1900 + 10):
        fed.step(1.0)
    assert not fed.grid.exercising and fed.gen_runs == []  # still cooling down
    for _ in range(300):
        fed.step(1.0)
    run = fed.gen_runs[-1]
    assert run.kind == "exercise" and run.nfpa110_duration_ok
    assert not run.nfpa110_load_ok and run.avg_kw < 30.0

    _event(fed, "genset-exercise", duration=1900, load_bank_kw=40)
    for _ in range(1900 + 310):
        fed.step(1.0)
    run = fed.gen_runs[-1]
    assert run.load_bank_kw == 40.0 and run.nfpa110_load_ok
    assert fed.grid.gen_test_hours_year == pytest.approx(fed.grid.gen_hours_total)

    _event(fed, "genset-exercise", duration=3600)
    for _ in range(60):
        fed.step(1.0)
    _event(fed, "utility-breaker", action="open")
    for _ in range(30):
        fed.step(1.0)
    assert fed.grid.on_emergency and not fed.grid.exercising
    assert fed.grid.run is not None and fed.grid.run.kind == "emergency"


def test_genset_exercise_event_validation() -> None:
    """The event API refuses a load bank above nameplate, a boolean, a bad
    action, and a bad duration; sane requests pass."""
    good: list[dict[str, object]] = [
        {"event": "genset-exercise"},
        {"event": "genset-exercise", "action": "stop"},
        {"event": "genset-exercise", "load_bank_kw": 40, "duration": 1800},
    ]
    for body in good:
        EventRequest.model_validate(body)
    bad: list[dict[str, object]] = [
        {"event": "genset-exercise", "action": "run"},
        {"event": "genset-exercise", "load_bank_kw": 150},
        {"event": "genset-exercise", "load_bank_kw": True},
        {"event": "genset-exercise", "load_bank_kw": "forty"},
        {"event": "genset-exercise", "duration": 0},
    ]
    for body in bad:
        with pytest.raises(ValueError):
            EventRequest.model_validate(body)


def test_wastewater_discharge_record(full: HelicsConfig) -> None:
    """The permit record: a caustic slug is held back (inhibited time and an
    inhibit event are logged), the discharge that follows stays inside the
    6.0-9.0 band, the day's volume and pH range are recorded, and hourly
    samples accumulate."""
    fed = build(full, "utilities")
    assert isinstance(fed, UtilitiesFederate)
    fed.settings = fed.settings.model_copy(update={"autopilot": "off"})
    fed.write("wwn.discharge_valve", True)  # the PLC asks to discharge...
    fed.wwn.base_mol = 0.05 * fed.wwn.liters  # ...into a pH 12 caustic dump
    for _ in range(120):
        fed.step(1.0)
    w = fed.wwn
    assert w.inhibit_events == 1 and w.inhibited_s == pytest.approx(120.0)
    assert w.discharged_l_day == 0.0
    fed.write("wwn.acid_pump", True)
    for _ in range(4 * 3600):
        fed.step(1.0)
        if fed.ph < 7.2:
            fed.write("wwn.acid_pump", False)
    assert w.discharged_l_day > 0
    assert w.permit_low <= w.discharge_ph_min <= w.discharge_ph_max <= w.permit_high
    assert len(w.samples) >= 4 and all(0 <= s.ph <= 14 for s in w.samples)
    rec = fed.truth()["wastewater"]
    assert rec["permit_band"] == [6.0, 9.0] and rec["inhibit_events"] == 1
    assert rec["discharge_ph_min"] is not None


def test_cip_verification_record(full: HelicsConfig) -> None:
    """A caustic wash on FV-102 with steam, dosing, and both pumps is recorded
    and verified once the supply passes 160 °F, the return passes 40 mS/cm,
    and flow is proven for 10 minutes; a rinse-only run is not verified."""
    fed = build(full, "utilities")
    assert isinstance(fed, UtilitiesFederate)
    fed.settings = fed.settings.model_copy(update={"autopilot": "off"})
    _event(fed, "cip-panel", circuit="fv102")
    for tag, value in (
        ("boiler.enable", True),  # the PLC keeps the steam up for the heater
        ("boiler.pressure_sp_psig", 15.0),
        ("cip.supply_pump", True),
        ("cip.return_pump", True),
        ("cip.caustic_dose", True),
        ("cip.steam_valve_pct", 100.0),
    ):
        fed.write(tag, value)
    for _ in range(60 * 60):
        fed.step(1.0)
    assert fed.cip.run is not None and fed.cip.run.circuit == "fv102"
    fed.write("cip.caustic_dose", False)
    fed.write("cip.supply_pump", False)
    fed.step(1.0)
    assert len(fed.cip.records) == 1
    r = fed.cip.records[0]
    assert r.chemical == "caustic" and r.verified
    assert r.max_supply_f >= CIP_MIN_CAUSTIC_F
    assert r.max_return_ms >= CIP_MIN_RETURN_MS
    assert r.flow_proven_s >= CIP_MIN_CONTACT_S
    # A short rinse with no chemical: logged, not verified.
    fed.write("cip.supply_pump", True)
    for _ in range(120):
        fed.step(1.0)
    fed.write("cip.supply_pump", False)
    fed.step(1.0)
    assert len(fed.cip.records) == 2 and not fed.cip.records[1].verified
    assert fed.truth()["cip"]["records"][-1]["chemical"] == "none"


def test_fill_level_inspection_follows_hb133(full: HelicsConfig) -> None:
    """Bottles below the labeled 12 fl oz by more than the 0.5 fl oz MAV are
    rejected, the accepted lot averages at least the labeled quantity, the
    quarterly net-contents variance stays inside the TTB 0.5 % tolerance, and
    the packaged barrels follow the 27 CFR 25.158 case table."""
    fed = build(full, "packaging")
    assert isinstance(fed, PackagingFederate)
    fed.clock = fed.clock.model_copy(
        update={"start": fed.clock.start.replace(hour=5, minute=59)}
    )
    for _ in range(3 * 3600):
        fed.step(1.0)
    f = fed.fill
    filler = fed.machines["filler"]
    assert f.count > 3000
    assert f.mean_floz >= LABELED_FLOZ
    assert 0.15 < f.std_floz < 0.25
    assert f.under_mav >= 1 and f.under_mav <= filler.defective
    assert f.under_mav / (f.count + f.under_mav) < 0.01
    assert abs(f.quarter_variance_pct) <= TTB_NET_CONTENTS_TOLERANCE_PCT
    ttb = fed.ttb()
    assert ttb["bbl_bottled"] == pytest.approx(fed.cases * BBL_PER_CASE_24X12)
    assert MAV_FLOZ == 0.5 and fed.truth()["fill"]["under_mav_rejects"] == f.under_mav
