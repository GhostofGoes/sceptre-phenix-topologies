"""Beer fermentation kinetics (de Andrés-Toro et al., 1998).

Equations and parameters follow the reproduction in Rodman, A. D. and
Gerogiorgis, D. I. (2016), "Multi-objective process optimisation of beer
fermentation via dynamic simulation", *Food and Bioproducts Processing*,
doi:10.1016/j.fbp.2016.04.002 (open manuscript:
https://www.pure.ed.ac.uk/ws/files/24789868/FBP_03_2016.pdf), Eqs. (1)-(19),
Tables 1-4; isothermal lager runs reproduce its Table 4. The original
source is de Andrés-Toro, B. et al. (1998), "A kinetic model for beer
production under industrial operational conditions", *Mathematics and
Computers in Simulation* 48:65-74.

Units: concentrations in g/L, time in hours, temperature in kelvin. Every
temperature-dependent parameter has the form ``p = exp(A + B/T)``.

Parameter decisions (DESIGN.md §9.3):

- Rodman Table 1 prints ``B = -12667.24`` for the maximum ethanol production
  rate. That is a typo; ``B = -1267.24`` gives about 0.32 1/h at 15 °C,
  consistent with the other rates.
- Rodman Table 2 prints the diacetyl production rate as ``1.27672e-7``. That
  is a typo too: with it the model peaks near 0.001 ppm, while the paper's own
  Fig. 5d peaks near 1.5 ppm and its Table 4 reports 0.20-0.21 ppm diacetyl at
  the end of the 11 °C and 13 °C isothermal runs. ``1.27672e-4`` (the de
  Andrés-Toro value) reproduces both, and the test checks Table 4.

Ale adaptation (DESIGN.md §9.3): the model was fitted to lager yeast. Its
cell-death rate is extremely temperature sensitive, so at ale temperatures
(66-70 °F) it predicts a stuck fermentation. The kinetics are therefore
evaluated at ``T - temp_shift_k`` (an ale strain at 66 °F behaves like the
fitted lager strain about 6.5 K colder), clamped to the model's validated
8-24 °C range. The shift (with an ale pitch of 2.5 g/L) is tuned so 16.1 °P
attenuates to about 3 °P in 5-6 days at 66-70 °F. Ethyl acetate and
diacetyl are kept in the model's own units (Rodman reports them as ppm).
"""

from __future__ import annotations

import math
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

#: CO2 released per unit ethanol formed (mass basis): 44.01 / 46.07.
CO2_PER_ETHANOL: float = 44.01 / 46.07
#: Heat of fermentation per kg of extract consumed (COBEM 2009).
HEAT_KJ_PER_KG_EXTRACT: float = 586.6
#: Density of water at 20 °C, g/L.
WATER_G_PER_L: float = 998.2
#: Density of ethanol at 20 °C, g/L.
ETHANOL_G_PER_L: float = 789.3


class Frozen(BaseModel):
    """Immutable Pydantic base."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class Arrhenius(Frozen):
    """Rate constant ``exp(A + B/T)`` with T in kelvin."""

    a: float
    b: float

    def __call__(self, t_k: float) -> float:
        """Evaluate at temperature ``t_k`` (K)."""
        return math.exp(self.a + self.b / t_k)


class KineticParams(Frozen):
    """de Andrés-Toro parameters (Rodman and Gerogiorgis 2016, Tables 1-2)."""

    mu_sd0: Arrhenius = Arrhenius(a=33.82, b=-10033.28)  # dead-cell settling, 1/h
    mu_x0: Arrhenius = Arrhenius(a=108.31, b=-31934.09)  # cell growth, 1/h
    mu_s0: Arrhenius = Arrhenius(a=-41.92, b=11654.64)  # sugar consumption, 1/h
    mu_e0: Arrhenius = Arrhenius(a=3.27, b=-1267.24)  # ethanol production, 1/h
    mu_dt: Arrhenius = Arrhenius(a=130.16, b=-38313.0)  # cell death, 1/h
    mu_lag: Arrhenius = Arrhenius(a=30.72, b=-9501.54)  # cell activation, 1/h
    k_s: Arrhenius = Arrhenius(a=-119.63, b=34203.95)  # affinity constant, g/L
    y_ea: Arrhenius = Arrhenius(a=89.92, b=-26589.0)  # ethyl acetate factor
    k_dc: float = 1.27672e-4  # diacetyl production, L/(g h); Rodman prints 1e-7
    k_dm: float = 1.13864e-3  # diacetyl consumption, L/(g h)
    temp_shift_k: float = 0.0  # ale adaptation shift (see module docstring)
    t_min_k: float = 281.15  # validated range, 8 °C
    t_max_k: float = 297.15  # validated range, 24 °C
    # Hop creep (DESIGN.md §9.3): no published constants, so these are tuned.
    hop_creep_k_ref: float = 0.02  # 1/h at hop_creep_t_ref_k
    hop_creep_t_ref_k: float = 292.04  # 66 °F
    hop_creep_q10: float = 2.0
    hop_creep_yield: float = 1.5  # dextrins converted, g/L per lb/bbl

    def model_temp(self, t_k: float) -> float:
        """Temperature the Arrhenius terms see: shifted and clamped."""
        return min(max(t_k - self.temp_shift_k, self.t_min_k), self.t_max_k)

    def hop_creep_k(self, t_k: float) -> float:
        """First-order hop-creep release rate (1/h) at ``t_k``."""
        return self.hop_creep_k_ref * self.hop_creep_q10 ** (
            (t_k - self.hop_creep_t_ref_k) / 10.0
        )


#: Lager parameters exactly as published (with the μ_e0 typo fixed).
LAGER: KineticParams = KineticParams()
#: Ale adaptation used by the cellar (tuned for 5-6 day primary at 66 °F).
ALE: KineticParams = KineticParams(temp_shift_k=6.5)


class FermentState(Frozen):
    """Model state. Concentrations in g/L."""

    x_lag: float
    x_act: float
    x_dead: float
    sugar: float
    ethanol: float = 0.0
    ethyl_acetate: float = 0.0
    diacetyl: float = 0.0
    hop_extract: float = 0.0  # dextrins hop enzymes will convert to sugar
    residual_offset: float = 0.0  # residual extract moved into hop_extract
    lag_done: bool = False

    @property
    def x_suspended(self) -> float:
        """Total suspended biomass, g/L (Eq. 2)."""
        return self.x_lag + self.x_act + self.x_dead


def pitch(sugar_g_l: float, biomass_g_l: float = 4.0) -> FermentState:
    """Initial state at pitching (Eq. 1): 48% lag, 2% active, 50% dead."""
    return FermentState(
        x_lag=0.48 * biomass_g_l,
        x_act=0.02 * biomass_g_l,
        x_dead=0.50 * biomass_g_l,
        sugar=sugar_g_l,
    )


class Rates(Frozen):
    """Time derivatives (per hour) of the state variables."""

    x_lag: float
    x_act: float
    x_dead: float
    sugar: float
    ethanol: float
    ethyl_acetate: float
    diacetyl: float
    hop_extract: float

    @property
    def sugar_consumed(self) -> float:
        """Extract consumed by yeast, g/(L h) (positive while fermenting)."""
        return -(self.sugar + self.hop_extract)


class Fermentation:
    """de Andrés-Toro kinetics for one batch of wort.

    ``s0`` is the initial fermentable sugar that sets the density-dependent
    terms (Eqs. 6, 10, 16).
    """

    s0: float
    p: KineticParams

    def __init__(self, s0: float, params: KineticParams = ALE) -> None:
        self.s0 = s0
        self.p = params

    def rates(self, st: FermentState, t_k: float) -> Rates:
        """Derivatives at state ``st`` and liquid temperature ``t_k``."""
        p = self.p
        tm = p.model_temp(t_k)
        s = max(st.sugar, 0.0)
        e = max(st.ethanol, 0.0)
        half_s0 = 0.5 * self.s0
        mu_sd = p.mu_sd0(tm) * half_s0 / (half_s0 + e)  # Eq. 6
        mu_lag = p.mu_lag(tm)
        k_s = p.k_s(tm)
        mu_s = p.mu_s0(tm) * s / (k_s + s)  # Eq. 14
        mu_a = p.mu_e0(tm) * s / (k_s + s)  # Eq. 17
        f = max(1.0 - e / half_s0, 0.0)  # Eq. 16
        if st.lag_done:
            mu_x = p.mu_x0(tm) * s / (half_s0 + e)  # Eq. 10
            mu_dt = p.mu_dt(tm)
        else:  # lag phase: no growth, no death (Eqs. 4-5)
            mu_x = 0.0
            mu_dt = 0.0
        hop_release = p.hop_creep_k(t_k) * max(st.hop_extract, 0.0)
        return Rates(
            x_lag=-mu_lag * st.x_lag,  # Eq. 3
            x_act=mu_x * st.x_act - mu_dt * st.x_act + mu_lag * st.x_lag,  # Eq. 11
            x_dead=-mu_sd * st.x_dead + mu_dt * st.x_act,  # Eq. 12
            sugar=-mu_s * st.x_act + hop_release,  # Eq. 13 plus hop creep
            ethanol=f * mu_a * st.x_act,  # Eq. 15
            ethyl_acetate=p.y_ea(tm) * mu_x * st.x_act,  # Eq. 18
            diacetyl=p.k_dc * s * st.x_act - p.k_dm * st.diacetyl * e,  # Eq. 19
            hop_extract=-hop_release,
        )

    def step(self, st: FermentState, t_k: float, dt_h: float) -> FermentState:
        """Advance ``dt_h`` hours with classic RK4 at constant temperature."""
        k1 = self.rates(st, t_k)
        k2 = self.rates(_axpy(st, k1, dt_h / 2), t_k)
        k3 = self.rates(_axpy(st, k2, dt_h / 2), t_k)
        k4 = self.rates(_axpy(st, k3, dt_h), t_k)
        new = st.model_copy(
            update={
                name: max(
                    getattr(st, name)
                    + dt_h
                    / 6.0
                    * (
                        getattr(k1, name)
                        + 2 * getattr(k2, name)
                        + 2 * getattr(k3, name)
                        + getattr(k4, name)
                    ),
                    0.0,
                )
                for name in _STATE_FIELDS
            }
        )
        if not new.lag_done and new.x_act >= 0.5 * new.x_suspended:
            # End of lag phase: about half of the suspended cells are active.
            new = new.model_copy(update={"lag_done": True})
        return new


_STATE_FIELDS: tuple[str, ...] = (
    "x_lag",
    "x_act",
    "x_dead",
    "sugar",
    "ethanol",
    "ethyl_acetate",
    "diacetyl",
    "hop_extract",
)


def _axpy(st: FermentState, k: Rates, h: float) -> FermentState:
    return st.model_copy(
        update={n: getattr(st, n) + h * getattr(k, n) for n in _STATE_FIELDS}
    )


# ---------------------------------------------------------------------------
# Wort and gravity
# ---------------------------------------------------------------------------


def sg_from_plato(plato: float) -> float:
    """Specific gravity (20/20 °C) from degrees Plato."""
    return 1.0 + plato / (258.6 - (plato / 258.2) * 227.1)


def plato_from_sg(sg: float) -> float:
    """Degrees Plato from specific gravity (ASBC polynomial)."""
    return -616.868 + 1111.14 * sg - 630.272 * sg**2 + 135.997 * sg**3


def extract_g_l(plato: float) -> float:
    """Extract concentration (g/L) of a wort at ``plato``."""
    return plato / 100.0 * sg_from_plato(plato) * WATER_G_PER_L


class Wort(Frozen):
    """Split of a wort's extract into fermentable sugar and residual extract.

    The residual (non-fermentable) extract is sized with Balling's relation
    so a fully fermented wort finishes at ``fg_plato`` apparent extract.
    """

    og_plato: float = 16.1
    fg_plato: float = 3.1

    @property
    def total_extract(self) -> float:
        """Total extract, g/L."""
        return extract_g_l(self.og_plato)

    @property
    def residual_extract(self) -> float:
        """Non-fermentable extract, g/L."""
        re_final = 0.1808 * self.og_plato + 0.8192 * self.fg_plato
        return re_final / 100.0 * sg_from_plato(self.fg_plato) * WATER_G_PER_L

    @property
    def fermentable(self) -> float:
        """Initial fermentable sugar S0, g/L."""
        return self.total_extract - self.residual_extract

    def gravity(self, st: FermentState) -> float:
        """Apparent extract (°P) of a fermentation state.

        Dextrins awaiting hop-creep conversion are still dissolved extract,
        so they count toward the gravity.
        """
        sugar = st.sugar + st.hop_extract + st.residual_offset
        return self.apparent_extract(sugar, st.ethanol)

    def apparent_extract(self, sugar: float, ethanol: float) -> float:
        """Apparent extract (°P, what a density meter reads) from S and E.

        Real extract comes from the remaining sugar plus residual extract;
        ethanol lowers the density (Tabarié: SG = SG_extract + SG_alcohol - 1).
        """
        rho = WATER_G_PER_L * sg_from_plato(self.og_plato)
        sg = 1.0
        for _ in range(4):  # fixed point on beer density
            real = 100.0 * (sugar + self.residual_extract) / rho
            abw = 100.0 * ethanol / rho
            sg = sg_from_plato(real) + _sg_ethanol(abw) - 1.0
            rho = WATER_G_PER_L * sg
        return plato_from_sg(sg)

    @staticmethod
    def abv(ethanol: float) -> float:
        """Alcohol by volume (%) from ethanol concentration (g/L)."""
        return 100.0 * ethanol / ETHANOL_G_PER_L


def _sg_ethanol(abw: float) -> float:
    """SG of an ethanol-water solution at ``abw`` % w/w (fit for 0-8%)."""
    return 1.0 - 0.00180 * abw
