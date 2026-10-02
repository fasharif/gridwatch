"""Load the national series from the warehouse onto a regular half-hourly grid."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from gridwatch.forecast.features import FloatArray

HALF_HOUR = timedelta(minutes=30)


class SeriesError(ValueError):
    """The national series is missing or not on a regular half-hourly grid."""


@dataclass(frozen=True)
class NationalSeries:
    """Actual and API-forecast intensity on a gap-free half-hourly grid (NaN = missing)."""

    start: datetime
    actual: FloatArray
    api_forecast: FloatArray

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise SeriesError("start must be timezone-aware")
        if self.actual.shape != self.api_forecast.shape:
            raise SeriesError("actual and api_forecast must have the same length")

    def __len__(self) -> int:
        return len(self.actual)

    def timestamp(self, index: int) -> datetime:
        """Naive UTC start time of the half-hour at ``index`` (may be past the end)."""
        return (self.start + index * HALF_HOUR).astimezone(UTC).replace(tzinfo=None)

    def index_of(self, when: datetime) -> int:
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        delta = when - self.start
        if delta % HALF_HOUR:
            raise SeriesError(f"{when.isoformat()} is not on the half-hour grid")
        return int(delta / HALF_HOUR)

    def truncate(self, until: datetime) -> NationalSeries:
        """The part of the series that starts before ``until`` (for validation runs)."""
        stop = max(0, min(len(self), self.index_of(until)))
        if stop == 0:
            raise SeriesError(f"no data before {until.isoformat()}")
        return NationalSeries(
            self.start, self.actual[:stop].copy(), self.api_forecast[:stop].copy()
        )

    def last_actual_index(self) -> int:
        known = np.flatnonzero(~np.isnan(self.actual))
        if known.size == 0:
            raise SeriesError("the series has no actual values")
        return int(known[-1])

    @classmethod
    def from_frame(cls, frame: pl.DataFrame) -> NationalSeries:
        """Build from columns period_start_utc, actual_gco2_kwh, forecast_gco2_kwh."""
        if frame.height == 0:
            raise SeriesError("the national series is empty")
        frame = frame.sort("period_start_utc")
        starts = frame["period_start_utc"]
        steps = starts.diff().drop_nulls().unique()
        if steps.len() > 1 or (steps.len() == 1 and steps[0] != HALF_HOUR):
            raise SeriesError("the national series has gaps or duplicates; rebuild the marts")
        first = starts[0]
        if not isinstance(first, datetime):
            raise SeriesError("period_start_utc must be a timestamp column")
        return cls(
            start=first.replace(tzinfo=UTC),
            actual=frame["actual_gco2_kwh"].cast(pl.Float64).fill_null(np.nan).to_numpy(),
            api_forecast=frame["forecast_gco2_kwh"].cast(pl.Float64).fill_null(np.nan).to_numpy(),
        )


def load_national_series(warehouse: Path) -> NationalSeries:
    if not warehouse.exists():
        raise SeriesError(f"{warehouse} does not exist; run `gridwatch transform` first")
    with duckdb.connect(str(warehouse), read_only=True) as con:
        frame = con.sql(
            """
            select period_start_utc, actual_gco2_kwh, forecast_gco2_kwh
            from marts.fct_national_intensity
            order by period_start_utc
            """
        ).pl()
    return NationalSeries.from_frame(frame)
