"""Fermentation kinetics: published values, ale timing, mass balance, shape."""

from __future__ import annotations

import math

import pytest
from scipy.integrate import solve_ivp

from cherrymountain_sim.fermentation import (
    ALE,
    CO2_PER_ETHANOL,
    LAGER,
    Arrhenius,
    Fermentation,
    FermentState,
    KineticParams,
    Wort,
    pitch,
    plato_from_sg,
    sg_from_plato,
)

WORT: Wort = Wort()
DT_H: float = 0.05


def f_to_k(t_f: float) -> float:
    """Fahrenheit to kelvin."""
    return (t_f - 32.0) / 1.8 + 273.15


def simulate(
    params: KineticParams, t_k: float, s0: float, x0: float, hours: float
) -> list[tuple[float, FermentState]]:
    """Isothermal run, recording (hours, state) every step."""
    f = Fermentation(s0, params)
    st = pitch(s0, x0)
    out = [(0.0, st)]
    t = 0.0
    while t < hours - 1e-9:
        st = f.step(st, t_k, DT_H)
        t += DT_H
        out.append((t, st))
    return out


def test_mu_e0_typo_resolved() -> None:
    """DESIGN.md §9.3: μ_e0(15 °C) ≈ 0.3 1/h with B = -1267.24."""
    t15 = 288.15
    assert LAGER.mu_e0(t15) == pytest.approx(0.32, abs=0.03)
    assert Arrhenius(a=3.27, b=-12667.24)(t15) < 1e-15  # the printed typo


@pytest.mark.parametrize(
    ("t_c", "e_max", "ea", "sugar_time_h"),
    [
        (11.0, 50.5, 0.11, 117.0),
        (13.0, 56.8, 0.57, 105.0),
    ],
)
def test_lager_matches_rodman_table4(
    t_c: float, e_max: float, ea: float, sugar_time_h: float
) -> None:
    """Isothermal lager runs reproduce Rodman and Gerogiorgis (2016) Table 4."""
    run = simulate(LAGER, t_c + 273.15, 130.0, 4.0, 250.0)
    assert max(s.ethanol for _, s in run) == pytest.approx(e_max, abs=1.0)
    assert run[-1][1].ethyl_acetate == pytest.approx(ea, rel=0.15)
    # Sugar consumption time: down to 0.65 g/L (0.5% of S0).
    t_sugar = next(t for t, s in run if s.sugar <= 0.65)
    assert t_sugar == pytest.approx(sugar_time_h, abs=10.0)


def test_lager_15c_stalls_like_published() -> None:
    """At 15 °C the published model leaves about 6 g/L sugar at 160 h."""
    run = simulate(LAGER, 288.15, 130.0, 4.0, 160.0)
    assert max(s.ethanol for _, s in run) == pytest.approx(60.6, abs=1.0)
    assert run[-1][1].sugar == pytest.approx(6.07, abs=2.5)


def test_rk4_matches_scipy() -> None:
    """The fixed-step RK4 agrees with an adaptive reference solver."""
    f = Fermentation(130.0, LAGER)
    t_k = 285.15
    fields = ("x_lag", "x_act", "x_dead", "sugar", "ethanol")

    def rhs(lag_done: bool):  # noqa: ANN202 - closure for solve_ivp
        def inner(_t: float, y: list[float]) -> list[float]:
            st = FermentState(**dict(zip(fields, y, strict=True)), lag_done=lag_done)
            r = f.rates(st, t_k)
            return [getattr(r, n) for n in fields]

        return inner

    def lag_end(_t: float, y: list[float]) -> float:
        return y[1] - 0.5 * (y[0] + y[1] + y[2])

    lag_end.terminal = True  # type: ignore[attr-defined]
    p = pitch(130.0)
    y0 = [getattr(p, n) for n in fields]
    a = solve_ivp(rhs(False), (0, 100), y0, events=lag_end, rtol=1e-9, atol=1e-11)
    b = solve_ivp(rhs(True), (a.t[-1], 100), a.y[:, -1], rtol=1e-9, atol=1e-11)
    ref = dict(zip(fields, b.y[:, -1], strict=True))
    got = simulate(LAGER, t_k, 130.0, 4.0, 100.0)[-1][1]
    assert got.sugar == pytest.approx(ref["sugar"], rel=5e-3, abs=0.05)
    assert got.ethanol == pytest.approx(ref["ethanol"], rel=5e-3)


@pytest.mark.parametrize("t_f", [66.0, 68.0, 70.0])
def test_ale_attenuation_timing(t_f: float) -> None:
    """DESIGN.md §9.3: 16.1 °P to about 3 °P in about 5-6 days at 66-70 °F."""
    run = simulate(ALE, f_to_k(t_f), WORT.fermentable, 2.5, 14 * 24.0)
    gravity = [(t, WORT.gravity(s)) for t, s in run]
    fg = gravity[-1][1]
    assert 2.8 <= fg <= 3.5
    done_h = next(t for t, g in gravity if g <= fg + 0.2)
    assert 5.0 * 24 <= done_h <= 6.0 * 24
    assert gravity[0][1] == pytest.approx(16.1, abs=0.01)


def test_ethanol_yield_and_co2_mass_balance() -> None:
    """Ethanol per sugar stays under the Gay-Lussac limit; CO2 = 0.955 E."""
    run = simulate(ALE, f_to_k(66.0), WORT.fermentable, 2.5, 14 * 24.0)
    first, last = run[0][1], run[-1][1]
    consumed = first.sugar - last.sugar
    assert 0.42 <= last.ethanol / consumed <= 92.14 / 180.16  # 0.511 theoretical
    # Integrate the CO2 rate (0.955 dE/dt) with the trapezoid rule.
    f = Fermentation(WORT.fermentable, ALE)
    rates = [CO2_PER_ETHANOL * f.rates(s, f_to_k(66.0)).ethanol for _, s in run]
    co2 = sum((a + b) / 2 * DT_H for a, b in zip(rates, rates[1:], strict=False))
    assert co2 == pytest.approx(CO2_PER_ETHANOL * last.ethanol, rel=2e-3)
    assert Wort.abv(last.ethanol) == pytest.approx(6.6, abs=0.6)  # recipe ~7.1%


def test_curve_shape() -> None:
    """Lag, then a sigmoid gravity drop; single CO2 peak; diacetyl rises then falls."""
    t_k = f_to_k(66.0)
    run = simulate(ALE, t_k, WORT.fermentable, 2.5, 14 * 24.0)
    f = Fermentation(WORT.fermentable, ALE)
    gravity = [WORT.gravity(s) for _, s in run]
    assert all(b <= a + 1e-9 for a, b in zip(gravity, gravity[1:], strict=False))
    lag_idx = int(12 / DT_H)
    assert gravity[0] - gravity[lag_idx] < 0.5  # lag phase
    co2 = [f.rates(s, t_k).ethanol for _, s in run]
    peak = max(range(len(co2)), key=co2.__getitem__)
    assert 24 <= run[peak][0] <= 72  # active fermentation day 1-3
    rising, falling = co2[: peak + 1], co2[peak:]
    assert all(b >= a - 1e-9 for a, b in zip(rising, rising[1:], strict=False))
    assert all(b <= a + 1e-9 for a, b in zip(falling, falling[1:], strict=False))
    vdk = [s.diacetyl for _, s in run]
    vpeak = max(vdk)
    assert vdk[0] == 0.0 and vpeak > 0
    assert vdk[int(8 * 24 / DT_H)] < 0.25 * vpeak  # reabsorbed (diacetyl rest)
    ethanol = [s.ethanol for _, s in run]
    assert all(b >= a - 1e-12 for a, b in zip(ethanol, ethanol[1:], strict=False))
    x_act = [s.x_act for _, s in run]
    xpeak = max(range(len(x_act)), key=x_act.__getitem__)
    assert 0 < xpeak < len(x_act) - 1 and x_act[-1] < x_act[xpeak]


def test_gravity_conversions_round_trip() -> None:
    """°P <-> SG conversions agree (1.066 is about 16.1 °P)."""
    assert sg_from_plato(16.1) == pytest.approx(1.066, abs=0.001)
    for p in (0.0, 3.1, 10.0, 16.1, 20.0):
        assert plato_from_sg(sg_from_plato(p)) == pytest.approx(p, abs=0.05)
    assert WORT.residual_extract > 0 and math.isfinite(WORT.fermentable)
