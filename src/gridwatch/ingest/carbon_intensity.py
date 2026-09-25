"""Client helpers for the GB Carbon Intensity API (api.carbonintensity.org.uk).

The API is run by the National Energy System Operator (NESO) and published under CC BY 4.0.
Limits found by probing in September 2026:

* ``/intensity/{from}/{to}`` and ``/generation/{from}/{to}`` reject ranges over 31 days.
* ``/regional/intensity/{from}/{to}`` rejects ranges of 14 days or more.
* A range query returns every half-hour whose *end* falls in ``[from, to]``. To fetch the
  half-hours that *start* in ``[a, b)`` gridwatch therefore asks for ``from = a + 30 min``
  and ``to = b``.
* ``/generation`` and ``/regional/intensity`` silently stop at the end of the calendar year
  of ``from``: a request from 15 December to 10 January returns December only. Windows are
  therefore split at each year boundary. The last half-hour of a year (starting 23:30 on
  31 December) ends in the new year, so the API files it under the new year and the split
  falls just before it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any

import polars as pl

HALF_HOUR = timedelta(minutes=30)
API_TIME_FORMAT = "%Y-%m-%dT%H:%MZ"
INTENSITY_INDEX_VALUES = frozenset({"very low", "low", "moderate", "high", "very high"})


class ApiError(RuntimeError):
    """The API answered, but with an error document instead of data."""


class ParseError(ValueError):
    """A response did not match the documented structure."""


class Dataset(StrEnum):
    NATIONAL_INTENSITY = "national_intensity"
    GENERATION_MIX = "generation_mix"
    REGIONAL = "regional"
    FORECAST_SNAPSHOT = "forecast_snapshots"


MAX_WINDOW: Mapping[Dataset, timedelta] = {
    Dataset.NATIONAL_INTENSITY: timedelta(days=30),
    Dataset.GENERATION_MIX: timedelta(days=30),
    Dataset.REGIONAL: timedelta(days=13),
}

_TS = pl.Datetime("us")

NATIONAL_SCHEMA: dict[str, pl.DataType] = {
    "period_start_utc": _TS,
    "period_end_utc": _TS,
    "forecast_gco2_kwh": pl.Int32(),
    "actual_gco2_kwh": pl.Int32(),
    "intensity_index": pl.String(),
    "fetched_at_utc": _TS,
}
GENERATION_SCHEMA: dict[str, pl.DataType] = {
    "period_start_utc": _TS,
    "period_end_utc": _TS,
    "fuel": pl.String(),
    "share_pct": pl.Float64(),
    "fetched_at_utc": _TS,
}
REGIONAL_INTENSITY_SCHEMA: dict[str, pl.DataType] = {
    "period_start_utc": _TS,
    "period_end_utc": _TS,
    "region_id": pl.Int16(),
    "region_short_name": pl.String(),
    "dno_region": pl.String(),
    "forecast_gco2_kwh": pl.Int32(),
    "intensity_index": pl.String(),
    "fetched_at_utc": _TS,
}
REGIONAL_MIX_SCHEMA: dict[str, pl.DataType] = {
    "period_start_utc": _TS,
    "region_id": pl.Int16(),
    "fuel": pl.String(),
    "share_pct": pl.Float64(),
    "fetched_at_utc": _TS,
}
SNAPSHOT_SCHEMA: dict[str, pl.DataType] = {
    "issued_at_utc": _TS,
    "period_start_utc": _TS,
    "period_end_utc": _TS,
    "forecast_gco2_kwh": pl.Int32(),
    "intensity_index": pl.String(),
    "fetched_at_utc": _TS,
}


@dataclass(frozen=True)
class Window:
    """A half-open UTC interval ``[start, end)`` aligned to half-hours."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for value in (self.start, self.end):
            if value.tzinfo is None:
                raise ValueError("window bounds must be timezone-aware")
            if value != floor_half_hour(value):
                raise ValueError(f"window bound {value.isoformat()} is not on a half-hour")
        if self.end <= self.start:
            raise ValueError("window end must be after its start")


def floor_half_hour(value: datetime) -> datetime:
    """Round an aware datetime down to the start of its half-hour, in UTC."""
    value = value.astimezone(UTC)
    return value.replace(minute=0 if value.minute < 30 else 30, second=0, microsecond=0)


def ceil_half_hour(value: datetime) -> datetime:
    """Round an aware datetime up to the next half-hour boundary, in UTC."""
    floored = floor_half_hour(value)
    return floored if floored == value.astimezone(UTC) else floored + HALF_HOUR


def year_split_point(value: datetime) -> datetime:
    """Start of the last half-hour of ``value``'s API year (23:30 UTC on 31 December).

    The API assigns a half-hour to the year in which it *ends*, so the half-hour starting
    at 23:30 on 31 December belongs to the following year.
    """
    value = value.astimezone(UTC)
    boundary = datetime(value.year + 1, 1, 1, tzinfo=UTC) - HALF_HOUR
    if value >= boundary:
        boundary = datetime(value.year + 2, 1, 1, tzinfo=UTC) - HALF_HOUR
    return boundary


def chunk_window(window: Window, max_span: timedelta) -> Iterator[Window]:
    """Split ``window`` into pieces no longer than ``max_span`` that never cross a year."""
    if max_span < HALF_HOUR:
        raise ValueError("max_span must be at least 30 minutes")
    cursor = window.start
    while cursor < window.end:
        stop = min(cursor + max_span, window.end, year_split_point(cursor))
        yield Window(cursor, stop)
        cursor = stop


def format_api_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime(API_TIME_FORMAT)


def range_url(base: str, dataset: Dataset, window: Window) -> str:
    paths = {
        Dataset.NATIONAL_INTENSITY: "intensity",
        Dataset.GENERATION_MIX: "generation",
        Dataset.REGIONAL: "regional/intensity",
    }
    if dataset not in paths:
        raise ValueError(f"{dataset} has no range endpoint")
    # Ask for half-hours that *end* in [start + 30 min, end], i.e. start in [start, end).
    # The API rejects from == to, so a single half-hour asks for one extra, which the
    # parser then clips away.
    end = window.end if window.end - window.start > HALF_HOUR else window.end + HALF_HOUR
    return (
        f"{base.rstrip('/')}/{paths[dataset]}/"
        f"{format_api_time(window.start + HALF_HOUR)}/{format_api_time(end)}"
    )


def snapshot_url(base: str, issued_at: datetime) -> str:
    return f"{base.rstrip('/')}/intensity/{format_api_time(issued_at)}/fw48h"


def parse_api_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ParseError(f"expected a timestamp string, got {value!r}")
    try:
        return datetime.strptime(value, API_TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise ParseError(f"unexpected timestamp format {value!r}") from exc


def _data_list(payload: object) -> list[Any]:
    if not isinstance(payload, dict):
        raise ParseError("response is not a JSON object")
    if "error" in payload:
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else error
        raise ApiError(f"API returned an error: {message}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ParseError("response has no 'data' list")
    return data


def _period(record: object) -> tuple[datetime, datetime]:
    if not isinstance(record, dict):
        raise ParseError(f"expected an object per period, got {record!r}")
    start = parse_api_time(record.get("from"))
    end = parse_api_time(record.get("to"))
    if end - start != HALF_HOUR:
        raise ParseError(f"period {record.get('from')} to {record.get('to')} is not 30 minutes")
    return start, end


def _optional_int(value: object, what: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ParseError(f"{what} should be a number or null, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise ParseError(f"{what} should be a whole number, got {value!r}")
    return int(value)


def _index(value: object) -> str | None:
    if value is None:
        return None
    if value not in INTENSITY_INDEX_VALUES:
        raise ParseError(f"unknown intensity index {value!r}")
    return str(value)


def _mix(items: object, where: str) -> list[tuple[str, float]]:
    if not isinstance(items, list):
        raise ParseError(f"generation mix for {where} is not a list")
    out: list[tuple[str, float]] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("fuel"), str):
            raise ParseError(f"bad generation mix entry {item!r} for {where}")
        perc = item.get("perc")
        if isinstance(perc, bool) or not isinstance(perc, int | float):
            raise ParseError(f"generation share {perc!r} for {where} is not a number")
        out.append((item["fuel"], float(perc)))
    return out


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _in_window(start: datetime, window: Window | None) -> bool:
    return window is None or window.start <= start < window.end


def parse_national(payload: object, fetched_at: datetime, window: Window | None) -> pl.DataFrame:
    """Parse ``/intensity/...`` into one row per half-hour."""
    rows: list[dict[str, object]] = []
    for record in _data_list(payload):
        start, end = _period(record)
        if not _in_window(start, window):
            continue
        intensity = record.get("intensity")
        if not isinstance(intensity, dict):
            raise ParseError(f"period {record.get('from')} has no intensity object")
        rows.append(
            {
                "period_start_utc": _naive(start),
                "period_end_utc": _naive(end),
                "forecast_gco2_kwh": _optional_int(intensity.get("forecast"), "forecast"),
                "actual_gco2_kwh": _optional_int(intensity.get("actual"), "actual"),
                "intensity_index": _index(intensity.get("index")),
                "fetched_at_utc": _naive(fetched_at),
            }
        )
    return pl.DataFrame(rows, schema=NATIONAL_SCHEMA)


def parse_generation(payload: object, fetched_at: datetime, window: Window | None) -> pl.DataFrame:
    """Parse ``/generation/...`` into one row per half-hour and fuel."""
    rows: list[dict[str, object]] = []
    for record in _data_list(payload):
        start, end = _period(record)
        if not _in_window(start, window):
            continue
        for fuel, share in _mix(record.get("generationmix"), str(record.get("from"))):
            rows.append(
                {
                    "period_start_utc": _naive(start),
                    "period_end_utc": _naive(end),
                    "fuel": fuel,
                    "share_pct": share,
                    "fetched_at_utc": _naive(fetched_at),
                }
            )
    return pl.DataFrame(rows, schema=GENERATION_SCHEMA)


def parse_regional(
    payload: object, fetched_at: datetime, window: Window | None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Parse ``/regional/intensity/...`` into (intensity per region, mix per region and fuel)."""
    intensity_rows: list[dict[str, object]] = []
    mix_rows: list[dict[str, object]] = []
    for record in _data_list(payload):
        start, end = _period(record)
        if not _in_window(start, window):
            continue
        regions = record.get("regions")
        if not isinstance(regions, list):
            raise ParseError(f"period {record.get('from')} has no regions list")
        for region in regions:
            if not isinstance(region, dict):
                raise ParseError(f"bad region entry {region!r}")
            region_id = _optional_int(region.get("regionid"), "regionid")
            if region_id is None:
                raise ParseError(f"region without an id in period {record.get('from')}")
            intensity = region.get("intensity")
            if not isinstance(intensity, dict):
                raise ParseError(f"region {region_id} has no intensity object")
            intensity_rows.append(
                {
                    "period_start_utc": _naive(start),
                    "period_end_utc": _naive(end),
                    "region_id": region_id,
                    "region_short_name": region.get("shortname"),
                    "dno_region": region.get("dnoregion"),
                    "forecast_gco2_kwh": _optional_int(intensity.get("forecast"), "forecast"),
                    "intensity_index": _index(intensity.get("index")),
                    "fetched_at_utc": _naive(fetched_at),
                }
            )
            where = f"region {region_id} at {record.get('from')}"
            for fuel, share in _mix(region.get("generationmix"), where):
                mix_rows.append(
                    {
                        "period_start_utc": _naive(start),
                        "region_id": region_id,
                        "fuel": fuel,
                        "share_pct": share,
                        "fetched_at_utc": _naive(fetched_at),
                    }
                )
    return (
        pl.DataFrame(intensity_rows, schema=REGIONAL_INTENSITY_SCHEMA),
        pl.DataFrame(mix_rows, schema=REGIONAL_MIX_SCHEMA),
    )


def parse_snapshot(payload: object, issued_at: datetime, fetched_at: datetime) -> pl.DataFrame:
    """Parse a ``/intensity/{issued}/fw48h`` response into a forecast snapshot."""
    rows: list[dict[str, object]] = []
    for record in _data_list(payload):
        start, end = _period(record)
        intensity = record.get("intensity")
        if not isinstance(intensity, dict):
            raise ParseError(f"period {record.get('from')} has no intensity object")
        rows.append(
            {
                "issued_at_utc": _naive(issued_at),
                "period_start_utc": _naive(start),
                "period_end_utc": _naive(end),
                "forecast_gco2_kwh": _optional_int(intensity.get("forecast"), "forecast"),
                "intensity_index": _index(intensity.get("index")),
                "fetched_at_utc": _naive(fetched_at),
            }
        )
    return pl.DataFrame(rows, schema=SNAPSHOT_SCHEMA)


def windows_to_fetch(
    configured_start: datetime,
    stored_min: datetime | None,
    stored_max: datetime | None,
    now: datetime,
    refetch: timedelta,
) -> list[Window]:
    """Work out which ranges an incremental run must request.

    * Nothing stored yet: everything from ``configured_start`` to now.
    * ``configured_start`` moved earlier than the stored history: backfill that gap.
    * Always re-request the last ``refetch`` of stored history, because the API revises
      recent actual values, then continue to now.
    """
    end = floor_half_hour(now)
    start = floor_half_hour(configured_start)
    if start >= end:
        return []
    if stored_min is None or stored_max is None:
        return [Window(start, end)]
    windows: list[Window] = []
    stored_min = floor_half_hour(stored_min)
    if start < stored_min:
        windows.append(Window(start, stored_min))
    resume = max(start, floor_half_hour(stored_max + HALF_HOUR - refetch))
    if resume < end:
        windows.append(Window(resume, end))
    return windows


def internal_gaps(starts: Sequence[datetime]) -> list[Window]:
    """Windows of missing half-hours between the first and last of ``starts`` (aware UTC)."""
    ordered = sorted(set(starts))
    gaps: list[Window] = []
    for previous, current in pairwise(ordered):
        if current - previous > HALF_HOUR:
            gaps.append(Window(previous + HALF_HOUR, current))
    return gaps


def plan_requests(
    base: str, dataset: Dataset, windows: Sequence[Window]
) -> list[tuple[str, Window]]:
    """Expand windows into (url, window) pairs that respect the endpoint's range limit."""
    plan: list[tuple[str, Window]] = []
    for window in windows:
        for chunk in chunk_window(window, MAX_WINDOW[dataset]):
            plan.append((range_url(base, dataset, chunk), chunk))
    return plan
