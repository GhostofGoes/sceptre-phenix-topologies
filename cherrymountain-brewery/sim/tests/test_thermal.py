"""Jacket thermal model against hand calculations."""

from __future__ import annotations

import math

import pytest

from cherrymountain_sim.thermal import (
    JacketedVessel,
    Zone,
    c_to_f,
    f_to_c,
    single_zone_steady_state,
)


def zone(name: str = "side", **kw: float | bool) -> Zone:
    """A 30 bbl side zone (2,745 kg wort, UA 1,050 W/K) with overrides."""
    base: dict[str, float | bool | str] = {
        "name": name,
        "liquid_kg": 2745.0,
        "ua_jacket_w_k": 1050.0,
        "ua_ambient_w_k": 12.0,
        "temp_c": 19.0,
        "steel_kg": 600.0,
        "valve_open": True,
    }
    base.update(kw)
    return Zone.model_validate(base)


def run(
    vessel: JacketedVessel, hours: float, q_w: float, t_gly: float, t_amb: float
) -> None:
    """Step the vessel for ``hours`` of wall time at 1 s."""
    for _ in range(int(hours * 3600)):
        vessel.step(1.0, q_w, t_gly, t_amb)


def test_single_zone_steady_state_matches_hand_calc() -> None:
    """0 = Q - UA(T - Tg) - UAamb(T - Ta) with the valve held open.

    Hand calc: Q = 670 W (peak real-time fermentation heat), UA = 1,050 W/K,
    Tg = 28 °F (-2.22 °C), UAamb = 12 W/K, Ta = 68 °F (20 °C):
    T = (670 + 1050*(-2.222) + 12*20) / 1062 = -1.340 °C.
    """
    t_gly, t_amb, q = f_to_c(28.0), f_to_c(68.0), 670.0
    hand = (q + 1050.0 * t_gly + 12.0 * t_amb) / (1050.0 + 12.0)
    assert hand == pytest.approx(-1.340, abs=0.005)
    assert single_zone_steady_state(q, 1050.0, t_gly, 12.0, t_amb) == pytest.approx(
        hand
    )
    v = JacketedVessel([zone()])
    run(v, 30.0, q, t_gly, t_amb)  # about 10 time constants
    assert v.zones[0].temp_c == pytest.approx(hand, abs=0.01)
    # At steady state the jacket removes Q plus the ambient gain.
    duty = v.zones[0].jacket_duty_w(t_gly)
    assert duty == pytest.approx(q + 12.0 * (t_amb - hand), rel=1e-3)


def test_time_constant() -> None:
    """After one time constant the zone is 63.2% of the way to steady state."""
    z = zone(ua_ambient_w_k=0.0)
    v = JacketedVessel([z])
    tau_s = z.heat_capacity / z.ua_jacket_w_k
    t0, t_gly = z.temp_c, -2.0
    for _ in range(int(tau_s)):
        v.step(1.0, 0.0, t_gly, 20.0)
    frac = (t0 - z.temp_c) / (t0 - t_gly)
    assert frac == pytest.approx(1 - math.exp(-1), abs=0.002)


def test_two_zone_steady_state_with_mixing() -> None:
    """Side open, cone closed: solve the 2x2 balance by hand.

    Side: Q_s - UA_s(T_s - Tg) - UAm(T_s - T_c) - a_s(T_s - Ta) = 0
    Cone: Q_c - UAm(T_c - T_s) - a_c(T_c - Ta) = 0
    """
    total, ua_s, uam, a_s, a_c = 670.0, 1050.0, 400.0, 12.0, 4.0
    tg, ta = -2.0, 20.0
    side = zone("side", ua_ambient_w_k=a_s)
    cone = zone(
        "cone",
        liquid_kg=915.0,
        ua_jacket_w_k=360.0,
        ua_ambient_w_k=a_c,
        steel_kg=200.0,
        valve_open=False,
    )
    v = JacketedVessel([side, cone], ua_mix_w_k=uam)
    # Fermentation heat splits by liquid mass: 2745 / 3660 = 0.75.
    q_s = total * 0.75
    q_c = total - q_s
    # Cone: T_c = (q_c + uam*T_s + a_c*ta) / (uam + a_c); substitute into side.
    k = uam / (uam + a_c)
    m = (q_c + a_c * ta) / (uam + a_c)
    t_s = (q_s + ua_s * tg + a_s * ta + uam * m) / (ua_s + a_s + uam - uam * k)
    t_c = (q_c + uam * t_s + a_c * ta) / (uam + a_c)
    run(v, 40.0, total, tg, ta)
    assert side.temp_c == pytest.approx(t_s, abs=0.02)
    assert cone.temp_c == pytest.approx(t_c, abs=0.02)


def test_energy_conserved_with_valves_closed() -> None:
    """Closed valves, no ambient loss: C dT = Q dt."""
    z = zone(valve_open=False, ua_ambient_w_k=0.0)
    v = JacketedVessel([z])
    t0 = z.temp_c
    run(v, 1.0, 670.0, -2.0, 20.0)
    assert (z.temp_c - t0) * z.heat_capacity == pytest.approx(670.0 * 3600, rel=1e-6)


def test_unit_conversions() -> None:
    """°F <-> °C."""
    assert f_to_c(32.0) == 0.0
    assert c_to_f(f_to_c(66.0)) == pytest.approx(66.0)
