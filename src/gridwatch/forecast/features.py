"""Feature construction for direct multi-horizon forecasting.

A forecast is issued at an *origin*: the index of the last half-hour whose actual value is
known. For a horizon ``h`` (1..96 half-hours) the target is index ``origin + h``. Every
feature uses values at indices ``<= origin`` only, plus calendar facts about the target
time, which are known in advance. ``tests/test_features.py`` checks this by corrupting all
values after the origin and asserting the features do not change.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view

from gridwatch.forecast.calendar import CalendarArrays

PERIODS_PER_DAY = 48
PERIODS_PER_WEEK = 7 * PERIODS_PER_DAY
MAX_HORIZON = 2 * PERIODS_PER_DAY  # 48 hours

BASE_FEATURES: tuple[str, ...] = (
    "horizon",
    "target_slot",
    "target_day_of_week",
    "target_is_holiday",
    "target_doy_sin",
    "target_doy_cos",
    "last_value",
    "previous_value",
    "mean_24h",
    "mean_7d",
    "std_24h",
    "min_24h",
    "max_24h",
    "change_24h",
    "same_slot_latest_day",
    "same_slot_profile_7d",
    "same_slot_week_before",
    "same_slot_two_weeks_before",
)
# Level features restated relative to the last known value. The model predicts the change
# from the last value, and a tree cannot subtract two inputs, so it is given the differences.
RELATIVE_TO_LAST: tuple[str, ...] = (
    "previous_value",
    "mean_24h",
    "mean_7d",
    "min_24h",
    "max_24h",
    "same_slot_latest_day",
    "same_slot_profile_7d",
    "same_slot_week_before",
    "same_slot_two_weeks_before",
)
FEATURE_NAMES: tuple[str, ...] = BASE_FEATURES + tuple(
    f"{name}_minus_last" for name in RELATIVE_TO_LAST
)

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


def _trailing(values: FloatArray, window: int, func: str) -> FloatArray:
    """Trailing statistic over ``window`` values ending at each index (NaN-aware)."""
    padded = np.concatenate([np.full(window - 1, np.nan), values])
    view = sliding_window_view(padded, window)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        if func == "mean":
            return np.asarray(np.nanmean(view, axis=1), dtype=np.float64)
        if func == "std":
            return np.asarray(np.nanstd(view, axis=1), dtype=np.float64)
        if func == "min":
            return np.asarray(np.nanmin(view, axis=1), dtype=np.float64)
        if func == "max":
            return np.asarray(np.nanmax(view, axis=1), dtype=np.float64)
    raise ValueError(f"unknown trailing statistic {func!r}")


@dataclass(frozen=True)
class TrailingStats:
    """Per-index trailing statistics, computed once per series."""

    mean_24h: FloatArray
    mean_7d: FloatArray
    std_24h: FloatArray
    min_24h: FloatArray
    max_24h: FloatArray

    @classmethod
    def of(cls, values: FloatArray) -> TrailingStats:
        return cls(
            mean_24h=_trailing(values, PERIODS_PER_DAY, "mean"),
            mean_7d=_trailing(values, PERIODS_PER_WEEK, "mean"),
            std_24h=_trailing(values, PERIODS_PER_DAY, "std"),
            min_24h=_trailing(values, PERIODS_PER_DAY, "min"),
            max_24h=_trailing(values, PERIODS_PER_DAY, "max"),
        )


def _take(values: FloatArray, index: IntArray) -> FloatArray:
    """values[index] with NaN where the index falls outside the series."""
    out = np.full(index.shape, np.nan)
    ok = (index >= 0) & (index < len(values))
    out[ok] = values[index[ok]]
    return out


def latest_same_slot_offset(horizons: IntArray) -> IntArray:
    """Periods back from the target to the most recent same-slot value known at the origin."""
    days_back = np.ceil(horizons / PERIODS_PER_DAY).astype(np.int64)
    return days_back * PERIODS_PER_DAY


def build_features(
    values: FloatArray,
    calendar: CalendarArrays,
    origins: IntArray,
    horizons: IntArray,
    stats: TrailingStats | None = None,
) -> FloatArray:
    """Feature matrix with one row per (origin, horizon) pair, origins varying slowest.

    ``values`` holds actuals (NaN when unknown); ``calendar`` must cover every target index.
    """
    origins = np.asarray(origins, dtype=np.int64)
    horizons = np.asarray(horizons, dtype=np.int64)
    if horizons.min(initial=1) < 1 or horizons.max(initial=1) > MAX_HORIZON:
        raise ValueError(f"horizons must be between 1 and {MAX_HORIZON}")
    if origins.size and (origins.min() < 0 or origins.max() >= len(values)):
        raise ValueError("origins must index into values")
    needed = int(origins.max(initial=0) + horizons.max(initial=0)) + 1
    if len(calendar) < needed:
        raise ValueError(f"calendar covers {len(calendar)} periods, {needed} needed")
    if stats is None:
        stats = TrailingStats.of(values)

    o = np.repeat(origins, horizons.size)
    h = np.tile(horizons, origins.size)
    t = o + h
    latest = t - latest_same_slot_offset(h)
    profile_parts = [_take(values, latest - k * PERIODS_PER_DAY) for k in range(7)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        profile = np.nanmean(np.vstack(profile_parts), axis=0)

    columns = [
        h.astype(np.float64),
        calendar.slot[t].astype(np.float64),
        calendar.day_of_week[t].astype(np.float64),
        calendar.is_holiday[t].astype(np.float64),
        calendar.doy_sin[t],
        calendar.doy_cos[t],
        values[o],
        _take(values, o - 1),
        stats.mean_24h[o],
        stats.mean_7d[o],
        stats.std_24h[o],
        stats.min_24h[o],
        stats.max_24h[o],
        values[o] - _take(values, o - PERIODS_PER_DAY),
        _take(values, latest),
        profile,
        _take(values, t - PERIODS_PER_WEEK),
        _take(values, t - 2 * PERIODS_PER_WEEK),
    ]
    last = columns[BASE_FEATURES.index("last_value")]
    columns += [columns[BASE_FEATURES.index(name)] - last for name in RELATIVE_TO_LAST]
    matrix = np.column_stack(columns)
    if matrix.shape[1] != len(FEATURE_NAMES):
        raise AssertionError("feature list and FEATURE_NAMES are out of step")
    return matrix
