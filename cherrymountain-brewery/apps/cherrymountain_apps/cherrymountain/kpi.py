"""Plant KPIs for ``status.apps.cherrymountain`` (DESIGN.md 16).

The KPIs come from the sim-ctl event API's ``GET /api/v1/kpis``, which reads
the federates' ground truth (cherrymountain_sim's event API on sim-ctl). The
ci variant has no event API, and the API may not answer yet during startup,
so the reader then reports ``available: false`` with a reason instead of
failing the stage.

Expected reply (every field optional; missing ones stay null):

    {
      "sim_time": "2026-07-14T09:30:00-06:00",
      "batches_completed": 3,
      "batches_in_progress": 1,
      "bottles_packaged": 12000,
      "kegs_packaged": 20,
      "energy_kwh": 5200.0,
      "demand_kw": 180.5,
      "peak_demand_kw": 240.0,
      "ttb_bbl_packaged": 41.2,
      "genset_test_hours_year": 0.5,
      ...
    }

The compliance figures (DESIGN.md 18A) pass through unchanged: TTB barrels,
fill inspection, wastewater inhibits, CIP verification, and the genset's run
and test hours, which a ``genset-exercise`` event (plantevent) advances. The
app adds the test hours left under the 40 CFR 60.4211(f) cap of 100 a year.
"""

from __future__ import annotations

from typing import Any, ClassVar, Final

from phenix_apps.common.utils import utc_now
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import simctl

#: Ounces per US beer barrel, and per bottle (DESIGN.md 12.2).
OZ_PER_BBL: Final[float] = 3968.0
OZ_PER_BOTTLE: Final[float] = 12.0
#: A half-barrel keg.
BBL_PER_KEG: Final[float] = 0.5
#: Maintenance and readiness-test hours a year an emergency engine may run
#: (40 CFR 60.4211(f)(2); DESIGN.md 7.3, 18A.4).
GENSET_TEST_HOURS_CAP: Final[float] = 100.0


class SimKpis(BaseModel):
    """The event API's KPI reply."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    sim_time: str | None = None
    batches_completed: int | None = Field(default=None, ge=0)
    batches_in_progress: int | None = Field(default=None, ge=0)
    bottles_packaged: int | None = Field(default=None, ge=0)
    kegs_packaged: int | None = Field(default=None, ge=0)
    energy_kwh: float | None = Field(default=None, ge=0)
    demand_kw: float | None = Field(default=None, ge=0)
    peak_demand_kw: float | None = Field(default=None, ge=0)
    ttb_bbl_packaged: float | None = Field(default=None, ge=0)
    fill_mean_floz: float | None = Field(default=None, ge=0)
    fill_under_mav_rejects: int | None = Field(default=None, ge=0)
    wastewater_inhibit_events: int | None = Field(default=None, ge=0)
    cip_runs_verified: int | None = Field(default=None, ge=0)
    cip_runs_unverified: int | None = Field(default=None, ge=0)
    genset_hours_year: float | None = Field(default=None, ge=0)
    genset_test_hours_year: float | None = Field(default=None, ge=0)


class Compliance(BaseModel):
    """The compliance figures of the KPI reply (DESIGN.md 18A)."""

    ttb_bbl_packaged: float | None = None
    fill_mean_floz: float | None = None
    fill_under_mav_rejects: int | None = None
    wastewater_inhibit_events: int | None = None
    cip_runs_verified: int | None = None
    cip_runs_unverified: int | None = None
    genset_hours_year: float | None = None
    genset_test_hours_year: float | None = None
    #: Test hours left this year under the 40 CFR 60.4211(f) cap.
    genset_test_hours_left: float | None = None


class Kpis(BaseModel):
    """What the app writes under ``status.apps.cherrymountain.kpis``."""

    available: bool
    reason: str | None = None
    source: str = "sim-ctl event API"
    read_at: str
    sim_time: str | None = None
    batches: int | None = None
    batches_in_progress: int | None = None
    bottles: int | None = None
    kegs: int | None = None
    packaged_bbl: float | None = None
    energy_kwh: float | None = None
    kwh_per_bbl: float | None = None
    demand_kw: float | None = None
    peak_demand_kw: float | None = None
    compliance: Compliance | None = None


def derive(raw: SimKpis, read_at: str) -> Kpis:
    """Barrels from bottles (12 oz) and kegs (1/2 bbl), and kWh per barrel."""
    bbl: float | None = None
    if raw.bottles_packaged is not None or raw.kegs_packaged is not None:
        bbl = (raw.bottles_packaged or 0) * OZ_PER_BOTTLE / OZ_PER_BBL + (
            raw.kegs_packaged or 0
        ) * BBL_PER_KEG
    per_bbl: float | None = None
    if bbl and raw.energy_kwh is not None:
        per_bbl = round(raw.energy_kwh / bbl, 2)
    compliance: Compliance = Compliance.model_validate(
        raw.model_dump(include=set(Compliance.model_fields))
    )
    if raw.genset_test_hours_year is not None:
        compliance.genset_test_hours_left = round(
            max(GENSET_TEST_HOURS_CAP - raw.genset_test_hours_year, 0.0), 2
        )
    return Kpis(
        available=True,
        compliance=compliance,
        read_at=read_at,
        sim_time=raw.sim_time,
        batches=raw.batches_completed,
        batches_in_progress=raw.batches_in_progress,
        bottles=raw.bottles_packaged,
        kegs=raw.kegs_packaged,
        packaged_bbl=round(bbl, 3) if bbl is not None else None,
        energy_kwh=raw.energy_kwh,
        kwh_per_bbl=per_bbl,
        demand_kw=raw.demand_kw,
        peak_demand_kw=raw.peak_demand_kw,
    )


def read(mm: Any, vm: str, guest_port: int, timeout: float) -> Kpis:
    """The current KPIs, or ``available: false`` and why."""
    now: str = utc_now().isoformat()
    try:
        endpoint: simctl.Endpoint = simctl.resolve(mm, vm, guest_port, create=False)
        raw: Any = simctl.EventApi(endpoint.url, timeout).kpis()
        return derive(SimKpis.model_validate(raw), now)
    except simctl.TunnelError:
        reason = f"no tunnel to {vm}:{guest_port} yet"
    except simctl.ApiError as ex:
        reason = f"event API: {ex}"
    except ValidationError:
        reason = "event API: the KPI reply has the wrong shape"
    return Kpis(available=False, reason=reason, read_at=now)
