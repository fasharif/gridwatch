"""Render the numbers behind docs/findings.md as Markdown tables.

Everything here is read from the reporting marts and the backtest summary, so the tables in
the committed snapshot (docs/generated/report.md) can be regenerated with one command.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from gridwatch.forecast.service import BACKTEST_SUMMARY_FILE

METHOD_LABELS = {
    "model": "gridwatch model (gradient boosting)",
    "naive_yesterday": "Naive: same half-hour, last known day",
    "naive_last_week": "Naive: same half-hour, one week earlier",
    "api_forecast": "API retained forecast (short lead)",
}


def _fmt(value: Any, decimals: int) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if math.isnan(value):
            return "n/a"
        return f"{value:,.{decimals}f}"
    return str(value)


def markdown_table(
    frame: pl.DataFrame, headers: Sequence[str] | None = None, decimals: int = 1
) -> str:
    """A GitHub-flavoured Markdown table; numbers right-aligned."""
    names = list(headers) if headers is not None else frame.columns
    if len(names) != frame.width:
        raise ValueError("one header per column is required")
    numeric = [dtype.is_numeric() for dtype in frame.dtypes]
    lines = [
        "| " + " | ".join(names) + " |",
        "| " + " | ".join("---:" if n else "---" for n in numeric) + " |",
    ]
    for row in frame.iter_rows():
        lines.append("| " + " | ".join(_fmt(v, decimals) for v in row) + " |")
    return "\n".join(lines)


def _q(con: duckdb.DuckDBPyConnection, sql: str) -> pl.DataFrame:
    return con.sql(sql).pl()


def build_report(warehouse: Path, outputs_dir: Path) -> str:
    with duckdb.connect(str(warehouse), read_only=True) as con:
        window = _q(con, "select * from reporting.rpt_report_window").row(0, named=True)
        coverage = _q(
            con,
            """
            select
                strftime(min(period_start_utc), '%Y-%m-%d %H:%M') as first_period_utc,
                strftime(max(period_start_utc) filter (where actual_gco2_kwh is not null),
                         '%Y-%m-%d %H:%M') as last_actual_utc,
                count(*) as half_hours,
                count(*) filter (where is_missing_from_source) as missing_from_api,
                count(*) filter (where is_actual_implausible) as implausible_actuals,
                count(*) filter (where is_forecast_implausible) as implausible_forecasts
            from marts.fct_national_intensity
            """,
        )
        best_slots = _q(
            con,
            """
            select start_time_label, mean_job_gco2_kwh, working_day_mean_job_gco2_kwh,
                   non_working_day_mean_job_gco2_kwh, rank_lowest
            from reporting.rpt_batch_job_start_slots
            where rank_lowest <= 5 or rank_lowest >= 46
            order by rank_lowest
            """,
        )
        best_week = _q(
            con,
            """
            select day_name, start_time_label, mean_job_gco2_kwh, jobs
            from reporting.rpt_batch_job_week_slots
            where rank_lowest <= 5
            order by rank_lowest
            """,
        )
        worst_week = _q(
            con,
            """
            select day_name, start_time_label, mean_job_gco2_kwh, jobs
            from reporting.rpt_batch_job_week_slots
            order by rank_lowest desc
            limit 3
            """,
        )
        savings = _q(
            con,
            """
            select strategy_label, days, mean_job_gco2_kwh, saving_vs_0000_pct,
                   saving_vs_0900_pct, saving_vs_1700_pct, kg_co2_per_run
            from reporting.rpt_batch_job_savings
            order by sort_order
            """,
        )
        job = _q(
            con,
            "select any_value(illustrative_job_kw) kw, any_value(batch_job_hours) h "
            "from reporting.rpt_batch_job_savings",
        ).row(0, named=True)
        countries = _q(
            con,
            """
            select country_name, comparison_year, intensity_gco2e_kwh, ratio_to_uk,
                   ratio_to_eu, intensity_2015_gco2e_kwh, change_since_2015_pct,
                   gas_share_pct, other_fossil_share_pct, nuclear_share_pct,
                   solar_share_pct + wind_share_pct as wind_solar_share_pct
            from reporting.rpt_country_intensity_comparison
            order by intensity_gco2e_kwh
            """,
        )
        trend = _q(
            con,
            """
            pivot (
                select country_name, year, round(intensity_gco2e_kwh, 0) as intensity
                from reporting.rpt_country_intensity_trend
                where year in (2000, 2010, 2015, 2019, 2020, 2021, 2022, 2023, 2024, 2025)
            )
            on year using any_value(intensity)
            order by country_name
            """,
        )
        annual_gb = _q(
            con,
            """
            select calendar_year, mean_actual_gco2_kwh, mean_low_carbon_pct, mean_wind_pct,
                   mean_gas_pct, mean_coal_pct, is_complete_year
            from reporting.rpt_gb_annual_intensity
            order by calendar_year
            """,
        )
        seasons = _q(
            con,
            """
            select season, mean_actual_gco2_kwh, p10_actual_gco2_kwh, p90_actual_gco2_kwh,
                   mean_daily_range_gco2_kwh, mean_wind_pct, mean_solar_pct, mean_gas_pct, days
            from reporting.rpt_gb_seasonal_summary
            order by season_order
            """,
        )
        season_extremes = _q(
            con,
            """
            select season,
                   arg_min(start_time_label, mean_actual_gco2_kwh) as lowest_half_hour,
                   min(mean_actual_gco2_kwh) as lowest_mean,
                   arg_max(start_time_label, mean_actual_gco2_kwh) as highest_half_hour,
                   max(mean_actual_gco2_kwh) as highest_mean
            from reporting.rpt_gb_seasonal_profile
            group by season
            order by case season when 'Winter' then 1 when 'Spring' then 2
                                 when 'Summer' then 3 else 4 end
            """,
        )
        regions = _q(
            con,
            """
            select region_short_name, nation, mean_forecast_gco2_kwh, winter_mean_gco2_kwh,
                   summer_mean_gco2_kwh, mean_wind_pct, mean_low_carbon_pct
            from reporting.rpt_gb_regional_intensity
            order by is_aggregate, mean_forecast_gco2_kwh
            """,
        )
        api = _q(
            con,
            """
            select scope, scope_value, periods, mae_gco2_kwh, rmse_gco2_kwh, mape_pct,
                   bias_gco2_kwh, within_10_pct, mean_actual_gco2_kwh
            from reporting.rpt_api_forecast_accuracy
            order by case scope when 'report_window' then 1 when 'season' then 2 else 3 end,
                     scope_value
            """,
        )
        snapshots = _q(
            con,
            "select lead_band, pairs, snapshots, mae_gco2_kwh, rmse_gco2_kwh, mape_pct "
            "from reporting.rpt_api_forecast_snapshot_accuracy order by min_lead_minutes",
        )

    parts: list[str] = [
        "# Report tables",
        "",
        f"Generated by `gridwatch report` at "
        f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC. Do not edit by hand.",
        "",
        f"GB report window (UK local dates): **{window['start_date']} to "
        f"{window['end_date']}** ({window['days']} days). Units: gCO2/kWh for GB (operational, "
        "Carbon Intensity API); gCO2e/kWh for countries (lifecycle, Ember).",
        "",
        "## Data coverage (national series)",
        "",
        markdown_table(
            coverage,
            [
                "First half-hour (UTC)",
                "Last actual (UTC)",
                "Half-hours",
                "Not served by API",
                "Implausible actuals removed",
                "Implausible forecasts removed",
            ],
        ),
        "",
        f"## (a) Flexible batch job ({job['h']} hours)",
        "",
        "Mean intensity seen by the job for each UK local start time (five best, three worst):",
        "",
        markdown_table(
            best_slots,
            ["Start", "All days", "Working days", "Weekends and holidays", "Rank"],
        ),
        "",
        "Best and worst start times across the week:",
        "",
        markdown_table(best_week, ["Day", "Start", "Mean gCO2/kWh", "Jobs"]),
        "",
        markdown_table(worst_week, ["Day", "Start", "Mean gCO2/kWh", "Jobs"]),
        "",
        f"One run per day under each scheduling rule; the kg column assumes a "
        f"{job['kw']} kW load for {job['h']} hours:",
        "",
        markdown_table(
            savings,
            [
                "Rule",
                "Days",
                "Mean gCO2/kWh",
                "Saving vs 00:00 (%)",
                "Saving vs 09:00 (%)",
                "Saving vs 17:00 (%)",
                "kg CO2 per run",
            ],
        ),
        "",
        "## (b) UAE and GCC against the UK and EU",
        "",
        markdown_table(
            countries,
            [
                "Area",
                "Year",
                "gCO2e/kWh",
                "x UK",
                "x EU",
                "2015",
                "Change since 2015 (%)",
                "Gas %",
                "Oil and other fossil %",
                "Nuclear %",
                "Wind and solar %",
            ],
            decimals=2,
        ),
        "",
        "Lifecycle intensity (gCO2e/kWh) by year:",
        "",
        markdown_table(trend, decimals=0),
        "",
        "## (c) Seasonal and regional variation in GB",
        "",
        "Annual national means:",
        "",
        markdown_table(
            annual_gb,
            ["Year", "Mean gCO2/kWh", "Low-carbon %", "Wind %", "Gas %", "Coal %", "Complete year"],
        ),
        "",
        markdown_table(
            seasons,
            [
                "Season",
                "Mean",
                "P10",
                "P90",
                "Mean daily range",
                "Wind %",
                "Solar %",
                "Gas %",
                "Days",
            ],
        ),
        "",
        markdown_table(
            season_extremes,
            ["Season", "Lowest half-hour", "Mean", "Highest half-hour", "Mean"],
        ),
        "",
        "Regional forecast intensity (the API publishes no regional actuals):",
        "",
        markdown_table(
            regions,
            ["Region", "Nation", "Mean", "Winter", "Summer", "Wind %", "Low-carbon %"],
        ),
        "",
        "## (d) Accuracy of the API's own forecast",
        "",
        markdown_table(
            api,
            [
                "Scope",
                "Value",
                "Half-hours",
                "MAE",
                "RMSE",
                "MAPE %",
                "Bias",
                "Within 10 g %",
                "Mean actual",
            ],
            decimals=2,
        ),
        "",
        "Stored day-ahead snapshots (fills up as the daily workflow runs):",
        "",
        markdown_table(snapshots, ["Lead", "Pairs", "Snapshots", "MAE", "RMSE", "MAPE %"])
        if snapshots.height
        else "_No snapshot has an actual value yet._",
        "",
    ]
    parts += _backtest_section(outputs_dir / BACKTEST_SUMMARY_FILE)
    return "\n".join(parts).rstrip() + "\n"


def _backtest_section(path: Path) -> list[str]:
    if not path.exists():
        return ["## Forecast backtest", "", "_Not run yet: `gridwatch backtest`._", ""]
    summary = json.loads(path.read_text(encoding="utf-8"))
    metrics = pl.DataFrame(summary["metrics"]).with_columns(pl.col("method").replace(METHOD_LABELS))
    config = summary["config"]
    lines = [
        "## Forecast backtest",
        "",
        f"{summary['origins']} daily forecasts issued at "
        f"{config['origin_hour_utc']:02d}:00 UTC from {summary['first_origin_utc']} to "
        f"{summary['last_origin_utc']}; model retrained every "
        f"{config['retrain_every_days']} days on the previous "
        f"{config['model']['train_days']} days ({summary['folds']} retrains).",
        "",
        markdown_table(
            metrics.select("horizon_band", "method", "n", "mae", "rmse", "mape", "bias"),
            ["Horizon", "Method", "Pairs", "MAE", "RMSE", "MAPE %", "Bias"],
            decimals=2,
        ),
        "",
        "Model 10-90% interval (conformal calibration; 80% is the target):",
        "",
        markdown_table(
            pl.DataFrame(summary["interval_coverage"]),
            ["Horizon", "Pairs", "Coverage %", "Mean width"],
        ),
        "",
        "Share of forecast days (24-48 h ahead) on which the model had the lower MAE:",
        "",
        markdown_table(
            pl.DataFrame(summary["model_win_rates_24_48h"]).with_columns(
                pl.col("compared_with").replace(METHOD_LABELS)
            ),
            ["Compared with", "Days", "Model better", "Model better %"],
        ),
        "",
        "MAE by target month, 24-48 h ahead:",
        "",
        markdown_table(
            pl.DataFrame(summary["monthly_mae_24_48h"]),
            ["Month", "Pairs", "Model", "Naive yesterday", "Naive last week", "API"],
        ),
        "",
    ]
    return lines


def write_report(warehouse: Path, outputs_dir: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report(warehouse, outputs_dir), encoding="utf-8", newline="\n")
    return target
