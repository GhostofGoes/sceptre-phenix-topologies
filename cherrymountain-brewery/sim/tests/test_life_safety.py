"""Life-safety and equipment-safety points of DESIGN.md §6.7 and §18A.3: the
CO2 supply shutoff at tier 3 (Denver IFC 5307), CO2 detection beyond the
cellar, the chiller's refrigerant detector and charge record (ASHRAE 15,
40 CFR 84 Subpart C), the BESS cell temperature and off-gas detector
(NFPA 855, UL 9540A), and the Section IV boiler's safety valve."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cherrymountain_sim.building import BuildingFederate
from cherrymountain_sim.federate import Federate, HelicsConfig, build
from cherrymountain_sim.local import LocalFederation
from cherrymountain_sim.power import PowerFederate
from cherrymountain_sim.simctl import EventRequest
from cherrymountain_sim.utilities import (
    BOILER_MAWP_PSIG,
    CO2_AUTO_RESET_S,
    REFRIG_LEAK_TRIGGER_PCT,
    REFRIG_NAMEPLATE_LB,
    UtilitiesFederate,
)

FULL: Path = (
    Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"
)


@pytest.fixture(scope="module")
def full() -> HelicsConfig:
    """The full variant's helics.json."""
    return HelicsConfig.load(FULL)


def _event(fed: Federate, kind: str, **params: object) -> None:
    fed.handle_message(json.dumps({"event": {"type": kind, "params": params}}))


def test_shutoff_relay_drops_at_tier_3_in_any_area(full: HelicsConfig) -> None:
    """A large leak at the bulk tank trips the area's tiers; at 30,000 ppm the
    horns and strobes run and the shutoff relay to rio-utilities drops. The
    cellar's own relay (tier 2 in the cellar) stays healthy."""
    bld = build(full, "building")
    assert isinstance(bld, BuildingFederate)
    vals = bld.values()
    assert vals["building/vent.co2_shutoff_ok"] == 1.0
    assert vals["building/bas-vent.co2_shutoff_ok"] is True
    _event(bld, "co2-leak", zone="tank", rate="high", duration=3600)
    for _ in range(120):
        bld.step(10.0)
        if bld.cel.areas["tank"].tiers[2]:
            break
    vals = bld.values()
    assert vals["building/bas-vent.co2_5_ppm"] >= 30000.0
    assert vals["building/bas-vent.co2_area_tier_1"] is True
    assert vals["building/bas-vent.area_exhaust"] is True
    assert vals["building/bas-vent.horn_strobe"] is True
    assert vals["building/vent.co2_shutoff_ok"] == 0.0
    assert vals["building/vent.co2_relay_ok"] == 1.0  # the cellar is clear


def test_loss_of_detection_or_power_drops_both_relays(full: HelicsConfig) -> None:
    """De-energize to trip: a failed detection panel or a dead EP-1 opens the
    cellar relay and the supply shutoff relay (Denver IFC 5307 8.c.ii)."""
    bld = build(full, "building")
    _event(bld, "gas-detection", action="fail")
    bld.step(10.0)
    vals = bld.values()
    assert vals["building/vent.co2_shutoff_ok"] == 0.0
    assert vals["building/vent.co2_relay_ok"] == 0.0
    _event(bld, "gas-detection", action="restore")
    bld.step(10.0)
    assert bld.values()["building/vent.co2_shutoff_ok"] == 1.0
    bld.apply_input("power/bus.ep1.energized", False)
    bld.step(10.0)
    assert bld.values()["building/vent.co2_shutoff_ok"] == 0.0


def test_supply_valve_is_hardwired_through_the_relay(full: HelicsConfig) -> None:
    """The valve's solenoid is powered through the relay: a PLC (or attacker)
    write of 1 opens it only while the relay is healthy, and the limit switch
    tells the truth. The relay reads open until the building first reports."""
    utl = build(full, "utilities")
    assert isinstance(utl, UtilitiesFederate)
    assert utl.values()["utilities/co2relay.co2_ok"] is False
    utl.write("co2.main_valve", True)
    utl.step(1.0)
    assert utl.values()["utilities/co2.valve_open"] is False
    utl.apply_input("building/vent.co2_shutoff_ok", 1.0)
    utl.write("co2.main_valve", True)
    utl.step(1.0)
    vals = utl.values()
    assert vals["utilities/co2relay.co2_ok"] is True
    assert vals["utilities/co2.valve_open"] is True
    utl.apply_input("building/vent.co2_shutoff_ok", 0.0)
    utl.write("co2.main_valve", True)
    for _ in range(60):
        utl.step(1.0)
    vals = utl.values()
    assert vals["utilities/co2.valve_open"] is False
    assert vals["utilities/co2.supply_psig"] == 0.0
    assert utl.truth()["co2_supply"]["shutoffs"] == 1


def test_shutoff_stops_a_supply_leak_and_the_autopilot_latches(full: HelicsConfig) -> None:
    """The whole federation: a high leak at the filler reaches tier 3, the
    shutoff closes the bulk valve, the supply header bleeds down and the leak
    stops, the area clears, and the autopilot reopens the supply only after
    the relay has been healthy for CO2_AUTO_RESET_S (a latching shutoff)."""
    lf = LocalFederation(full)
    lf.run(60)
    utl = lf.feds["utilities"]
    bld = lf.feds["building"]
    assert isinstance(utl, UtilitiesFederate) and isinstance(bld, BuildingFederate)
    assert utl.co2.valve_open
    _event(bld, "co2-leak", zone="filler", rate=400.0, duration=600)
    for _ in range(1800):
        lf.step(1.0)
        if not utl.co2.valve_open:
            break
    assert not utl.co2.valve_open and utl.co2.latched
    for _ in range(1800):
        lf.step(1.0)
        if lf.values["building/vent.co2_shutoff_ok"] == 1.0:
            break
    assert bld.cel.areas["filler"].co2_ppm * 1.25 < 27000.0
    assert lf.values["building/vent.co2_shutoff_ok"] == 1.0
    assert not utl.co2.valve_open  # still latched
    lf.run(600)  # the leak's 600 s end, while the shutoff holds the supply closed
    lf.run(int(CO2_AUTO_RESET_S) + 30)
    assert utl.co2.valve_open and not utl.co2.latched
    assert utl.co2.shutoffs == 1  # the leak ended, so the reopened supply holds


def test_refrigerant_leak_detector_and_charge_record(full: HelicsConfig) -> None:
    """A broken line empties the charge: the detector passes the 1,000 ppm OEL,
    capacity falls and the controller trips on low suction; a recharge writes
    the 40 CFR 84 Subpart C record with its leak rate."""
    utl = build(full, "utilities")
    assert isinstance(utl, UtilitiesFederate)
    _event(utl, "refrigerant-leak", rate="high")
    for _ in range(15 * 60):
        utl.step(1.0)
    vals = utl.values()
    assert vals["utilities/chiller.refrig_ppm"] > 1000.0
    assert vals["utilities/chiller.fault"] is True
    _event(utl, "refrigerant-leak", action="repair")
    _event(utl, "refrigerant-leak", action="recharge")
    rec = utl.truth()["refrigerant"]["records"][-1]
    assert rec["added_lb"] > 0.4 * REFRIG_NAMEPLATE_LB
    assert rec["leak_rate_pct_yr"] > REFRIG_LEAK_TRIGGER_PCT and rec["exceeds_trigger"]
    utl.step(1.0)
    assert utl.values()["utilities/chiller.fault"] is False


def test_bess_thermal_event_trips_the_pcs(full: HelicsConfig) -> None:
    """A failing cell heats; the BMS trips the PCS at 140 °F, and past 176 °F
    the off-gas detector reads the venting cell (UL 9540A)."""
    pytest.importorskip("opendssdirect")
    pwr = build(full, "power")
    assert isinstance(pwr, PowerFederate)
    pwr.step(1.0)
    vals = pwr.values()
    assert vals["power/bess-pcs.cell_temp_f"] < 100.0
    assert vals["power/bess-pcs.fault"] is False
    _event(pwr, "bess-thermal")
    for _ in range(150):
        pwr.step(1.0)
    vals = pwr.values()
    assert vals["power/bess-pcs.cell_temp_f"] > 176.0
    assert vals["power/bess-pcs.offgas_ppm"] > 50.0
    assert vals["power/bess-pcs.fault"] is True
    assert vals["power/bess-pcs.p_kw"] == 0.0
    _event(pwr, "bess-thermal", action="clear")
    pwr.step(1.0)
    assert pwr.values()["power/bess-pcs.fault"] is False


def test_boiler_header_stays_under_the_section_iv_safety_valve(full: HelicsConfig) -> None:
    """The Section IV boiler runs at 12 psig; a 30 psig setpoint write (a PLC
    fault or an attacker) is capped by the safety valve at the 15 psig MAWP,
    and each lift is counted."""
    utl = build(full, "utilities")
    assert isinstance(utl, UtilitiesFederate)
    for _ in range(600):
        utl.step(1.0)
    assert 11.0 <= utl.blr.psig <= 13.5
    for _ in range(600):
        utl.write("boiler.enable", True)
        utl.write("boiler.pressure_sp_psig", 30.0)
        utl.step(1.0)
    assert utl.blr.psig == BOILER_MAWP_PSIG
    assert utl.truth()["utilities"]["boiler_safety_lifts"] >= 1


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"event": "co2-leak"}, "zone"),
        ({"event": "co2-leak", "zone": "cellar"}, "zone"),
        ({"event": "co2-leak", "zone": "tank", "rate": "huge"}, "rate"),
        ({"event": "gas-detection", "action": "reset"}, "fail or restore"),
        ({"event": "refrigerant-leak", "action": "vent"}, "leak, repair, or recharge"),
        ({"event": "bess-thermal", "action": "stop"}, "start or clear"),
    ],
)
def test_new_event_validation(body: dict[str, object], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        EventRequest.model_validate(body)


def test_new_events_are_accepted() -> None:
    for body in (
        {"event": "co2-leak", "zone": "draft", "rate": "low", "duration": 300},
        {"event": "gas-detection"},
        {"event": "refrigerant-leak", "rate": "low"},
        {"event": "bess-thermal"},
    ):
        EventRequest.model_validate(body)
