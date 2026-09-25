"""Calendar helpers shared by the forecast features and the dbt holiday seed."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
import numpy as np
import numpy.typing as npt

UK = ZoneInfo("Europe/London")
HALF_HOUR = timedelta(minutes=30)


def uk_bank_holidays(years: Iterable[int]) -> dict[date, str]:
    """England and Wales bank holidays for ``years``, as {date: name}."""
    calendar = holidays.country_holidays("GB", subdiv="ENG", years=list(years))
    return {day: str(name) for day, name in calendar.items()}


@dataclass(frozen=True)
class CalendarArrays:
    """Calendar attributes of each half-hour on a regular grid, in UK local time."""

    slot: npt.NDArray[np.int16]  # 0..47 local half-hour of day
    day_of_week: npt.NDArray[np.int8]  # 0 = Monday
    is_holiday: npt.NDArray[np.bool_]
    doy_sin: npt.NDArray[np.float64]
    doy_cos: npt.NDArray[np.float64]

    def __len__(self) -> int:
        return len(self.slot)


def calendar_for(start: datetime, periods: int) -> CalendarArrays:
    """Local calendar attributes for ``periods`` half-hours starting at ``start`` (UTC)."""
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware")
    if periods < 0:
        raise ValueError("periods must not be negative")
    start = start.astimezone(UTC)
    slot = np.empty(periods, dtype=np.int16)
    dow = np.empty(periods, dtype=np.int8)
    doy = np.empty(periods, dtype=np.float64)
    local_dates: list[date] = []
    for i in range(periods):
        local = (start + i * HALF_HOUR).astimezone(UK)
        slot[i] = local.hour * 2 + local.minute // 30
        dow[i] = local.weekday()
        doy[i] = local.timetuple().tm_yday
        local_dates.append(local.date())
    years = {d.year for d in local_dates} or {start.year}
    bank = uk_bank_holidays(range(min(years), max(years) + 1))
    is_holiday = np.fromiter((d in bank for d in local_dates), dtype=np.bool_, count=periods)
    angle = 2 * np.pi * (doy - 1) / 365.25
    return CalendarArrays(
        slot=slot,
        day_of_week=dow,
        is_holiday=is_holiday,
        doy_sin=np.sin(angle),
        doy_cos=np.cos(angle),
    )
