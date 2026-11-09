"""Brewhouse physics (DESIGN.md §9.3): boiling point, strike water,
bitterness, the knockout heat exchanger, and mash conversion.

Every function is pure and unit tested against hand calculations or published
values; :mod:`cherrymountain_sim.brewhouse` uses them in the federate.

**Boiling point.** Water boils where its vapor pressure equals the ambient
pressure. The Antoine equation for water (1-100 °C; A = 8.07131, B = 1730.63,
C = 233.426, mmHg and °C) gives 94.6 °C at 83.4 kPa. Dissolved extract raises
the wort's boiling point: ΔT = K_b·m with K_b = 0.512 K·kg/mol and the extract
counted as maltose (342.3 g/mol), about 0.29 K at 16.1 °P, so wort boils at
about 94.9 °C (202.9 °F) on site.

**Strike water.** T_w = (0.2/r)·(T_t − T_g) + T_t with r in qt/lb (Palmer);
the derived constant is 0.197 (grain heat capacity 0.4 of water, in qt/lb
units), and the metric form uses 0.41 with r in L/kg. The constant is a ratio
of temperature differences, so °F or °C work alike.

**Bitterness.** Tinseth: U = 1.65·0.000125^(G−1) · (1 − e^(−k·t))/4.15 with
k = 0.04 min⁻¹ and IBU = U × added α-acids in mg/L. G is the average kettle
gravity during the boil. At altitude the isomerization is slower: k is
multiplied by the Arrhenius rate ratio between the local boiling point and
100 °C, either from Hosom's mIBU temperature function, U_rel(T) =
2.39×10¹¹·e^(−9773/T), or from Malowicki and Shellhammer's activation energy
(99.7 kJ/mol). The whirlpool hop stand integrates U_rel(T) over the stand.

**Heat exchanger.** Counterflow ε-NTU: ε = (1 − e^(−NTU(1−Cr))) /
(1 − Cr·e^(−NTU(1−Cr))) for Cr < 1 and NTU/(1 + NTU) for Cr = 1.

**Mash.** A two-enzyme lumped model in the spirit of Brandam et al. (2003):
starch gelatinizes above about 60 °C; α-amylase (optimum 68-72 °C, slow to
denature) cuts gelatinized starch into dextrins and some fermentable sugar;
β-amylase (optimum 60-65 °C, quick to denature) cuts dextrins into maltose.
Each enzyme has an activity peak and first-order thermal deactivation. The
constants are tuned (not fitted to data) to reproduce the documented trend
that fermentability falls as the mash temperature rises, as in Saarni, Miller
and Block (2020).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

KPA_PER_MMHG: float = 0.133322368
#: Antoine constants for water, mmHg and °C (1-100 °C).
ANTOINE: tuple[float, float, float] = (8.07131, 1730.63, 233.426)
#: Ebullioscopic constant of water, K·kg/mol.
KB_WATER: float = 0.512
MALTOSE_G_PER_MOL: float = 342.3
#: Tinseth's isomerization time constant at 100 °C, 1/min.
TINSETH_K: float = 0.04
#: mg/L of α-acid per (fraction AA × oz / gal), Tinseth's 7490.
MG_L_PER_OZ_GAL: float = 7490.0
#: Hosom's mIBU temperature function: U_rel(T) = A·exp(-B/T), T in K.
HOSOM_A: float = 2.39e11
HOSOM_B: float = 9773.0
#: Malowicki and Shellhammer (2005) isomerization activation energy, J/mol.
MALOWICKI_EA: float = 99.7e3
R_GAS: float = 8.314462


def f_to_c(t_f: float) -> float:
    """Fahrenheit to Celsius."""
    return (t_f - 32.0) / 1.8


def c_to_f(t_c: float) -> float:
    """Celsius to Fahrenheit."""
    return t_c * 1.8 + 32.0


def water_boiling_point_c(p_kpa: float) -> float:
    """Saturation temperature of water at ``p_kpa`` (Antoine), °C."""
    a, b, c = ANTOINE
    return b / (a - math.log10(p_kpa / KPA_PER_MMHG)) - c


def boiling_point_elevation_k(plato: float) -> float:
    """Boiling-point elevation of wort at ``plato`` (extract as maltose), K."""
    extract = plato / 100.0
    molality = extract / (1.0 - extract) * 1000.0 / MALTOSE_G_PER_MOL
    return KB_WATER * molality


def wort_boiling_point_c(p_kpa: float, plato: float) -> float:
    """Local wort boiling point, °C (DESIGN.md §6.3 boil detection)."""
    return water_boiling_point_c(p_kpa) + boiling_point_elevation_k(plato)


def strike_temperature(
    target: float,
    grain: float,
    ratio: float,
    *,
    metric: bool = False,
    exact: bool = False,
) -> float:
    """Strike water temperature (°F or °C alike).

    ``ratio`` is the liquor-to-grist ratio in qt/lb, or in L/kg with
    ``metric``. ``exact`` uses the derived 0.197 instead of Palmer's 0.2.
    """
    if ratio <= 0:
        raise ValueError("ratio must be positive")
    constant = 0.41 if metric else (0.197 if exact else 0.2)
    return constant / ratio * (target - grain) + target


# ---------------------------------------------------------------------------
# Bitterness
# ---------------------------------------------------------------------------


def tinseth_bigness(gravity: float) -> float:
    """Tinseth's gravity (bigness) factor."""
    return 1.65 * 0.000125 ** (gravity - 1.0)


def tinseth_utilization(gravity: float, minutes: float, k: float = TINSETH_K) -> float:
    """Tinseth utilization for a boil of ``minutes`` at rate ``k`` (1/min)."""
    return tinseth_bigness(gravity) * (1.0 - math.exp(-k * minutes)) / 4.15


def alpha_mg_l(alpha: float, grams: float, liters: float) -> float:
    """Added α-acids, mg/L (``alpha`` as a fraction, e.g. 0.14)."""
    return alpha * grams * 1000.0 / liters


def tinseth_ibu(
    alpha: float,
    grams: float,
    liters: float,
    gravity: float,
    minutes: float,
    k: float = TINSETH_K,
) -> float:
    """IBU of one boil addition (Tinseth, metric)."""
    return tinseth_utilization(gravity, minutes, k) * alpha_mg_l(alpha, grams, liters)


def hosom_relative_utilization(temp_k: float) -> float:
    """Hosom's mIBU relative utilization, 1.0 at 100 °C."""
    return HOSOM_A * math.exp(-HOSOM_B / temp_k)


def isomerization_rate_ratio(
    boil_c: float, method: Literal["hosom", "malowicki"] = "hosom"
) -> float:
    """Isomerization rate at ``boil_c`` relative to 100 °C."""
    t = boil_c + 273.15
    if method == "hosom":
        return hosom_relative_utilization(t) / hosom_relative_utilization(373.15)
    return math.exp(-MALOWICKI_EA / R_GAS * (1.0 / t - 1.0 / 373.15))


def hop_stand_ibu(
    alpha: float,
    grams: float,
    liters: float,
    gravity: float,
    temps_c: Iterable[float],
    step_min: float = 1.0,
) -> float:
    """IBU of a whirlpool/hop-stand addition (Hosom mIBU).

    ``temps_c`` is the wort temperature at each ``step_min`` of the stand;
    the effective boil time is the time integral of U_rel(T).
    """
    t_eff = sum(hosom_relative_utilization(t + 273.15) for t in temps_c) * step_min
    return tinseth_ibu(alpha, grams, liters, gravity, t_eff)


# ---------------------------------------------------------------------------
# Heat exchanger
# ---------------------------------------------------------------------------


def counterflow_effectiveness(ntu: float, cr: float) -> float:
    """ε of a counterflow exchanger for NTU and Cr = C_min/C_max."""
    if ntu <= 0:
        return 0.0
    if not 0.0 <= cr <= 1.0:
        raise ValueError("Cr must be in [0, 1]")
    if math.isclose(cr, 1.0):
        return ntu / (1.0 + ntu)
    e = math.exp(-ntu * (1.0 - cr))
    return (1.0 - e) / (1.0 - cr * e)


class HxResult(BaseModel):
    """Outlet temperatures and duty of one exchanger pass."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    hot_out: float
    cold_out: float
    duty_w: float
    effectiveness: float


def counterflow(
    hot_in: float, cold_in: float, c_hot_w_k: float, c_cold_w_k: float, ua_w_k: float
) -> HxResult:
    """Counterflow outlet temperatures (°C) from capacity rates and UA."""
    if c_hot_w_k <= 0 or c_cold_w_k <= 0:
        return HxResult(hot_out=hot_in, cold_out=cold_in, duty_w=0.0, effectiveness=0.0)
    c_min, c_max = sorted((c_hot_w_k, c_cold_w_k))
    eps = counterflow_effectiveness(ua_w_k / c_min, c_min / c_max)
    q = eps * c_min * (hot_in - cold_in)
    return HxResult(
        hot_out=hot_in - q / c_hot_w_k,
        cold_out=cold_in + q / c_cold_w_k,
        duty_w=q,
        effectiveness=eps,
    )


# ---------------------------------------------------------------------------
# Mash conversion
# ---------------------------------------------------------------------------


class MashParams(BaseModel):
    """Tuned constants of the two-enzyme mash model (rates per minute)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    gel_mid_c: float = 61.0  # starch gelatinization midpoint
    gel_width_c: float = 1.5
    gel_rate: float = 0.25  # 1/min once gelatinizable
    alpha_k: float = 0.20  # starch -> dextrin at full activity
    alpha_opt_c: float = 70.0
    alpha_width_c: float = 8.0
    alpha_sugar_share: float = 0.45  # fermentable share of α products
    beta_k: float = 0.06  # dextrin -> maltose at full activity
    beta_opt_c: float = 63.0
    beta_width_c: float = 9.0
    beta_half_life_63_min: float = 30.0
    alpha_half_life_70_min: float = 90.0
    deactivation_q10: float = 3.0


class MashState(BaseModel):
    """Mash composition as fractions of the potential extract, plus enzymes."""

    starch: float = 1.0  # native (ungelatinized) starch
    gelatinized: float = 0.0
    dextrin: float = 0.0
    sugar: float = 0.0
    alpha: float = 1.0  # α-amylase activity (relative)
    beta: float = 1.0  # β-amylase activity (relative)

    @property
    def conversion(self) -> float:
        """Share of the starch converted to soluble extract."""
        return self.dextrin + self.sugar

    @property
    def fermentability(self) -> float:
        """Fermentable share of the soluble extract (0 before conversion)."""
        soluble = self.dextrin + self.sugar
        return self.sugar / soluble if soluble > 0 else 0.0


def _peak(t: float, opt: float, width: float) -> float:
    return math.exp(-(((t - opt) / width) ** 2))


def mash_step(st: MashState, temp_c: float, dt_min: float, p: MashParams) -> MashState:
    """Advance the mash ``dt_min`` minutes at ``temp_c`` (explicit Euler)."""
    gel = (
        p.gel_rate
        / (1.0 + math.exp(-(temp_c - p.gel_mid_c) / p.gel_width_c))
        * st.starch
    )
    cut_a = p.alpha_k * st.alpha * _peak(temp_c, p.alpha_opt_c, p.alpha_width_c)
    cut_a *= st.gelatinized
    cut_b = p.beta_k * st.beta * _peak(temp_c, p.beta_opt_c, p.beta_width_c)
    cut_b *= st.dextrin
    k_beta = (
        math.log(2)
        / p.beta_half_life_63_min
        * p.deactivation_q10 ** ((temp_c - 63.0) / 10.0)
    )
    k_alpha = (
        math.log(2)
        / p.alpha_half_life_70_min
        * p.deactivation_q10 ** ((temp_c - 70.0) / 10.0)
    )
    return MashState(
        starch=max(st.starch - gel * dt_min, 0.0),
        gelatinized=max(st.gelatinized + (gel - cut_a) * dt_min, 0.0),
        dextrin=max(
            st.dextrin + ((1 - p.alpha_sugar_share) * cut_a - cut_b) * dt_min, 0.0
        ),
        sugar=st.sugar + (p.alpha_sugar_share * cut_a + cut_b) * dt_min,
        alpha=st.alpha * math.exp(-k_alpha * dt_min),
        beta=st.beta * math.exp(-k_beta * dt_min),
    )


def mash_profile(
    steps: Iterable[tuple[float, float]], p: MashParams | None = None
) -> MashState:
    """Run a mash profile of ``(temp_c, minutes)`` steps at 0.5 min resolution."""
    params = p or MashParams()
    st = MashState()
    for temp_c, minutes in steps:
        n = max(1, round(minutes / 0.5))
        for _ in range(n):
            st = mash_step(st, temp_c, minutes / n, params)
    return st
