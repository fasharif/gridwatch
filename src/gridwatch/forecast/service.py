"""Entry points that read the warehouse, run the forecaster and write Parquet outputs."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from gridwatch.config import Settings
from gridwatch.forecast.backtest import BacktestConfig, BacktestResult, run_backtest
from gridwatch.forecast.calendar import calendar_for
from gridwatch.forecast.data import NationalSeries, load_national_series
from gridwatch.forecast.features import MAX_HORIZON, TrailingStats
from gridwatch.forecast.model import IntensityForecaster, ModelConfig

log = logging.getLogger(__name__)

FORECAST_FILE = "forecast_latest.parquet"
BACKTEST_PREDICTIONS_FILE = "backtest_predictions.parquet"
BACKTEST_SUMMARY_FILE = "backtest_summary.json"


def forecast_next(series: NationalSeries, config: ModelConfig) -> pl.DataFrame:
    """Train on everything known and forecast the 48 hours after the last actual value."""
    origin = series.last_actual_index()
    values = series.actual
    calendar = calendar_for(series.start, origin + MAX_HORIZON + 1)
    horizons = np.arange(1, MAX_HORIZON + 1)
    stats = TrailingStats.of(values[: origin + 1])
    model = IntensityForecaster(config, horizons).fit(values[: origin + 1], calendar, origin, stats)
    result = model.predict(values[: origin + 1], calendar, origin, stats)
    issued = series.timestamp(origin + 1)
    return pl.DataFrame(
        {
            "issued_at_utc": [issued] * horizons.size,
            "period_start_utc": [series.timestamp(origin + int(h)) for h in horizons],
            "horizon": horizons,
            "forecast_gco2_kwh": result.point,
            "p10_gco2_kwh": result.lower,
            "p90_gco2_kwh": result.upper,
        }
    )


def run_forecast(settings: Settings, config: ModelConfig | None = None) -> pl.DataFrame:
    series = load_national_series(settings.warehouse_path)
    frame = forecast_next(series, config or ModelConfig())
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(settings.outputs_dir / FORECAST_FILE)
    log.info("48-hour forecast issued at %s written", frame["issued_at_utc"][0])
    return frame


def summary_json(result: BacktestResult, config: BacktestConfig) -> dict[str, object]:
    return {
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first_origin_utc": result.first_origin.strftime("%Y-%m-%dT%H:%MZ"),
        "last_origin_utc": result.last_origin.strftime("%Y-%m-%dT%H:%MZ"),
        "origins": result.predictions["origin_utc"].n_unique(),
        "folds": result.folds,
        "config": {
            "test_days": config.test_days,
            "retrain_every_days": config.retrain_every_days,
            "origin_hour_utc": config.origin_hour_utc,
            "model": asdict(config.model),
        },
        "metrics": result.metrics.to_dicts(),
        "interval_coverage": result.coverage.to_dicts(),
        "mae_by_lead": result.lead_times.to_dicts(),
        "monthly_mae_24_48h": result.monthly.to_dicts(),
        "model_win_rates_24_48h": result.wins.to_dicts(),
    }


def validation_suffix(until: datetime, config: BacktestConfig) -> str:
    """File-name suffix of a validation run: the cutoff and the settings being compared.

    Each combination of training window and calibration period gets its own files, so a
    run never overwrites the result of another one.
    """
    return (
        f"_until_{until:%Y%m%d}_train{config.model.train_days}_cal{config.model.calibration_days}"
    )


def validation_summaries(outputs_dir: Path) -> list[dict[str, Any]]:
    """Every stored validation-run summary (``backtest --until``), oldest cutoff first."""
    summaries = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(outputs_dir.glob("backtest_summary_until_*.json"))
    ]
    return sorted(
        summaries,
        key=lambda s: (
            str(s.get("until_utc")),
            s["config"]["model"]["train_days"],
            s["config"]["model"]["calibration_days"],
        ),
    )


def run_backtest_job(
    settings: Settings, config: BacktestConfig, until: datetime | None = None
) -> dict[str, object]:
    """Run the backtest and write its outputs.

    With ``until`` only data before that time is used (for choosing settings on a period
    before the test year), and the outputs get a suffix naming the cutoff and the settings,
    so the main results and other validation runs are kept.
    """
    series = load_national_series(settings.warehouse_path)
    suffix = ""
    if until is not None:
        series = series.truncate(until)
        suffix = validation_suffix(until, config)
    result = run_backtest(series, config)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    predictions_name = BACKTEST_PREDICTIONS_FILE.replace(".parquet", f"{suffix}.parquet")
    result.predictions.write_parquet(settings.outputs_dir / predictions_name)
    summary = summary_json(result, config)
    summary["until_utc"] = None if until is None else until.strftime("%Y-%m-%dT%H:%MZ")
    path: Path = settings.outputs_dir / BACKTEST_SUMMARY_FILE.replace(".json", f"{suffix}.json")
    path.write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    log.info("backtest over %s origins written to %s", summary["origins"], path)
    return summary
