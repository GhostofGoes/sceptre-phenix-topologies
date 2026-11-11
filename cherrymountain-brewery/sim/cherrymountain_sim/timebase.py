"""Time compression for the cellar (DESIGN.md §9.4).

Rules:

- Kinetic state (sugar, ethanol, biomass, diacetyl) advances ``compression``
  times faster than the wall clock.
- Exported fluxes (CO2 g/s, fermentation heat W, and the jacket, glycol and
  electrical loads they cause) are *real-time-equivalent*: the real-world
  rate for the tank's current kinetic state, never multiplied by the
  compression factor.
- Tank thermal and fluid dynamics run in wall time.
- Recipe-time logic runs in sim time, read from ``cellar/clock.sim_hours``.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

#: Default compression: one simulated day every 30 wall minutes.
DEFAULT_COMPRESSION: float = 48.0


class TimeBase(BaseModel):
    """Maps wall-clock seconds to compressed kinetic (sim) time."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    compression: float = Field(default=DEFAULT_COMPRESSION, ge=1.0)

    def kinetic_hours(self, wall_s: float) -> float:
        """Sim (kinetic) hours covered by ``wall_s`` wall seconds."""
        return wall_s * self.compression / 3600.0

    def wall_seconds(self, sim_hours: float) -> float:
        """Wall seconds needed to cover ``sim_hours`` of kinetics."""
        return sim_hours * 3600.0 / self.compression

    @staticmethod
    def flux_per_second(rate_per_sim_hour: float) -> float:
        """Real-time-equivalent flux (per wall second) for a kinetic rate.

        The rate is evaluated per *sim* hour from the current state, so the
        exported flux is the real-world value and does not depend on the
        compression factor.
        """
        return rate_per_sim_hour / 3600.0
