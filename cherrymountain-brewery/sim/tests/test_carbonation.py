"""Carbonation: the Zahm-Nagel fit, its altitude correction, and Henry's law (§17)."""

from __future__ import annotations

import pytest

from cherrymountain_sim.carbonation import (
    BrightTank,
    equilibrium_volumes,
    henry_volumes,
    site_gauge_psig,
    zahm_nagel_psig,
    zahm_nagel_volumes,
)


def test_design_values() -> None:
    """§9.3: 2.6 vol at 34 °F is about 10.2 psig at sea level and about
    12.8 psig on site (12.1 psia ambient)."""
    assert zahm_nagel_psig(34, 2.6) == pytest.approx(10.2, abs=0.05)
    assert site_gauge_psig(34, 2.6) == pytest.approx(12.8, abs=0.05)
    assert zahm_nagel_volumes(34, zahm_nagel_psig(34, 2.6)) == pytest.approx(2.6)


@pytest.mark.parametrize("temp_f", [32, 34, 38, 42, 46, 50])
@pytest.mark.parametrize("volumes", [2.0, 2.4, 2.6, 2.8, 3.0])
def test_zahm_nagel_agrees_with_henry(temp_f: float, volumes: float) -> None:
    """At the fit's absolute pressure, Henry's law (k_H(298 K) = 0.034
    mol/(kg bar), van 't Hoff 2400 K) gives the same volumes within 6 %."""
    psia = zahm_nagel_psig(temp_f, volumes) + 14.696
    assert henry_volumes(temp_f, psia) == pytest.approx(volumes, rel=0.06)


def test_altitude_correction_is_absolute_pressure() -> None:
    """The same beer needs the same absolute pressure anywhere: the site gauge
    reads higher by the ambient difference."""
    for t, v in ((34, 2.6), (38, 2.4)):
        sea_abs = zahm_nagel_psig(t, v) + 14.696
        site_abs = site_gauge_psig(t, v, 12.1) + 12.1
        assert site_abs == pytest.approx(sea_abs)
        assert equilibrium_volumes(t, site_abs) == pytest.approx(v)


def test_bbt_carbonates_toward_target() -> None:
    """With the stone open the dissolved CO2 rises and the headspace stays
    below the 15 psig relief; the CO2 drawn is reported."""
    bbt = BrightTank(
        "BBT201", phase="carbonating", temp_f=34, level_pct=90, co2_vol=1.8
    )
    bbt.write("co2_valve_pct", 40)
    bbt.write("valve", True)
    for _ in range(4 * 3600):
        bbt.step(1.0, glycol_f=28.0, ambient_f=65.0)
    assert bbt.co2_vol > 2.2
    assert bbt.pressure_psia < 12.1 + 15.0 + 0.01
    assert bbt.co2_slpm > 0
    assert bbt.temp_f < 36.0


def test_serving_bbt_holds_top_pressure() -> None:
    """The regulator holds the head pressure while the filler draws beer."""
    bbt = BrightTank("BBT202", phase="serving", temp_f=34, level_pct=70, co2_vol=2.6)
    p0 = bbt.pressure_psia
    for _ in range(1800):
        bbt.step(1.0, glycol_f=28.0, ambient_f=65.0, draw_gpm=3.0)
    assert bbt.level_pct < 70
    assert bbt.pressure_psia == pytest.approx(p0, abs=0.5)
    assert bbt.regulator_slpm > 0
