"""Spring-loaded pressure-relief valve (PSV) on a cellar vessel (DESIGN.md §6.3).

Every fermenter and bright tank is rated 15 psig MAWP and carries a mechanical
relief valve set at that pressure. The PLC does not control it, so it is the
limit an attacker cannot write away: raising a spunding setpoint above the
MAWP, or forcing a carbonation stone open, ends with the PSV lifting and
venting CO2 into the cellar room instead of the vessel's pressure climbing
without bound.

The model is the usual pop-action valve:

- **Lift.** The valve pops open when the head reaches the set pressure
  (gauge).
- **Flow.** While open it vents up to its rated capacity, ``rated_gps``
  grams of CO2 per second. An inflow larger than that (a failed regulator on a
  60 psig supply) keeps the head rising above the set pressure: overpressure.
- **Reseat.** It closes once the head has fallen to the reseat pressure, the
  set pressure less the blowdown (10 % of set, a typical value for a small
  spring-loaded valve in gas service).

A valve that lifts and reseats within one step still reports that step as
lifted (:attr:`ReliefValve.open`), so a short pop shows in the published
state. The vented mass goes into the cellar room (DESIGN.md §6.6 #5), where
the building federate's CO2 balance and the §6.7 interlock see it.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class ReliefValveConfig(BaseModel):
    """Set pressure, blowdown, and capacity of one PSV.

    The capacity covers the largest credible CO2 inflow into a bright tank: the
    carbonation stone fully open (200 standard L/min) plus the serving
    regulator (150 standard L/min), about 11.4 g/s, with margin.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    set_psig: float = 15.0
    blowdown_pct: float = 10.0
    rated_gps: float = 25.0

    @property
    def reseat_psig(self) -> float:
        """Gauge pressure at which the valve closes again."""
        return self.set_psig * (1.0 - self.blowdown_pct / 100.0)


class ReliefValve:
    """State of one PSV: lifted or seated, and what it vented."""

    cfg: ReliefValveConfig
    lifted: bool
    vent_gps: float
    vented_g: float
    lifts: int

    def __init__(self, cfg: ReliefValveConfig | None = None) -> None:
        self.cfg = cfg or ReliefValveConfig()
        self.lifted = False
        self.vent_gps = 0.0  # CO2 vented in the last step, g/s
        self.vented_g = 0.0  # running total, g
        self.lifts = 0  # times the valve popped

    @property
    def open(self) -> bool:
        """True while lifted, or when it vented during the last step."""
        return self.lifted or self.vent_gps > 0.0

    def relieve(self, head_psig: float, excess_g: float, dt_s: float) -> float:
        """Grams of CO2 the valve vents in a step of ``dt_s`` seconds.

        ``head_psig`` is the head pressure (gauge) before relief, and
        ``excess_g`` the gas above the reseat pressure, which is the most a
        lift can let out. The caller removes the returned mass from the
        vessel's headspace.
        """
        if not self.lifted and head_psig >= self.cfg.set_psig:
            self.lifted = True
            self.lifts += 1
        vent_g = 0.0
        if self.lifted:
            vent_g = min(self.cfg.rated_gps * dt_s, max(excess_g, 0.0))
            if vent_g >= excess_g:  # blown down to the reseat pressure
                self.lifted = False
        self.vent_gps = vent_g / dt_s if dt_s > 0 else 0.0
        self.vented_g += vent_g
        return vent_g
