"""Seasonal naive baselines. Both use only values known at the forecast origin."""

from __future__ import annotations

import numpy as np

from gridwatch.forecast.features import (
    PERIODS_PER_WEEK,
    FloatArray,
    IntArray,
    latest_same_slot_offset,
)


def _take(values: FloatArray, index: IntArray) -> FloatArray:
    out = np.full(index.shape, np.nan)
    ok = (index >= 0) & (index < len(values))
    out[ok] = values[index[ok]]
    return out


def naive_same_slot_yesterday(values: FloatArray, origin: int, horizons: IntArray) -> FloatArray:
    """Same half-hour on the most recent day fully known at the origin.

    For an origin at midnight this is "the same half-hour yesterday" for both forecast days.
    """
    targets = origin + np.asarray(horizons, dtype=np.int64)
    return _take(values, targets - latest_same_slot_offset(np.asarray(horizons, dtype=np.int64)))


def naive_same_slot_last_week(values: FloatArray, origin: int, horizons: IntArray) -> FloatArray:
    """Same half-hour seven days before the target (always known for horizons up to 48 h)."""
    targets = origin + np.asarray(horizons, dtype=np.int64)
    return _take(values, targets - PERIODS_PER_WEEK)
