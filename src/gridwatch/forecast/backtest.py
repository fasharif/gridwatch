"""Rolling-origin backtest of the forecaster against naive baselines and the API forecast.

One forecast is issued per day at a fixed UTC hour over the test period. The model is
retrained every ``retrain_every_days`` using only data before the first origin of that
block (an expanding-origin, sliding-window scheme), so no forecast ever sees its targets.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import polars as pl

from gridwatch.forecast.baselines import (
    naive_same_slot_last_week,
    naive_same_slot_yesterday,
    persistence,
)
from gridwatch.forecast.calendar import calendar_for
from gridwatch.forecast.data import NationalSeries
from gridwatch.forecast.features import MAX_HORIZON, PERIODS_PER_DAY, TrailingStats
from gridwatch.forecast.metrics import score
from gridwatch.forecast.model import IntensityForecaster, ModelConfig

log = logging.getLogger(__name__)

METHODS = ("model", "persistence", "naive_yesterday", "naive_last_week", "api_forecast")
HORIZON_BANDS = (("0-24 h", 1, 48), ("24-48 h", 49, 96), ("0-48 h", 1, 96))
# Finer bands of lead time, to show where each method wins (persistence in the first hours).
LEAD_BANDS = (
    ("0-1 h", 1, 2),
    ("1-4 h", 3, 8),
    ("4-12 h", 9, 24),
    ("12-24 h", 25, 48),
    ("24-36 h", 49, 72),
    ("36-48 h", 73, 96),
)


@dataclass(frozen=True)
class BacktestConfig:
    test_days: int = 365
    retrain_every_days: int = 28
    origin_hour_utc: int = 0
    model: ModelConfig = field(default_factory=ModelConfig)

    def __post_init__(self) -> None:
        if self.test_days < 1:
            raise ValueError("test_days must be positive")
        if self.retrain_every_days < 1:
            raise ValueError("retrain_every_days must be positive")
        if not 0 <= self.origin_hour_utc <= 23:
            raise ValueError("origin_hour_utc must be 0-23")


@dataclass
class BacktestResult:
    predictions: pl.DataFrame
    metrics: pl.DataFrame
    coverage: pl.DataFrame
    monthly: pl.DataFrame
    wins: pl.DataFrame
    lead_times: pl.DataFrame
    folds: int
    first_origin: datetime
    last_origin: datetime


def origin_indices(series: NationalSeries, config: BacktestConfig) -> list[int]:
    """Daily origins (index of the last known half-hour) whose 48 h of targets are observed."""
    last_target = series.last_actual_index()
    last_origin = last_target - MAX_HORIZON
    # Issue time is origin_hour_utc; the last known half-hour starts 30 minutes earlier.
    start = series.start
    first_issue = start + timedelta(days=1)
    first_issue = first_issue.replace(hour=config.origin_hour_utc, minute=0)
    offset = int((first_issue - start) / timedelta(minutes=30)) - 1
    candidates = list(range(offset, last_origin + 1, PERIODS_PER_DAY))
    return candidates[-config.test_days :]


def run_backtest(
    series: NationalSeries,
    config: BacktestConfig,
    progress: Callable[[int, int], None] | None = None,
) -> BacktestResult:
    origins = origin_indices(series, config)
    if not origins:
        raise ValueError("not enough data for a single backtest origin")
    values = series.actual
    calendar = calendar_for(series.start, len(values) + MAX_HORIZON)
    stats = TrailingStats.of(values)
    horizons = np.arange(1, MAX_HORIZON + 1)
    blocks = [
        origins[i : i + config.retrain_every_days]
        for i in range(0, len(origins), config.retrain_every_days)
    ]
    frames: list[pl.DataFrame] = []
    for number, block in enumerate(blocks, start=1):
        cutoff = block[0]
        model = IntensityForecaster(config.model, horizons).fit(values, calendar, cutoff, stats)
        for origin in block:
            forecast = model.predict(values, calendar, origin, stats)
            targets = origin + horizons
            frames.append(
                pl.DataFrame(
                    {
                        "origin_utc": [series.timestamp(origin + 1)] * horizons.size,
                        "target_utc": [series.timestamp(int(t)) for t in targets],
                        "horizon": horizons,
                        "actual": values[targets],
                        "model": forecast.point,
                        "model_p10": forecast.lower,
                        "model_p90": forecast.upper,
                        "persistence": persistence(values, origin, horizons),
                        "naive_yesterday": naive_same_slot_yesterday(values, origin, horizons),
                        "naive_last_week": naive_same_slot_last_week(values, origin, horizons),
                        "api_forecast": series.api_forecast[targets],
                        "fold": [number] * horizons.size,
                    }
                )
            )
        if progress is not None:
            progress(number, len(blocks))
        log.info("backtest block %d/%d done (%d origins)", number, len(blocks), len(block))
    predictions = pl.concat(frames)
    return BacktestResult(
        predictions=predictions,
        metrics=summarise(predictions),
        coverage=interval_coverage(predictions),
        monthly=monthly_breakdown(predictions),
        wins=win_rates(predictions),
        lead_times=lead_time_breakdown(predictions),
        folds=len(blocks),
        first_origin=series.timestamp(origins[0] + 1),
        last_origin=series.timestamp(origins[-1] + 1),
    )


def methods_in(predictions: pl.DataFrame) -> list[str]:
    """The methods present, in METHODS order (outputs from older versions may lack some)."""
    return [m for m in METHODS if m in predictions.columns]


def complete_pairs(predictions: pl.DataFrame) -> pl.DataFrame:
    """Rows where the actual and every method have a value, so all methods share a sample."""
    columns = ["actual", *methods_in(predictions)]
    return predictions.drop_nulls(subset=columns).filter(
        pl.all_horizontal([pl.col(c).is_not_nan() for c in columns])
    )


def summarise(predictions: pl.DataFrame) -> pl.DataFrame:
    """MAE, RMSE, MAPE and bias per method and horizon band, on a common sample."""
    complete = complete_pairs(predictions)
    rows: list[dict[str, object]] = []
    for band, lo, hi in HORIZON_BANDS:
        part = complete.filter(pl.col("horizon").is_between(lo, hi))
        actual = part["actual"].to_numpy()
        for method in methods_in(predictions):
            metrics = score(part[method].to_numpy(), actual)
            rows.append({"horizon_band": band, "method": method, **metrics.as_dict()})
    return pl.DataFrame(rows)


def _inside() -> pl.Expr:
    return (pl.col("actual") >= pl.col("model_p10")) & (pl.col("actual") <= pl.col("model_p90"))


def interval_coverage(predictions: pl.DataFrame) -> pl.DataFrame:
    """Share of actuals inside the model's 10-90% interval (80% if well calibrated)."""
    complete = complete_pairs(predictions)
    rows: list[dict[str, object]] = []
    for band, lo, hi in HORIZON_BANDS:
        part = complete.filter(pl.col("horizon").is_between(lo, hi))
        inside = part.filter(_inside()).height
        rows.append(
            {
                "horizon_band": band,
                "n": part.height,
                "coverage_pct": 100.0 * inside / part.height if part.height else None,
                "mean_width": float(
                    np.mean(part["model_p90"].to_numpy() - part["model_p10"].to_numpy())
                )
                if part.height
                else None,
            }
        )
    return pl.DataFrame(rows)


def lead_time_breakdown(predictions: pl.DataFrame) -> pl.DataFrame:
    """MAE per method and interval coverage for each band of lead time."""
    complete = complete_pairs(predictions)
    methods = methods_in(predictions)
    rows: list[dict[str, object]] = []
    for band, lo, hi in LEAD_BANDS:
        part = complete.filter(pl.col("horizon").is_between(lo, hi))
        if part.height == 0:
            continue
        actual = part["actual"].to_numpy()
        row: dict[str, object] = {"lead_band": band, "n": part.height}
        for method in methods:
            row[f"mae_{method}"] = float(np.mean(np.abs(part[method].to_numpy() - actual)))
        row["coverage_pct"] = 100.0 * part.filter(_inside()).height / part.height
        rows.append(row)
    return pl.DataFrame(rows)


def monthly_breakdown(predictions: pl.DataFrame) -> pl.DataFrame:
    """MAE per calendar month of the target for the 24-48 h band, to show where each wins.

    The last column is the model's 10-90% interval coverage in that month.
    """
    part = complete_pairs(predictions).filter(pl.col("horizon") > PERIODS_PER_DAY)
    return (
        part.with_columns(pl.col("target_utc").dt.strftime("%Y-%m").alias("month"))
        .group_by("month")
        .agg(
            [pl.len().alias("n")]
            + [
                (pl.col(m) - pl.col("actual")).abs().mean().alias(f"mae_{m}")
                for m in methods_in(predictions)
            ]
            + [(100.0 * _inside().mean()).alias("coverage_pct")]
        )
        .sort("month")
    )


def win_rates(predictions: pl.DataFrame) -> pl.DataFrame:
    """Share of forecast days (24-48 h band) on which the model beats each other method."""
    methods = methods_in(predictions)
    part = complete_pairs(predictions).filter(pl.col("horizon") > PERIODS_PER_DAY)
    daily = part.group_by("origin_utc").agg(
        [(pl.col(m) - pl.col("actual")).abs().mean().alias(m) for m in methods]
    )
    rows = []
    for other in methods[1:]:
        wins = daily.filter(pl.col("model") < pl.col(other)).height
        rows.append(
            {
                "compared_with": other,
                "days": daily.height,
                "model_better_days": wins,
                "model_better_pct": 100.0 * wins / daily.height if daily.height else None,
            }
        )
    return pl.DataFrame(rows)
