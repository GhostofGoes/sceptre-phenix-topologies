"""Brewhouse physics against hand calculations and published values (§17)."""

from __future__ import annotations

import pytest

from cherrymountain_sim import recipe
from cherrymountain_sim.brewing import (
    boiling_point_elevation_k,
    c_to_f,
    counterflow,
    counterflow_effectiveness,
    hop_stand_ibu,
    isomerization_rate_ratio,
    mash_profile,
    strike_temperature,
    tinseth_ibu,
    tinseth_utilization,
    water_boiling_point_c,
    wort_boiling_point_c,
)

SITE_KPA: float = 83.4


def test_boiling_points_on_site() -> None:
    """§3.1: water about 94.6 °C (202.3 °F); 16 °P wort about 94.9 °C (202.8 °F)."""
    assert water_boiling_point_c(101.325) == pytest.approx(100.0, abs=0.02)
    assert water_boiling_point_c(SITE_KPA) == pytest.approx(94.6, abs=0.1)
    assert boiling_point_elevation_k(16.1) == pytest.approx(0.3, abs=0.05)
    wort = wort_boiling_point_c(SITE_KPA, 16.1)
    assert wort == pytest.approx(94.9, abs=0.1)
    assert c_to_f(wort) == pytest.approx(202.8, abs=0.2)


def test_strike_water() -> None:
    """Palmer: 1.25 qt/lb, grain 70 °F, target 152 °F -> 165.1 °F; the metric
    form (0.41, L/kg) agrees for the same mash in °C."""
    assert strike_temperature(152, 70, 1.25) == pytest.approx(165.12)
    assert strike_temperature(152, 70, 1.25, exact=True) == pytest.approx(
        164.92, abs=0.01
    )
    metric_c = strike_temperature(66.67, 21.11, 1.25 * 2.0864, metric=True)
    assert c_to_f(metric_c) == pytest.approx(165.12, abs=0.3)
    with pytest.raises(ValueError):
        strike_temperature(152, 70, 0)


def test_tinseth_matches_brewtarget_and_brewday() -> None:
    """Sea-level Tinseth, as Brewtarget and brewday compute it (metric:
    IBU = U x AA x g x 1000 / L). Reference values from the published formula:
    1 oz of 6 % hops for 60 min in 5 gal at 1.050 is 20.7 IBU; utilization
    at 1.050/60 min is 0.2307 and at 1.080/15 min 0.0874."""
    assert tinseth_utilization(1.050, 60) == pytest.approx(0.2307, abs=1e-4)
    assert tinseth_utilization(1.080, 15) == pytest.approx(0.0874, abs=1e-4)
    assert tinseth_ibu(0.06, 28.3495, 18.9271, 1.050, 60) == pytest.approx(
        20.73, abs=0.02
    )
    assert tinseth_utilization(1.050, 0) == 0.0


def test_altitude_derating() -> None:
    """§9.3: the rate ratio is about 0.69 (Hosom) and 0.63-0.65 (Malowicki and
    Shellhammer); the IBU loss is about 11 % / 13-14 % for 60-minute and
    26 % / 31 % for 10-minute additions."""
    boil = wort_boiling_point_c(SITE_KPA, 15.5)
    hosom = isomerization_rate_ratio(boil, "hosom")
    malowicki = isomerization_rate_ratio(boil, "malowicki")
    assert hosom == pytest.approx(0.69, abs=0.01)
    assert 0.63 <= malowicki <= 0.65
    for minutes, lo, hi in ((60, 0.10, 0.14), (10, 0.26, 0.32)):
        base = tinseth_utilization(1.063, minutes)
        for ratio in (hosom, malowicki):
            loss = 1 - tinseth_utilization(1.063, minutes, 0.04 * ratio) / base
            assert lo <= loss <= hi, (minutes, ratio, loss)
    assert isomerization_rate_ratio(100.0) == pytest.approx(1.0)


def test_hop_stand_is_slower_than_boiling() -> None:
    """A 20-minute stand at 180 °F isomerizes like about 5.4 boil minutes."""
    stand = hop_stand_ibu(0.12, 1000, 1000, 1.063, [82.2] * 20)
    boil = tinseth_ibu(0.12, 1000, 1000, 1.063, 5.4)
    assert stand == pytest.approx(boil, rel=0.02)


def test_recipe_meets_its_target_on_site() -> None:
    """Fire of the Phenix: 68 IBU on site; the same schedule at sea level would
    overshoot by about 10 % (§3.2)."""
    r = recipe.load()
    assert r.name == "Fire of the Phenix"
    assert r.ibu() == pytest.approx(68.0, abs=0.5)
    assert r.ibu(101.325) == pytest.approx(r.ibu() * 1.10, rel=0.02)
    assert r.og_plato == pytest.approx(16.1, abs=0.1)
    assert [h.use for h in r.hops].count("dry-hop") == 3


def test_hx_effectiveness_hand_calc() -> None:
    """ε-NTU: NTU 2, Cr 0.5 -> ε = (1 - e^-1)/(1 - 0.5 e^-1) = 0.7746; balanced
    flow ε = NTU/(1 + NTU)."""
    assert counterflow_effectiveness(2.0, 0.5) == pytest.approx(0.7746, abs=1e-4)
    assert counterflow_effectiveness(3.0, 1.0) == pytest.approx(0.75)
    assert counterflow_effectiveness(0.0, 0.3) == 0.0
    with pytest.raises(ValueError):
        counterflow_effectiveness(1.0, 1.5)


def test_hx_outlets_hand_calc() -> None:
    """Knockout stage 1: wort 94.9 °C at C = 6,685 W/K against 14.4 °C water
    at 7,250 W/K, UA 36,000 W/K. NTU = 5.385, Cr = 0.9221, NTU(1 - Cr) =
    0.4197, ε = (1 - 0.6573)/(1 - 0.9221 x 0.6573) = 0.8700, so Q = 0.8700 x
    6,685 x 80.5 = 468.2 kW, wort out 24.87 °C, water out 78.98 °C."""
    r = counterflow(94.9, 14.4, 6685.0, 7250.0, 36000.0)
    assert r.effectiveness == pytest.approx(0.8700, abs=0.0005)
    assert r.duty_w == pytest.approx(468.2e3, rel=0.002)
    assert r.hot_out == pytest.approx(24.87, abs=0.05)
    assert r.cold_out == pytest.approx(78.98, abs=0.05)
    # energy balance
    assert 6685.0 * (94.9 - r.hot_out) == pytest.approx(7250.0 * (r.cold_out - 14.4))


def test_hx_agrees_with_ht() -> None:
    """Cross-check with CalebBell's ht (DESIGN.md §9.3) when it is installed."""
    ht = pytest.importorskip("ht")
    for ntu, cr in ((0.5, 0.2), (2.0, 0.5), (5.4, 0.92), (3.0, 1.0)):
        assert counterflow_effectiveness(ntu, cr) == pytest.approx(
            ht.effectiveness_from_NTU(ntu, cr, subtype="counterflow"), rel=1e-9
        )


def test_mash_fermentability_falls_with_temperature() -> None:
    """Saarni et al. (2020) trend: hotter mashes are less fermentable; 152 °F
    for 60 min gives about 0.75 with complete conversion."""
    ferm = {t: mash_profile([(t, 60)]).fermentability for t in (63.0, 66.7, 70.0, 72.0)}
    assert ferm[63.0] > ferm[66.7] > ferm[70.0] > ferm[72.0]
    st = mash_profile([(66.7, 60)])
    assert st.fermentability == pytest.approx(0.76, abs=0.04)
    assert st.conversion > 0.99
    assert mash_profile([(50.0, 60)]).conversion < 0.2  # below gelatinization
