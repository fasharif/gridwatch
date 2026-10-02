from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pytest

from gridwatch.forecast.baselines import naive_same_slot_last_week, naive_same_slot_yesterday
from gridwatch.forecast.calendar import calendar_for, uk_bank_holidays
from gridwatch.forecast.features import (
    FEATURE_NAMES,
    MAX_HORIZON,
    TrailingStats,
    build_features,
    latest_same_slot_offset,
)
from tests.helpers import synthetic_series

HORIZONS = np.arange(1, MAX_HORIZON + 1)


def test_features_ignore_everything_after_the_origin() -> None:
    series = synthetic_series(days=30)
    calendar = calendar_for(series.start, len(series) + MAX_HORIZON)
    origin = 20 * 48 - 1
    clean = build_features(series.actual, calendar, np.array([origin]), HORIZONS)
    corrupted = series.actual.copy()
    corrupted[origin + 1 :] = 1e6
    leaked = build_features(corrupted, calendar, np.array([origin]), HORIZONS)
    np.testing.assert_array_equal(clean, leaked)


def test_feature_matrix_shape_and_order() -> None:
    series = synthetic_series(days=30)
    calendar = calendar_for(series.start, len(series) + MAX_HORIZON)
    origins = np.array([500, 900])
    matrix = build_features(series.actual, calendar, origins, HORIZONS)
    assert matrix.shape == (2 * MAX_HORIZON, len(FEATURE_NAMES))
    horizon = matrix[:, FEATURE_NAMES.index("horizon")]
    np.testing.assert_array_equal(horizon[:MAX_HORIZON], HORIZONS)
    last = matrix[:, FEATURE_NAMES.index("last_value")]
    assert set(last[:MAX_HORIZON]) == {series.actual[500]}


def test_same_slot_feature_uses_latest_known_day() -> None:
    series = synthetic_series(days=30)
    calendar = calendar_for(series.start, len(series) + MAX_HORIZON)
    origin = 600
    matrix = build_features(series.actual, calendar, np.array([origin]), HORIZONS)
    column = matrix[:, FEATURE_NAMES.index("same_slot_latest_day")]
    assert column[0] == series.actual[origin + 1 - 48]
    assert column[48] == series.actual[origin + 49 - 96]


def test_latest_same_slot_offset() -> None:
    np.testing.assert_array_equal(
        latest_same_slot_offset(np.array([1, 48, 49, 96])), np.array([48, 48, 96, 96])
    )


def test_invalid_inputs() -> None:
    series = synthetic_series(days=20)
    calendar = calendar_for(series.start, len(series) + MAX_HORIZON)
    with pytest.raises(ValueError, match="horizons"):
        build_features(series.actual, calendar, np.array([400]), np.array([0]))
    with pytest.raises(ValueError, match="origins"):
        build_features(series.actual, calendar, np.array([len(series)]), HORIZONS)
    short = calendar_for(series.start, 10)
    with pytest.raises(ValueError, match="calendar covers"):
        build_features(series.actual, short, np.array([400]), HORIZONS)


def test_trailing_stats_are_causal() -> None:
    values = np.arange(100, dtype=float)
    stats = TrailingStats.of(values)
    assert stats.mean_24h[47] == pytest.approx(np.mean(values[:48]))
    assert stats.max_24h[60] == 60
    assert np.isnan(TrailingStats.of(np.full(5, np.nan)).mean_24h).all()


def test_calendar_uses_uk_local_time() -> None:
    # 2026-03-29 is the spring clock change: 01:00 UTC is 02:00 BST.
    calendar = calendar_for(datetime(2026, 3, 29, 0, 0, tzinfo=UTC), 4)
    assert calendar.slot.tolist() == [0, 1, 4, 5]
    winter = calendar_for(datetime(2026, 1, 5, 9, 0, tzinfo=UTC), 1)
    assert winter.slot.tolist() == [18]
    assert winter.day_of_week.tolist() == [0]


def test_calendar_marks_bank_holidays() -> None:
    calendar = calendar_for(datetime(2025, 12, 25, 12, 0, tzinfo=UTC), 1)
    assert calendar.is_holiday.tolist() == [True]
    assert date(2026, 8, 31) in uk_bank_holidays([2026])


def test_calendar_rejects_naive_start() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        calendar_for(datetime(2026, 1, 1), 1)


def test_naive_baselines() -> None:
    values = np.arange(1000, dtype=float)
    origin = 700
    yesterday = naive_same_slot_yesterday(values, origin, HORIZONS)
    assert yesterday[0] == values[origin + 1 - 48]
    assert yesterday[95] == values[origin + 96 - 96]
    week = naive_same_slot_last_week(values, origin, HORIZONS)
    assert week[0] == values[origin + 1 - 336]
    assert np.isnan(naive_same_slot_last_week(values, 100, HORIZONS)[0])
