"""Collect everything the dashboard shows from the warehouse and the model outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from gridwatch.forecast.service import (
    BACKTEST_PREDICTIONS_FILE,
    BACKTEST_SUMMARY_FILE,
    FORECAST_FILE,
)

Rows = list[dict[str, Any]]


@dataclass
class DashboardData:
    built_at_utc: str
    data_through_utc: str | None
    report_window: dict[str, Any]
    kpis: dict[str, Any]
    recent: Rows
    api_snapshot: Rows
    model_forecast: Rows
    weekly_profile: Rows
    start_slots: Rows
    strategies: Rows
    seasonal_profile: Rows
    monthly: Rows
    regions: Rows
    country_trend: Rows
    country_comparison: Rows
    backtest_by_horizon: Rows
    backtest_metrics: Rows
    backtest_meta: dict[str, Any] = field(default_factory=dict)


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> Rows:
    frame = con.sql(sql).pl()
    return _frame_rows(frame)


def _frame_rows(frame: pl.DataFrame) -> Rows:
    out: Rows = []
    for row in frame.iter_rows(named=True):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, datetime):
                clean[key] = value.strftime("%Y-%m-%dT%H:%M")
            elif isinstance(value, float) and value != value:  # NaN
                clean[key] = None
            elif hasattr(value, "isoformat"):
                clean[key] = value.isoformat()
            else:
                clean[key] = value
        out.append(clean)
    return out


def _backtest(outputs_dir: Path) -> tuple[Rows, Rows, dict[str, Any]]:
    predictions_path = outputs_dir / BACKTEST_PREDICTIONS_FILE
    summary_path = outputs_dir / BACKTEST_SUMMARY_FILE
    if not predictions_path.exists() or not summary_path.exists():
        return [], [], {}
    predictions = pl.read_parquet(predictions_path)
    methods = ["model", "naive_yesterday", "naive_last_week", "api_forecast"]
    complete = predictions.drop_nulls(subset=["actual", *methods]).filter(
        pl.all_horizontal([pl.col(c).is_not_nan() for c in ["actual", *methods]])
    )
    by_horizon = (
        complete.group_by("horizon")
        .agg([(pl.col(m) - pl.col("actual")).abs().mean().alias(m) for m in methods])
        .sort("horizon")
        .with_columns((pl.col("horizon") / 2).alias("hours_ahead"))
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    meta = {
        key: summary[key]
        for key in (
            "first_origin_utc",
            "last_origin_utc",
            "origins",
            "folds",
            "config",
            "interval_coverage",
            "model_win_rates_24_48h",
        )
    }
    return _frame_rows(by_horizon), list(summary["metrics"]), meta


def collect(warehouse: Path, outputs_dir: Path) -> DashboardData:
    if not warehouse.exists():
        raise FileNotFoundError(f"{warehouse} not found; run `gridwatch transform` first")
    with duckdb.connect(str(warehouse), read_only=True) as con:
        last = con.sql(
            "select max(period_start_utc) from marts.fct_national_intensity "
            "where actual_gco2_kwh is not null"
        ).fetchone()
        data_through = last[0].strftime("%Y-%m-%d %H:%M") if last and last[0] else None
        window = _rows(con, "select * from reporting.rpt_report_window")[0]
        kpis = _rows(
            con,
            """
            with recent as (
                select avg(actual_gco2_kwh) as last_7d_mean
                from marts.fct_national_intensity
                where period_start_utc > (
                    select max(period_start_utc) from marts.fct_national_intensity
                    where actual_gco2_kwh is not null
                ) - interval 7 day
            ),
            window_mean as (
                select avg(national.actual_gco2_kwh) as window_mean
                from marts.fct_national_intensity as national
                inner join marts.dim_date as dates on national.date_key = dates.date_key
                cross join reporting.rpt_report_window as report_window
                where dates.calendar_date between report_window.start_date
                    and report_window.end_date
            ),
            best as (
                select start_time_label as best_start, mean_job_gco2_kwh as best_start_mean
                from reporting.rpt_batch_job_start_slots
                order by rank_lowest, time_key
                limit 1
            ),
            worst as (
                select start_time_label as worst_start, mean_job_gco2_kwh as worst_start_mean
                from reporting.rpt_batch_job_start_slots
                order by rank_lowest desc, time_key
                limit 1
            ),
            saving as (
                select saving_vs_0900_pct as profile_saving_vs_0900_pct,
                       saving_vs_1700_pct as profile_saving_vs_1700_pct
                from reporting.rpt_batch_job_savings
                where strategy = 'profile_guided'
            ),
            api as (
                select mae_gco2_kwh as api_mae, mape_pct as api_mape
                from reporting.rpt_api_forecast_accuracy
                where scope = 'report_window'
            )
            select *
            from recent
            cross join window_mean
            cross join best
            cross join worst
            left join saving on true
            left join api on true
            """,
        )[0]
        recent = _rows(
            con,
            """
            select period_start_utc, actual_gco2_kwh, forecast_gco2_kwh
            from marts.fct_national_intensity
            where period_start_utc > (
                select max(period_start_utc) from marts.fct_national_intensity
                where actual_gco2_kwh is not null
            ) - interval 2 day
            order by period_start_utc
            """,
        )
        api_snapshot = _rows(
            con,
            """
            select period_start_utc, forecast_gco2_kwh, issued_at_utc
            from marts.fct_api_forecast_snapshots
            where issued_at_utc = (select max(issued_at_utc)
                                   from marts.fct_api_forecast_snapshots)
              and lead_minutes >= 0
            order by period_start_utc
            """,
        )
        weekly = _rows(
            con,
            "select iso_day_of_week, day_name, time_key, start_time_label, mean_actual_gco2_kwh "
            "from reporting.rpt_gb_weekly_profile order by iso_day_of_week, time_key",
        )
        start_slots = _rows(
            con,
            "select * from reporting.rpt_batch_job_start_slots order by time_key",
        )
        strategies = _rows(
            con,
            "select * from reporting.rpt_batch_job_savings order by sort_order",
        )
        seasonal = _rows(
            con,
            "select season, time_key, start_time_label, mean_actual_gco2_kwh "
            "from reporting.rpt_gb_seasonal_profile order by season, time_key",
        )
        monthly = _rows(
            con,
            "select month_start, year_month, mean_actual_gco2_kwh, p10_actual_gco2_kwh, "
            "p90_actual_gco2_kwh, mean_low_carbon_pct, coverage_pct "
            "from reporting.rpt_gb_monthly_intensity order by month_start",
        )
        regions = _rows(
            con,
            "select * from reporting.rpt_gb_regional_intensity "
            "where not is_aggregate order by mean_forecast_gco2_kwh",
        )
        trend = _rows(
            con,
            "select country_code, country_name, peer_group, is_aggregate, year, "
            "intensity_gco2e_kwh from reporting.rpt_country_intensity_trend "
            "order by country_code, year",
        )
        comparison = _rows(
            con,
            "select * from reporting.rpt_country_intensity_comparison order by intensity_gco2e_kwh",
        )
    forecast_path = outputs_dir / FORECAST_FILE
    model_forecast = _frame_rows(pl.read_parquet(forecast_path)) if forecast_path.exists() else []
    by_horizon, metrics, meta = _backtest(outputs_dir)
    return DashboardData(
        built_at_utc=datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        data_through_utc=data_through,
        report_window=window,
        kpis=kpis,
        recent=recent,
        api_snapshot=api_snapshot,
        model_forecast=model_forecast,
        weekly_profile=weekly,
        start_slots=start_slots,
        strategies=strategies,
        seasonal_profile=seasonal,
        monthly=monthly,
        regions=regions,
        country_trend=trend,
        country_comparison=comparison,
        backtest_by_horizon=by_horizon,
        backtest_metrics=metrics,
        backtest_meta=meta,
    )
