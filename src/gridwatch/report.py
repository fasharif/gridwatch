"""Render the numbers behind docs/findings.md as Markdown tables.

Everything here is read from the reporting marts, the raw Ember manifest and the backtest
outputs, so the committed snapshot (docs/generated/report.md) can be regenerated with one
command. Every number quoted in docs/findings.md and in the README's results must appear in
this report, at the precision quoted; tests/test_docs_numbers.py checks that. Derived figures
the prose needs (differences, ratios, counts) are therefore computed here, in the "Headline
figures" table, rather than by hand.
"""

from __future__ import annotations

import contextlib
import json
import math
import platform
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from gridwatch.forecast.backtest import HORIZON_BANDS, METHODS
from gridwatch.forecast.service import (
    BACKTEST_PREDICTIONS_FILE,
    BACKTEST_SUMMARY_FILE,
    validation_summaries,
)
from gridwatch.ingest.ember import previous_download
from gridwatch.stats import diebold_mariano, saving_interval

METHOD_LABELS = {
    "model": "gridwatch model (gradient boosting)",
    "persistence": "Persistence: last known value",
    "naive_yesterday": "Naive: same half-hour, last known day",
    "naive_last_week": "Naive: same half-hour, one week earlier",
    "api_forecast": "API retained forecast (short lead)",
}
NAIVE_BASELINES = ("persistence", "naive_yesterday", "naive_last_week")
DM_LAGS = 7
BOOTSTRAP_BLOCK_DAYS = 7
BOOTSTRAP_RESAMPLES = 2000
SOFTWARE = ("dbt-core", "dbt-duckdb", "duckdb", "scikit-learn", "polars", "numpy")


def _fmt(value: Any, decimals: int) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if math.isnan(value):
            return "n/a"
        # "z" prints a value that rounds to zero as 0.0, never -0.0.
        return f"{value:z,.{decimals}f}"
    return str(value)


def markdown_table(
    frame: pl.DataFrame,
    headers: Sequence[str] | None = None,
    decimals: int = 1,
    column_decimals: Mapping[str, int] | None = None,
) -> str:
    """A GitHub-flavoured Markdown table; numbers right-aligned.

    Floats get ``decimals`` places unless ``column_decimals`` names their column.
    """
    names = list(headers) if headers is not None else frame.columns
    if len(names) != frame.width:
        raise ValueError("one header per column is required")
    places = [(column_decimals or {}).get(column, decimals) for column in frame.columns]
    numeric = [dtype.is_numeric() for dtype in frame.dtypes]
    lines = [
        "| " + " | ".join(names) + " |",
        "| " + " | ".join("---:" if n else "---" for n in numeric) + " |",
    ]
    for row in frame.iter_rows():
        cells = (_fmt(value, digits) for value, digits in zip(row, places, strict=True))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _q(con: duckdb.DuckDBPyConnection, sql: str) -> pl.DataFrame:
    return con.sql(sql).pl()


def _p_value(p: float) -> str:
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _versions() -> str:
    parts = []
    for name in SOFTWARE:
        try:
            parts.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            parts.append(f"{name} not installed")
    return (
        f"Python {platform.python_version()} on {platform.system()} {platform.release()}; "
        + ", ".join(parts)
        + "."
    )


def _ember_line(raw_dir: Path | None) -> str:
    manifest = previous_download(raw_dir) if raw_dir is not None else None
    if manifest is None:
        return "Ember file: not recorded (no manifest in the raw data directory)."
    modified = manifest.last_modified or "unknown"
    with contextlib.suppress(TypeError, ValueError):
        modified = parsedate_to_datetime(modified).strftime("%Y-%m-%d")
    return (
        f"Ember file: last modified {modified}, {manifest.rows:,} rows, SHA-256 "
        f"`{manifest.sha256}`, fetched {manifest.fetched_at_utc}."
    )


def build_report(warehouse: Path, outputs_dir: Path, raw_dir: Path | None = None) -> str:
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
        spread = _q(
            con,
            """
            select
                quantile_cont(national.actual_gco2_kwh, 0.1) as p10,
                quantile_cont(national.actual_gco2_kwh, 0.9) as p90
            from marts.fct_national_intensity as national
            inner join marts.dim_date as dates on national.date_key = dates.date_key
            cross join reporting.rpt_report_window as report_window
            where dates.calendar_date between report_window.start_date and report_window.end_date
              and national.actual_gco2_kwh is not null
            """,
        ).row(0, named=True)
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
            select strategy, strategy_label, days, mean_job_gco2_kwh, saving_vs_0000_pct,
                   saving_vs_0900_pct, saving_vs_1700_pct, kg_co2_per_run,
                   saving_vs_0900_gco2_kwh
            from reporting.rpt_batch_job_savings
            order by sort_order
            """,
        )
        job = _q(
            con,
            "select any_value(illustrative_job_kw) kw, any_value(batch_job_hours) h "
            "from reporting.rpt_batch_job_savings",
        ).row(0, named=True)
        # The same days as rpt_batch_job_savings, in date order, for the bootstrap.
        daily = _q(
            con,
            """
            select job_start_date, fixed_0000, fixed_0900, fixed_1700, any_start,
                   profile_guided, forecast_guided, oracle
            from reporting.rpt_batch_job_daily_strategies
            where profile_guided is not null and forecast_guided is not null
            order by job_start_date
            """,
        )
        profile_choice = _q(
            con,
            """
            with chosen as (
                select
                    strftime(daily.job_start_date, '%Y-%m') as year_month,
                    slots.start_time_label,
                    count(*) as days
                from reporting.rpt_batch_job_daily_strategies as daily
                inner join marts.dim_time_of_day as slots
                    on daily.profile_time_key = slots.time_key
                where daily.profile_guided is not null and daily.forecast_guided is not null
                group by all
            ),

            ranked as (
                select
                    *,
                    sum(days) over (partition by year_month) as month_days,
                    row_number() over (
                        partition by year_month order by days desc, start_time_label
                    ) as rank_in_month
                from chosen
            )

            select year_month, start_time_label, days, month_days
            from ranked
            where rank_in_month = 1
            order by year_month
            """,
        )
        # Days of the window missing from the rule comparison, and the upstream values
        # removed from the job windows that start on those days (up to 04:00 the next day).
        excluded = _q(
            con,
            """
            with window_days as (
                select cast(unnest(generate_series(start_date, end_date, interval 1 day))
                            as date) as day
                from reporting.rpt_report_window
            ),

            excluded as (
                select day
                from window_days
                where day not in (
                    select job_start_date
                    from reporting.rpt_batch_job_daily_strategies
                    where profile_guided is not null and forecast_guided is not null
                )
            )

            select
                strftime(excluded.day, '%Y-%m-%d') as day,
                strftime(national.period_start_utc, '%Y-%m-%d %H:%M') as half_hour_utc,
                case
                    when national.is_missing_from_source then 'not served by the API'
                    when national.is_actual_implausible then 'implausible actual removed'
                    when national.is_forecast_implausible then 'implausible forecast removed'
                    when national.is_actual_missing then 'no actual value'
                end as reason
            from excluded
            left join marts.fct_national_intensity as national
                on national.period_start_local >= excluded.day
                and national.period_start_local < excluded.day + interval 28 hour
                and (national.is_missing_from_source or national.is_actual_missing
                     or national.is_forecast_implausible)
            order by 1, 2
            """,
        )
        profile_overall = _q(
            con,
            """
            select slots.start_time_label, count(*) as days
            from reporting.rpt_batch_job_daily_strategies as daily
            inner join marts.dim_time_of_day as slots on daily.profile_time_key = slots.time_key
            where daily.profile_guided is not null and daily.forecast_guided is not null
            group by all
            order by days desc, slots.start_time_label
            limit 1
            """,
        ).row(0, named=True)
        countries = _q(
            con,
            """
            select country_name, comparison_year, intensity_gco2e_kwh, ratio_to_uk,
                   ratio_to_eu, intensity_2015_gco2e_kwh, change_since_2015_pct,
                   fossil_share_pct, gas_share_pct, coal_share_pct, other_fossil_share_pct,
                   nuclear_share_pct, solar_share_pct + wind_share_pct as wind_solar_share_pct,
                   hydro_share_pct, bioenergy_share_pct
            from reporting.rpt_country_intensity_comparison
            order by intensity_gco2e_kwh
            """,
        )
        trend = _q(
            con,
            """
            pivot (
                select country_name, year, intensity_gco2e_kwh as intensity
                from reporting.rpt_country_intensity_trend
                where year in (2000, 2010, 2015, 2019, 2020, 2021, 2022, 2023, 2024, 2025)
            )
            on year using any_value(intensity)
            order by country_name
            """,
        )
        uae = _q(
            con,
            """
            select year, generation_twh, intensity_gco2e_kwh, gas_share_pct,
                   nuclear_share_pct, solar_share_pct
            from marts.fct_country_intensity_annual
            where country_code = 'ARE' and year >= 2015
            order by year
            """,
        )
        annual_gb = _q(
            con,
            """
            select calendar_year, mean_actual_gco2_kwh, mean_low_carbon_pct, mean_wind_pct,
                   mean_gas_pct, mean_coal_pct,
                   100.0 * periods_with_actual
                       / (48 * (case when calendar_year % 4 = 0 then 366 else 365 end))
                       as actual_coverage_pct
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
                   first(start_time_label order by mean_actual_gco2_kwh, start_time_label)
                       as lowest_half_hour,
                   min(mean_actual_gco2_kwh) as lowest_mean,
                   first(start_time_label order by mean_actual_gco2_kwh desc, start_time_label)
                       as highest_half_hour,
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

    by_strategy = {row["strategy"]: row for row in savings.iter_rows(named=True)}
    if best_slots.height == 0 or not {"profile_guided", "oracle"} <= by_strategy.keys():
        raise ValueError(
            "the report window has too few complete days for the batch-job analysis; "
            "widen it with --report-start and --report-end"
        )
    best = best_slots.row(0, named=True)
    worst = best_slots.row(best_slots.height - 1, named=True)
    profile = by_strategy["profile_guided"]
    oracle = by_strategy["oracle"]
    kg_saved = profile["saving_vs_0900_gco2_kwh"] * job["kw"] * job["h"] / 1000.0
    backtest = _load_summary(outputs_dir / BACKTEST_SUMMARY_FILE)

    headline: list[tuple[str, str]] = [
        ("Half-hourly GB intensity in the report window, 10th percentile", f"{spread['p10']:.0f}"),
        ("Half-hourly GB intensity in the report window, 90th percentile", f"{spread['p90']:.0f}"),
        (
            f"Best start for the {job['h']}-hour job (UK time) and its mean gCO2/kWh",
            f"{best['start_time_label']} ({best['mean_job_gco2_kwh']:.1f})",
        ),
        (
            "Worst start and its mean gCO2/kWh",
            f"{worst['start_time_label']} ({worst['mean_job_gco2_kwh']:.1f})",
        ),
        (
            "Extra carbon per kWh at the worst start against the best (%)",
            f"{100.0 * (worst['mean_job_gco2_kwh'] / best['mean_job_gco2_kwh'] - 1.0):.0f}",
        ),
        (
            "Historical-profile rule: saving against a fixed 09:00 start (gCO2/kWh)",
            f"{profile['saving_vs_0900_gco2_kwh']:.1f}",
        ),
        (
            "Perfect foresight: saving against a fixed 09:00 start (gCO2/kWh)",
            f"{oracle['saving_vs_0900_gco2_kwh']:.1f}",
        ),
        (
            "Share of the perfect-foresight saving the profile rule captured (%)",
            f"{100.0 * profile['saving_vs_0900_gco2_kwh'] / oracle['saving_vs_0900_gco2_kwh']:.0f}",
        ),
        (
            f"Profile rule: kg CO2 saved per run against 09:00 ({job['kw']} kW, {job['h']} h)",
            f"{kg_saved:.1f}",
        ),
        (
            f"Profile rule: tonnes CO2 saved against 09:00 over the {profile['days']} days",
            f"{kg_saved * profile['days'] / 1000.0:.1f}",
        ),
        (
            "Start the profile rule chose most often, and on how many days",
            f"{profile_overall['start_time_label']} on {profile_overall['days']} days",
        ),
    ]
    headline += _forecast_headline(backtest)

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
        "## Data and software",
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
        _ember_line(raw_dir),
        "",
        f"Software: {_versions()}",
        "",
        "## Headline figures",
        "",
        "Derived figures quoted in docs/findings.md and the README.",
        "",
        markdown_table(
            pl.DataFrame(headline, schema=["figure", "value"], orient="row"), ["Figure", "Value"]
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
        *_excluded_days_section(excluded),
        f"One run per day under each scheduling rule; the kg column assumes a "
        f"{job['kw']} kW load for {job['h']} hours:",
        "",
        markdown_table(
            savings.drop("strategy", "saving_vs_0900_gco2_kwh"),
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
        f"Savings with 95% intervals from a moving-block bootstrap: the {daily.height} days are "
        f"resampled in blocks of {BOOTSTRAP_BLOCK_DAYS} consecutive days, "
        f"{BOOTSTRAP_RESAMPLES:,} times, keeping every rule on the same days:",
        "",
        markdown_table(
            _saving_intervals(daily, by_strategy),
            [
                "Rule",
                "Saving vs 09:00 (%)",
                "95% interval",
                "Saving vs 17:00 (%)",
                "95% interval",
            ],
        ),
        "",
        "Start chosen most often by the historical-profile rule, by month:",
        "",
        markdown_table(
            profile_choice, ["Month", "Start (UK time)", "Days chosen", "Days in month"]
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
                "All fossil %",
                "Gas %",
                "Coal %",
                "Oil and other fossil %",
                "Nuclear %",
                "Wind and solar %",
                "Hydro %",
                "Bioenergy %",
            ],
            decimals=0,
            column_decimals={
                "intensity_gco2e_kwh": 1,
                "ratio_to_uk": 2,
                "ratio_to_eu": 2,
                "intensity_2015_gco2e_kwh": 1,
                "change_since_2015_pct": 1,
            },
        ),
        "",
        "Lifecycle intensity (gCO2e/kWh) by year:",
        "",
        markdown_table(trend.rename({"country_name": "Area"}), decimals=1),
        "",
        "United Arab Emirates by year:",
        "",
        markdown_table(
            uae,
            ["Year", "Generation TWh", "gCO2e/kWh", "Gas %", "Nuclear %", "Solar %"],
            decimals=0,
            column_decimals={"intensity_gco2e_kwh": 1},
        ),
        "",
        "## (c) Seasonal and regional variation in GB",
        "",
        "Annual national means:",
        "",
        markdown_table(
            annual_gb,
            [
                "Year",
                "Mean gCO2/kWh",
                "Low-carbon %",
                "Wind %",
                "Gas %",
                "Coal %",
                "Half-hours with an actual (%)",
            ],
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
            column_decimals={
                "p10_actual_gco2_kwh": 0,
                "p90_actual_gco2_kwh": 0,
                "mean_wind_pct": 0,
                "mean_solar_pct": 0,
                "mean_gas_pct": 0,
            },
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
            column_decimals={"mean_wind_pct": 0, "mean_low_carbon_pct": 0},
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
        ),
        "",
        "Stored day-ahead snapshots (fills up as the daily workflow runs):",
        "",
        markdown_table(snapshots, ["Lead", "Pairs", "Snapshots", "MAE", "RMSE", "MAPE %"])
        if snapshots.height
        else "_No snapshot has an actual value yet._",
        "",
    ]
    parts += _backtest_section(backtest, outputs_dir / BACKTEST_PREDICTIONS_FILE)
    parts += _validation_section(outputs_dir)
    return "\n".join(parts).rstrip() + "\n"


def _excluded_days_section(excluded: pl.DataFrame) -> list[str]:
    if excluded.height == 0:
        return ["Every day of the window is in the rule comparison.", ""]
    return [
        f"Days of the window left out of the rule comparison ({excluded['day'].n_unique()}): a "
        "day counts only when every rule, the forecast rule included, can be scored, and a "
        "value was removed upstream in one of these days' job windows:",
        "",
        markdown_table(excluded, ["Day (UK)", "Half-hour removed (UTC)", "Reason"]),
        "",
    ]


def _saving_intervals(daily: pl.DataFrame, by_strategy: Mapping[str, Any]) -> pl.DataFrame:
    rows = []
    for strategy in ("fixed_0000", "any_start", "profile_guided", "forecast_guided", "oracle"):
        cells: list[object] = [by_strategy[strategy]["strategy_label"]]
        for baseline in ("fixed_0900", "fixed_1700"):
            interval = saving_interval(
                daily[strategy].to_numpy(),
                daily[baseline].to_numpy(),
                block_length=BOOTSTRAP_BLOCK_DAYS,
                resamples=BOOTSTRAP_RESAMPLES,
            )
            cells += [interval.estimate, f"{interval.lower:.1f} to {interval.upper:.1f}"]
        rows.append(cells)
    return pl.DataFrame(
        rows,
        schema=[
            ("rule", pl.String),
            ("vs_0900", pl.Float64),
            ("ci_0900", pl.String),
            ("vs_1700", pl.Float64),
            ("ci_1700", pl.String),
        ],
        orient="row",
    )


def _load_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    summary: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return summary


def _forecast_headline(summary: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    if summary is None:
        return []
    mae = {m["method"]: m["mae"] for m in summary["metrics"] if m["horizon_band"] == "24-48 h"}
    wins = {w["compared_with"]: w for w in summary["model_win_rates_24_48h"]}
    rows = [
        (
            f"Model 24-48 h MAE below the {_short(baseline)} baseline (%)",
            f"{100.0 * (1.0 - mae['model'] / mae[baseline]):.0f}",
        )
        for baseline in NAIVE_BASELINES
        if baseline in mae
    ]
    rows += [
        (
            "Days the model lost to the last-known-day baseline, 24-48 h (%)",
            f"{100.0 - wins['naive_yesterday']['model_better_pct']:.1f}",
        )
    ]
    monthly = [
        m["coverage_pct"]
        for m in summary.get("monthly_mae_24_48h", [])
        if m.get("coverage_pct") is not None
    ]
    if monthly:
        rows.append(
            (
                "Lowest and highest monthly 10-90% coverage, 24-48 h (%)",
                f"{min(monthly):.1f} to {max(monthly):.1f}",
            )
        )
    return rows


def _short(method: str) -> str:
    return {
        "persistence": "persistence",
        "naive_yesterday": "last-known-day",
        "naive_last_week": "last-week",
    }[method]


def _diebold_mariano_table(predictions_path: Path) -> pl.DataFrame | None:
    if not predictions_path.exists():
        return None
    predictions = pl.read_parquet(predictions_path)
    methods = [m for m in METHODS if m in predictions.columns]
    complete = predictions.drop_nulls(subset=["actual", *methods]).filter(
        pl.all_horizontal([pl.col(c).is_not_nan() for c in ["actual", *methods]])
    )
    rows = []
    for band, first, last in HORIZON_BANDS[:2]:
        daily = (
            complete.filter(pl.col("horizon").is_between(first, last))
            .group_by("origin_utc")
            .agg([(pl.col(m) - pl.col("actual")).abs().mean().alias(m) for m in methods])
            .sort("origin_utc")
        )
        for baseline in (b for b in NAIVE_BASELINES if b in methods):
            try:
                test = diebold_mariano(
                    daily["model"].to_numpy(), daily[baseline].to_numpy(), lags=DM_LAGS
                )
            except ValueError:
                continue  # too few forecast days, e.g. on the short CI fixture
            rows.append(
                [
                    band,
                    METHOD_LABELS[baseline],
                    test.n,
                    test.mean_difference,
                    test.statistic,
                    _p_value(test.p_value),
                ]
            )
    return pl.DataFrame(
        rows,
        schema=[
            ("horizon", pl.String),
            ("compared_with", pl.String),
            ("days", pl.Int64),
            ("mean_difference", pl.Float64),
            ("statistic", pl.Float64),
            ("p_value", pl.String),
        ],
        orient="row",
    )


def _backtest_section(summary: Mapping[str, Any] | None, predictions_path: Path) -> list[str]:
    if summary is None:
        return ["## Forecast backtest", "", "_Not run yet: `gridwatch backtest`._", ""]
    metrics = pl.DataFrame(summary["metrics"]).with_columns(pl.col("method").replace(METHOD_LABELS))
    monthly = pl.DataFrame(summary["monthly_mae_24_48h"])
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
        ),
        "",
        "Model 10-90% interval (conformal calibration; 80% is the target):",
        "",
        markdown_table(
            pl.DataFrame(summary["interval_coverage"]),
            ["Horizon", "Pairs", "Coverage %", "Mean width"],
        ),
        "",
        *_lead_time_section(summary),
        "Share of forecast days (24-48 h ahead) on which the model had the lower MAE:",
        "",
        markdown_table(
            pl.DataFrame(summary["model_win_rates_24_48h"]).with_columns(
                pl.col("compared_with").replace(METHOD_LABELS)
            ),
            ["Compared with", "Days", "Model better", "Model better %"],
        ),
        "",
        "MAE by target month, 24-48 h ahead, and the model's 10-90% coverage in that month:",
        "",
        markdown_table(
            monthly,
            ["Month", "Pairs", *[_SHORT_LABELS[c] for c in monthly.columns[2:]]],
        ),
        "",
    ]
    tests = _diebold_mariano_table(predictions_path)
    if tests is not None and tests.height:
        lines += [
            "Diebold-Mariano tests of equal accuracy, model against each naive baseline. The "
            "loss is each forecast day's MAE; a negative difference or statistic means the "
            f"model was more accurate. Newey-West variance with {DM_LAGS} lags, two-sided "
            "normal p-values:",
            "",
            markdown_table(
                tests,
                [
                    "Horizon",
                    "Compared with",
                    "Days",
                    "Mean daily MAE difference",
                    "DM statistic",
                    "p-value",
                ],
                column_decimals={"statistic": 2},
            ),
            "",
        ]
    return lines


_SHORT_LABELS = {
    "mae_model": "Model",
    "mae_persistence": "Persistence",
    "mae_naive_yesterday": "Naive last known day",
    "mae_naive_last_week": "Naive last week",
    "mae_api_forecast": "API",
    "coverage_pct": "Model 10-90% coverage %",
}


def _lead_time_section(summary: Mapping[str, Any]) -> list[str]:
    rows = summary.get("mae_by_lead")
    if not rows:
        return []
    frame = pl.DataFrame(rows)
    return [
        "MAE by lead time, with the model's 10-90% coverage (all methods on the same half-hours):",
        "",
        markdown_table(
            frame,
            ["Lead time", "Pairs", *[_SHORT_LABELS[c] for c in frame.columns[2:]]],
        ),
        "",
    ]


def _validation_section(outputs_dir: Path) -> list[str]:
    runs = validation_summaries(outputs_dir)
    lines = [
        "## Validation runs",
        "",
        "Backtests on data before the test year, used to choose the settings "
        "(`gridwatch backtest --until ...`):",
        "",
    ]
    if not runs:
        return [*lines, "_No validation run stored._", ""]
    rows = []
    for run in runs:
        mae = {
            (m["horizon_band"], m["method"]): m["mae"]
            for m in run["metrics"]
            if m["method"] == "model"
        }
        coverage = {c["horizon_band"]: c["coverage_pct"] for c in run["interval_coverage"]}
        # Runs made before the lead-time breakdown existed have no first-hour figures.
        first_hour: dict[str, Any] = next(
            (b for b in run.get("mae_by_lead", []) if b["lead_band"] == "0-1 h"), {}
        )
        rows.append(
            [
                str(run.get("until_utc") or "")[:10],
                f"{run['first_origin_utc'][:10]} to {run['last_origin_utc'][:10]}",
                run["origins"],
                run["config"]["model"]["train_days"],
                run["config"]["model"]["calibration_days"],
                first_hour.get("mae_model"),
                first_hour.get("mae_persistence"),
                mae.get(("0-24 h", "model")),
                mae.get(("24-48 h", "model")),
                coverage.get("0-48 h"),
            ]
        )
    frame = pl.DataFrame(
        rows,
        schema=[
            ("until", pl.String),
            ("origins_utc", pl.String),
            ("origins", pl.Int64),
            ("train_days", pl.Int64),
            ("calibration_days", pl.Int64),
            ("mae_0_1", pl.Float64),
            ("persistence_0_1", pl.Float64),
            ("mae_0_24", pl.Float64),
            ("mae_24_48", pl.Float64),
            ("coverage", pl.Float64),
        ],
        orient="row",
    )
    return [
        *lines,
        markdown_table(
            frame,
            [
                "Data before",
                "Forecasts issued",
                "Forecasts",
                "Training days",
                "Calibration days",
                "Model MAE 0-1 h",
                "Persistence MAE 0-1 h",
                "Model MAE 0-24 h",
                "Model MAE 24-48 h",
                "10-90% coverage, 0-48 h (%)",
            ],
        ),
        "",
    ]


def write_report(
    warehouse: Path, outputs_dir: Path, target: Path, raw_dir: Path | None = None
) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report(warehouse, outputs_dir, raw_dir), encoding="utf-8", newline="\n")
    return target
