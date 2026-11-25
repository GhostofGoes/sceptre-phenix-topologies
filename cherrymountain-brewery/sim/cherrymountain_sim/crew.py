"""The cellar crew: the hand work that moves beer through the cellar.

A 30 bbl cellar is run by people with hoses and a swing panel. No PLC point
does these jobs, so the model does them when the plant's state calls for them
(DESIGN.md §9.6), whoever controls the cellar:

- **Rack** a fermenter into the conditioning tank (BBT-201) once its beer is
  done and cold, leaving the yeast and trub behind, then dump what is left.
- **Put beer on line**: move the conditioning tank's beer into the serving
  tank (BBT-202, the one plc-cellar opens to the filler) once it is bright and
  the serving tank is nearly empty.
- **Clean** an emptied fermenter or the emptied conditioning tank by hand. The
  serving tank is topped up with the same beer and is not cleaned between fills.

The crew does one job at a time. A transfer takes a set-up time (hoses, the
swing panel) and then runs at the transfer pump's rate, with the pump in Hand
at its local starter: plc-cellar gives the pump no command, and it reads the
pump running as a transfer in progress, which withdraws the CIP permit.

What the crew does not do: change a setpoint. A fermenter is ready when its
beer is old enough and cold, so somebody (the built-in operator, or whoever
commands plc-cellar) has to crash it first.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from cherrymountain_sim.carbonation import BrightTank
from cherrymountain_sim.cellar import LITERS_PER_BBL, Cellar, Fermenter, Transfer

LOG: logging.Logger = logging.getLogger("cherrymountain_sim.crew")


class CrewSettings(BaseModel):
    """The cellar crew's rules (helics.json ``sim.crew``)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    enabled: bool = True
    #: The bright tank that feeds the filler. The others condition beer for it.
    serving: str = "BBT202"
    #: Minutes to set a transfer up: hoses, the swing panel, a purge.
    setup_min: float = Field(default=10.0, ge=0.0)
    #: Minutes to clean an emptied vessel by hand.
    clean_min: float = Field(default=45.0, ge=0.0)
    #: A fermenter is ready to rack this many kinetic hours after pitching (a
    #: day into the crash that starts on day 10) ...
    ready_age_h: float = Field(default=264.0, ge=0.0)
    #: ... once both of its zones are this cold, °F.
    ready_temp_f: float = 40.0
    #: The conditioning tank's beer is bright at this much dissolved CO2, vol ...
    bright_vol: float = Field(default=2.4, ge=0.0)
    #: ... and this cold, °F.
    bright_temp_f: float = 38.0
    #: The serving tank is refilled when it is down to this level, %.
    refill_below_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    #: The share of a fermenter racking leaves behind (yeast and trub), %.
    rack_leave_pct: float = Field(default=7.0, ge=0.0, le=50.0)
    #: The transfer pump's rate, gal/min.
    pump_gpm: float = Field(default=30.0, gt=0.0)


class Job(BaseModel):
    """What the crew is doing."""

    kind: Literal["rack", "to-filler", "clean"]
    src: str
    dst: str = ""
    stage: Literal["setup", "pump", "clean"]
    #: Federate seconds at which the set-up or the cleaning is over.
    until_s: float = 0.0


class CellarCrew:
    """The crew's jobs on one :class:`~cherrymountain_sim.cellar.Cellar`."""

    cellar: Cellar
    settings: CrewSettings
    job: Job | None
    racked_l: float
    to_filler_l: float
    dumped_l: float
    cleans: int
    #: Fermenters that took wort before they were cleaned, with the count.
    dirty_fills: dict[str, int]

    def __init__(self, cellar: Cellar, settings: CrewSettings | None = None) -> None:
        self.cellar = cellar
        self.settings = settings or CrewSettings()
        self.job = None
        self.racked_l = 0.0
        self.to_filler_l = 0.0
        self.dumped_l = 0.0
        self.cleans = 0
        self.dirty_fills = {}
        self._flagged: set[str] = set()

    # -- what the vessels need ---------------------------------------------------------

    @property
    def serving(self) -> BrightTank | None:
        """The tank that feeds the filler."""
        return self.cellar.bbts.get(self.settings.serving)

    @property
    def conditioning(self) -> BrightTank | None:
        """The tank beer is racked into and carbonated in (the first that is not
        the serving tank)."""
        return next((b for u, b in self.cellar.bbts.items() if u != self.settings.serving), None)

    def ready(self, fv: Fermenter) -> bool:
        """True when a fermenter's beer is done and cold enough to rack."""
        s = self.settings
        return (
            fv.filled
            and fv.age_h >= s.ready_age_h
            and max(fv.zone_temp_f("side"), fv.zone_temp_f("cone")) <= s.ready_temp_f
        )

    def bright(self, bbt: BrightTank) -> bool:
        """True when a tank's beer is carbonated and cold enough to package."""
        s = self.settings
        return bbt.liquid_l > 0 and bbt.co2_vol >= s.bright_vol and bbt.temp_f <= s.bright_temp_f

    def _next(self, t: float) -> Job | None:
        s = self.settings
        serving, cond = self.serving, self.conditioning
        if serving is None or cond is None:
            return None  # no bright tanks to work with (the ci slice)
        setup = t + s.setup_min * 60.0
        clean = t + s.clean_min * 60.0
        if serving.level_pct <= s.refill_below_pct and self.bright(cond):
            return Job(kind="to-filler", src=cond.unit, dst=serving.unit, stage="setup", until_s=setup)
        if cond.liquid_l <= 0 and not cond.clean:
            return Job(kind="clean", src=cond.unit, stage="clean", until_s=clean)
        for unit, fv in self.cellar.fermenters.items():
            if not fv.filled and not fv.clean:
                return Job(kind="clean", src=unit, stage="clean", until_s=clean)
        if cond.liquid_l <= 0 and cond.clean:
            waiting = [(fv.age_h, unit) for unit, fv in self.cellar.fermenters.items() if self.ready(fv)]
            if waiting:
                return Job(kind="rack", src=max(waiting)[1], dst=cond.unit, stage="setup", until_s=setup)
        return None

    # -- the work ----------------------------------------------------------------------

    def step(self, t: float) -> None:
        """Do the crew's work up to federate time ``t`` (before the cellar's step)."""
        self._watch_fills()
        if not self.settings.enabled:
            return
        if self.job is None:
            self.job = self._next(t)
            if self.job is not None:
                LOG.info("cellar crew: %s %s%s", self.job.kind, self.job.src, f" to {self.job.dst}" if self.job.dst else "")
            return
        job = self.job
        if job.stage == "setup":
            if t >= job.until_s:
                self._start_pump(job)
        elif job.stage == "pump":
            if self.cellar.transfer is None or self.cellar.transfer.done:
                self._finish_transfer(job)
        elif t >= job.until_s:
            self._vessel_clean(job.src)
            self.cleans += 1
            self.job = None

    def _start_pump(self, job: Job) -> None:
        leave = 0.0
        fv = self.cellar.fermenters.get(job.src)
        if fv is not None:
            leave = fv.liquid_l * self.settings.rack_leave_pct / 100.0
        self.cellar.transfer = Transfer(src=job.src, dst=job.dst, gpm=self.settings.pump_gpm, leave_l=leave)
        job.stage = "pump"

    def _finish_transfer(self, job: Job) -> None:
        moved = self.cellar.transfer.moved_l if self.cellar.transfer else 0.0
        self.cellar.transfer = None
        fv = self.cellar.fermenters.get(job.src)
        if fv is not None:
            self.racked_l += moved
            # A full destination ends a racking too; the fermenter is emptied only
            # when the beer is out of it.
            if fv.liquid_l <= fv.cfg.volume_l * self.settings.rack_leave_pct / 100.0 + 1.0:
                self.dumped_l += fv.dump()
        else:
            self.to_filler_l += moved
        LOG.info("cellar crew: %s %s to %s moved %.1f bbl", job.kind, job.src, job.dst, moved / LITERS_PER_BBL)
        self.job = None

    def _vessel_clean(self, unit: str) -> None:
        fv = self.cellar.fermenters.get(unit)
        if fv is not None:
            fv.clean = True
        else:
            self.cellar.bbts[unit].clean = True

    def _watch_fills(self) -> None:
        """Count a fermenter that took wort before it was cleaned, once per fill."""
        for unit, fv in self.cellar.fermenters.items():
            if fv.dirty_fill and unit not in self._flagged:
                self._flagged.add(unit)
                self.dirty_fills[unit] = self.dirty_fills.get(unit, 0) + 1
                LOG.warning("cellar: %s was filled before it was cleaned", unit)
            elif not fv.dirty_fill:
                self._flagged.discard(unit)

    # -- what the transfer panel shows -------------------------------------------------

    @property
    def route_set(self) -> bool:
        """True while the swing panel is on a transfer route (the pumping stage)."""
        return self.job is not None and self.job.stage == "pump"

    @property
    def pumping(self) -> bool:
        """True while the crew runs the transfer pump in Hand."""
        return self.route_set and self.cellar.transfer is not None and not self.cellar.transfer.done

    def truth(self) -> dict[str, Any]:
        """The crew's job and totals, and which empty vessels are clean."""
        cellar = self.cellar
        vessels = {**cellar.fermenters, **cellar.bbts}
        return {
            "enabled": self.settings.enabled,
            "job": self.job.model_dump(exclude={"until_s"}) if self.job else None,
            "racked_bbl": round(self.racked_l / LITERS_PER_BBL, 2),
            "to_filler_bbl": round(self.to_filler_l / LITERS_PER_BBL, 2),
            "dumped_bbl": round(self.dumped_l / LITERS_PER_BBL, 2),
            "cleans": self.cleans,
            "dirty": sorted(u for u, v in vessels.items() if not v.clean and v.liquid_l <= 0),
            "ready_to_rack": sorted(u for u, fv in cellar.fermenters.items() if self.ready(fv)),
            "dirty_fills": dict(self.dirty_fills),
        }
