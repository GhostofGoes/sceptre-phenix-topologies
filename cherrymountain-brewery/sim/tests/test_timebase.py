"""Time-compression invariants (DESIGN.md §9.4) and cellar mass balances."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cherrymountain_sim.cellar import (
    CO2_G_PER_MOL,
    Cellar,
    Environment,
    Fermenter,
    InitialState,
)
from cherrymountain_sim.fermentation import CO2_PER_ETHANOL, HEAT_KJ_PER_KG_EXTRACT
from cherrymountain_sim.timebase import TimeBase


def fermenting(sim_hours: float = 24.0) -> Fermenter:
    """A fermenter filled, pitched, and advanced ``sim_hours``."""
    fv = Fermenter("FV101", Environment())
    fv.fill_and_pitch(66.0)
    fv.advance_kinetics(sim_hours, 66.0)
    return fv


def test_default_compression_one_day_per_half_hour() -> None:
    """48x: one simulated day takes 30 wall minutes."""
    tb = TimeBase()
    assert tb.compression == 48.0
    assert tb.wall_seconds(24.0) == pytest.approx(1800.0)
    assert tb.kinetic_hours(1800.0) == pytest.approx(24.0)
    with pytest.raises(ValidationError):
        TimeBase(compression=0.5)


def test_kinetic_state_compressed() -> None:
    """N wall seconds at 48x advance kinetics like 48N seconds at 1x."""
    a, b = fermenting(), fermenting()
    for _ in range(60):
        a.step(1.0, TimeBase(compression=48.0))
    for _ in range(60 * 48):
        b.step(1.0, TimeBase(compression=1.0))
    assert a.state is not None and b.state is not None
    # Same kinetic time; temperatures differ slightly (thermal is wall time).
    assert a.state.sugar == pytest.approx(b.state.sugar, rel=2e-3)
    assert a.state.ethanol == pytest.approx(b.state.ethanol, rel=2e-3)


def test_exported_fluxes_do_not_scale_with_compression() -> None:
    """For the same kinetic state, fluxes are real-time-equivalent."""
    fluxes = {
        c: fermenting(48.0).step(1.0, TimeBase(compression=c)) for c in (1.0, 48.0)
    }
    assert fluxes[48.0].co2_gen_gps == pytest.approx(fluxes[1.0].co2_gen_gps)
    assert fluxes[48.0].heat_w == pytest.approx(fluxes[1.0].heat_w)
    # Real-world magnitudes, not 48x (which would be ~20 g/s and ~30 kW).
    assert 0.1 < fluxes[48.0].co2_gen_gps < 1.0
    assert 100.0 < fluxes[48.0].heat_w < 1500.0


def test_co2_and_heat_mass_balance_through_compression() -> None:
    """Integrated exported CO2 and heat times the compression equal the
    stoichiometric totals from the kinetic state (DESIGN.md §9.3)."""
    tb = TimeBase()
    fv = fermenting(0.0)
    assert fv.state is not None
    s0, e0 = fv.state.sugar, fv.state.ethanol
    heat_j = 0.0
    for _ in range(int(tb.wall_seconds(6 * 24))):  # six sim days
        heat_j += fv.step(1.0, tb).heat_w
    vol = fv.cfg.volume_l
    co2_state_g = CO2_PER_ETHANOL * (fv.state.ethanol - e0) * vol
    assert fv.co2_generated_g * tb.compression == pytest.approx(co2_state_g, rel=5e-3)
    consumed_kg = (s0 - fv.state.sugar) * vol / 1000.0
    assert heat_j * tb.compression / 1e3 == pytest.approx(
        consumed_kg * HEAT_KJ_PER_KG_EXTRACT, rel=5e-3
    )


def test_headspace_co2_balance() -> None:
    """CO2 generated = CO2 vented + CO2 accumulated in the headspace."""
    fv = fermenting(36.0)
    fv.spund_sp_psia = 14.0
    mol0 = fv.gas_mol
    for _ in range(1800):
        fv.step(1.0, TimeBase())
    accumulated_g = (fv.gas_mol - mol0) * CO2_G_PER_MOL
    assert fv.co2_vented_g > 0 and fv.spund_open
    assert fv.co2_generated_g == pytest.approx(
        fv.co2_vented_g + accumulated_g, rel=1e-6
    )
    assert fv.pressure_psia == pytest.approx(14.0, abs=1e-6)


def test_clock_runs_in_sim_time() -> None:
    """``cellar/clock.sim_hours`` advances compression x wall time."""
    c = Cellar(["FV101"], initial={"FV101": InitialState(phase="fermenting")})
    for _ in range(900):
        c.step(1.0)
    assert c.sim_hours == pytest.approx(12.0)


def test_hop_creep_extra_attenuation() -> None:
    """Dry hop 2 lb/bbl on day 5: 0.2-0.5 °P extra attenuation over 3-5 days."""
    control, hopped = fermenting(120.0), fermenting(120.0)
    hopped.dry_hop(2.0)
    assert hopped.gravity_plato == pytest.approx(control.gravity_plato)  # no jump
    for fv in (control, hopped):
        fv.advance_kinetics(4 * 24.0, 66.0)
    assert 0.2 <= control.gravity_plato - hopped.gravity_plato <= 0.5
