from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

import numpy as np
import polars as pl
import pytest

from gridwatch.forecast.backtest import (
    BacktestConfig,
    interval_coverage,
    origin_indices,
    run_backtest,
    summarise,
    win_rates,
)
from gridwatch.forecast.calendar import calendar_for
from gridwatch.forecast.data import NationalSeries, SeriesError
from gridwatch.forecast.features import MAX_HORIZON
from gridwatch.forecast.metrics import score
from gridwatch.forecast.model import IntensityForecaster, ModelConfig, NotEnoughHistoryError
from gridwatch.forecast.service import forecast_next
from tests.helpers import synthetic_series

SMALL = ModelConfig(train_days=40, origin_step=6, max_iter=60, calibration_days=8)


def test_score_known_values() -> None:
    metrics = score(np.array([110.0, 90.0, np.nan]), np.array([100.0, 100.0, 50.0]))
    assert metrics.n == 2
    assert metrics.mae == pytest.approx(10.0)
    assert metrics.rmse == pytest.approx(10.0)
    assert metrics.mape == pytest.approx(10.0)
    assert metrics.bias == pytest.approx(0.0)


def test_score_skips_zero_actuals_for_mape_only() -> None:
    metrics = score(np.array([5.0, 110.0]), np.array([0.0, 100.0]))
    assert metrics.mae == pytest.approx(7.5)
    assert metrics.mape == pytest.approx(10.0)


def test_score_edge_cases() -> None:
    assert score(np.array([np.nan]), np.array([1.0])).n == 0
    with pytest.raises(ValueError, match="same shape"):
        score(np.zeros(2), np.zeros(3))


def test_model_learns_the_daily_cycle() -> None:
    series = synthetic_series(days=70)
    calendar = calendar_for(series.start, len(series) + MAX_HORIZON)
    cutoff = 58 * 48 - 1
    model = IntensityForecaster(SMALL).fit(series.actual, calendar, cutoff)
    model_errors, flat_errors = [], []
    for day in range(5):
        origin = cutoff + day * 48
        forecast = model.predict(series.actual, calendar, origin)
        actual = series.actual[origin + forecast.horizons]
        model_errors.append(score(forecast.point, actual).mae)
        flat = np.full(actual.shape, np.mean(series.actual[origin - 47 : origin + 1]))
        flat_errors.append(score(flat, actual).mae)
        assert np.all(forecast.lower <= forecast.point)
        assert np.all(forecast.point <= forecast.upper)
    # The series swings +-40 around its daily mean with noise of 8: a model that has
    # learned the cycle must be far better than a flat line at the recent mean.
    assert np.mean(model_errors) < 0.5 * np.mean(flat_errors)
    assert np.mean(model_errors) < 15


def test_fit_ignores_values_after_cutoff() -> None:
    series = synthetic_series(days=70)
    calendar = calendar_for(series.start, len(series) + MAX_HORIZON)
    cutoff = 55 * 48
    first = IntensityForecaster(SMALL).fit(series.actual, calendar, cutoff)
    corrupted = series.actual.copy()
    corrupted[cutoff + 1 :] = -500.0
    second = IntensityForecaster(SMALL).fit(corrupted, calendar, cutoff)
    a = first.predict(series.actual, calendar, cutoff)
    b = second.predict(series.actual, calendar, cutoff)
    np.testing.assert_allclose(a.point, b.point)


def test_not_enough_history() -> None:
    series = synthetic_series(days=20)
    calendar = calendar_for(series.start, len(series) + MAX_HORIZON)
    with pytest.raises(NotEnoughHistoryError):
        IntensityForecaster(SMALL).fit(series.actual, calendar, 10 * 48)


def test_predict_before_fit() -> None:
    series = synthetic_series(days=20)
    calendar = calendar_for(series.start, len(series) + MAX_HORIZON)
    with pytest.raises(RuntimeError, match="fit"):
        IntensityForecaster(SMALL).predict(series.actual, calendar, 500)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"train_days": 7}, "train_days"),
        ({"origin_step": 0}, "origin_step"),
        ({"quantiles": (0.9, 0.1)}, "quantiles"),
        ({"train_days": 30, "calibration_days": 30}, "calibration_days"),
    ],
)
def test_model_config_validation(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ModelConfig(**kwargs)  # type: ignore[arg-type]


def test_backtest_on_synthetic_series() -> None:
    series = synthetic_series(days=75)
    config = BacktestConfig(test_days=6, retrain_every_days=3, model=SMALL)
    result = run_backtest(series, config)
    assert result.folds == 2
    assert result.predictions["origin_utc"].n_unique() == 6
    assert result.predictions.height == 6 * MAX_HORIZON
    assert set(result.metrics["method"]) == {
        "model",
        "naive_yesterday",
        "naive_last_week",
        "api_forecast",
    }
    assert result.metrics.height == 12
    assert result.first_origin.hour == 0
    # every forecast is issued at midnight UTC, after its last known half-hour
    assert (result.predictions["target_utc"] >= result.predictions["origin_utc"]).all()
    assert result.coverage.height == 3
    assert result.wins.height == 3


def test_origins_leave_room_for_targets() -> None:
    series = synthetic_series(days=30)
    origins = origin_indices(series, BacktestConfig(test_days=400))
    assert origins[-1] + MAX_HORIZON <= series.last_actual_index()
    assert all(b - a == 48 for a, b in pairwise(origins))
    assert series.timestamp(origins[0] + 1).hour == 0


def test_summaries_use_a_common_sample() -> None:
    predictions = pl.DataFrame(
        {
            "origin_utc": [datetime(2026, 1, 1)] * 2,
            "horizon": [1, 60],
            "actual": [100.0, 100.0],
            "model": [110.0, 90.0],
            "model_p10": [80.0, 95.0],
            "model_p90": [120.0, 99.0],
            "naive_yesterday": [120.0, None],
            "naive_last_week": [100.0, 100.0],
            "api_forecast": [101.0, 99.0],
        }
    )
    metrics = summarise(predictions)
    row = metrics.filter((pl.col("method") == "model") & (pl.col("horizon_band") == "0-48 h"))
    assert row["n"].item() == 1  # the second pair lacks a naive value
    coverage = interval_coverage(predictions)
    assert coverage.filter(pl.col("horizon_band") == "0-24 h")["coverage_pct"].item() == 100.0
    assert win_rates(predictions).height == 3


def test_forecast_next_starts_after_last_actual() -> None:
    series = synthetic_series(days=50)
    frame = forecast_next(series, ModelConfig(train_days=30, max_iter=40, calibration_days=6))
    assert frame.height == MAX_HORIZON
    assert frame["period_start_utc"][0] == series.timestamp(series.last_actual_index() + 1)
    assert (frame["p10_gco2_kwh"] <= frame["p90_gco2_kwh"]).all()


def test_series_rejects_gaps() -> None:
    frame = pl.DataFrame(
        {
            "period_start_utc": [datetime(2026, 1, 1, 0), datetime(2026, 1, 1, 1)],
            "actual_gco2_kwh": [1, 2],
            "forecast_gco2_kwh": [1, 2],
        }
    )
    with pytest.raises(SeriesError, match="gaps"):
        NationalSeries.from_frame(frame)


def test_series_helpers() -> None:
    frame = pl.DataFrame(
        {
            "period_start_utc": [datetime(2026, 1, 1, 0), datetime(2026, 1, 1, 0, 30)],
            "actual_gco2_kwh": [10, None],
            "forecast_gco2_kwh": [11, 12],
        }
    )
    series = NationalSeries.from_frame(frame)
    assert series.last_actual_index() == 0
    assert series.index_of(datetime(2026, 1, 1, 0, 30, tzinfo=UTC)) == 1
    with pytest.raises(SeriesError, match="grid"):
        series.index_of(datetime(2026, 1, 1, 0, 10, tzinfo=UTC))
    with pytest.raises(SeriesError, match="empty"):
        NationalSeries.from_frame(frame.head(0))


def test_truncate_keeps_data_before_cutoff() -> None:
    series = synthetic_series(days=10)
    cut = series.truncate(datetime(2025, 1, 5, tzinfo=UTC))
    assert len(cut) == 4 * 48
    assert cut.timestamp(len(cut) - 1) == datetime(2025, 1, 4, 23, 30)
    with pytest.raises(SeriesError, match="no data before"):
        series.truncate(datetime(2024, 12, 1, tzinfo=UTC))
