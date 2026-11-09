"""SV-SGT, the fictional utility tariff built from OpenEI URDB records (DESIGN.md §7.4).

Values are copied as recorded in URDB (CC0), effective 2026-01-01, for Public
Service Co. of Colorado (EIA 15466):

- Distribution demand, Schedule SG (URDB 69bda768f0300279d30f154c):
  $9.66/kW-month on the monthly maximum demand.
- Generation and transmission demand, same record: $27.14/kW in June to
  September, $19.79/kW in the other months, all hours.
- Energy, Schedule SG-TOU (URDB 69bdaa2ade50eca8ee0baa6a): on-peak weekdays
  17:00-21:00 all year, $0.38375/kWh in June to September and $0.23371/kWh
  otherwise; off-peak $0.09927/kWh.
- Fixed charge $91.79/month. Demand interval 15 minutes (the records leave it
  blank).

:class:`Meter` integrates the site's import power into 15-minute demand
intervals, the monthly peak, TOU energy, and the month-to-date bill.
"""

from __future__ import annotations

import datetime as dt
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class Tariff(BaseModel):
    """SV-SGT rates."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    distribution_demand_per_kw: float = 9.66
    gt_demand_summer_per_kw: float = 27.14
    gt_demand_other_per_kw: float = 19.79
    energy_onpeak_summer_per_kwh: float = 0.38375
    energy_onpeak_other_per_kwh: float = 0.23371
    energy_offpeak_per_kwh: float = 0.09927
    fixed_per_month: float = 91.79
    summer_months: tuple[int, ...] = (6, 7, 8, 9)
    onpeak_start_hour: int = 17
    onpeak_end_hour: int = 21
    demand_interval_s: float = 900.0

    def summer(self, month: int) -> bool:
        """June to September."""
        return month in self.summer_months

    def onpeak(self, when: dt.datetime) -> bool:
        """Weekdays 17:00-21:00, all year (holidays are not modeled)."""
        return (
            when.weekday() < 5
            and self.onpeak_start_hour <= when.hour < self.onpeak_end_hour
        )

    def energy_rate(self, when: dt.datetime) -> float:
        """$/kWh at ``when``."""
        if not self.onpeak(when):
            return self.energy_offpeak_per_kwh
        if self.summer(when.month):
            return self.energy_onpeak_summer_per_kwh
        return self.energy_onpeak_other_per_kwh

    def demand_rate(self, month: int) -> float:
        """$/kW-month on the monthly peak: distribution plus G&T."""
        gt = (
            self.gt_demand_summer_per_kw
            if self.summer(month)
            else self.gt_demand_other_per_kw
        )
        return self.distribution_demand_per_kw + gt

    def annual_demand_rate(self) -> float:
        """$/kW-year of a peak held every month (the "each 50 kW" figure)."""
        return sum(self.demand_rate(m) for m in range(1, 13))

    def bill(self, energy_cost: float, peak_kw: float, month: int) -> float:
        """A monthly bill from its energy cost and peak demand."""
        return self.fixed_per_month + energy_cost + peak_kw * self.demand_rate(month)


class Meter(BaseModel):
    """Revenue meter: 15-minute demand, monthly peak, TOU energy, bill."""

    tariff: Tariff = Tariff()
    month: int = 0
    interval_start: float = 0.0
    interval_kwh: float = 0.0
    last_demand_kw: float = 0.0
    month_peak_kw: float = 0.0
    run_peak_kw: float = 0.0
    month_energy_cost: float = 0.0
    month_kwh: float = 0.0
    total_kwh: float = 0.0
    onpeak_kwh: float = 0.0

    def add(self, t_s: float, when: dt.datetime, import_kw: float, dt_s: float) -> None:
        """Record ``dt_s`` seconds of net import (export earns nothing here)."""
        if when.month != self.month:
            self.month = when.month
            self.month_peak_kw = self.month_energy_cost = self.month_kwh = 0.0
        kwh = max(import_kw, 0.0) * dt_s / 3600.0
        self.interval_kwh += kwh
        self.month_kwh += kwh
        self.total_kwh += kwh
        if self.tariff.onpeak(when):
            self.onpeak_kwh += kwh
        self.month_energy_cost += kwh * self.tariff.energy_rate(when)
        if t_s + dt_s - self.interval_start >= self.tariff.demand_interval_s:
            self.last_demand_kw = (
                self.interval_kwh * 3600.0 / self.tariff.demand_interval_s
            )
            self.month_peak_kw = max(self.month_peak_kw, self.last_demand_kw)
            self.run_peak_kw = max(self.run_peak_kw, self.last_demand_kw)
            self.interval_kwh = 0.0
            self.interval_start = t_s + dt_s

    def running_demand_kw(self, t_s: float) -> float:
        """Demand the current interval is heading for (its average so far),
        or the last completed interval's in the interval's first minute."""
        elapsed = t_s - self.interval_start
        if elapsed < 60.0:
            return self.last_demand_kw
        return self.interval_kwh * 3600.0 / elapsed

    @property
    def month_to_date_usd(self) -> float:
        """Month-to-date bill (the peak so far is billed in full)."""
        return self.tariff.bill(
            self.month_energy_cost, self.month_peak_kw, self.month or 1
        )
