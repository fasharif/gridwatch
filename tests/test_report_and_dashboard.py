from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from gridwatch.dashboard.build import _json_for_script, kpi_tiles, render
from gridwatch.dashboard.data import DashboardData
from gridwatch.dashboard.figures import all_charts
from gridwatch.report import markdown_table


def test_markdown_table_formats_numbers() -> None:
    frame = pl.DataFrame({"name": ["a", "b"], "value": [1234.567, None], "n": [3, 4]})
    table = markdown_table(frame, ["Name", "Value", "N"], decimals=1)
    lines = table.splitlines()
    assert lines[0] == "| Name | Value | N |"
    assert lines[1] == "| --- | ---: | ---: |"
    assert lines[2] == "| a | 1,234.6 | 3 |"
    assert lines[3] == "| b | n/a | 4 |"


def test_markdown_table_needs_one_header_per_column() -> None:
    with pytest.raises(ValueError, match="one header"):
        markdown_table(pl.DataFrame({"a": [1]}), ["A", "B"])


def test_json_for_script_cannot_close_the_script_tag() -> None:
    text = _json_for_script({"label": "</script><b>x</b>", "value": float("nan")})
    assert "</script>" not in text
    assert "<b>" not in text
    assert json.loads(text) == {"label": "</script><b>x</b>", "value": None}


def sample_data() -> DashboardData:
    slots = [
        {
            "time_key": k,
            "start_time_label": f"{k // 2:02d}:{(k % 2) * 30:02d}",
            "mean_job_gco2_kwh": 100.0 + k,
            "working_day_mean_job_gco2_kwh": 110.0 + k,
            "non_working_day_mean_job_gco2_kwh": 90.0 + k,
            "rank_lowest": k + 1,
            "jobs": 30,
        }
        for k in range(48)
    ]
    return DashboardData(
        built_at_utc="2026-09-20 00:00",
        data_through_utc="2026-09-19 23:30",
        report_window={
            "start_date": "2026-07-23",
            "end_date": "2026-08-31",
            "days": 40,
            "batch_job_hours": 4,
        },
        kpis={
            "last_7d_mean": 120.4,
            "window_mean": 118.0,
            "best_start": "10:30",
            "best_start_mean": 90.0,
            "worst_start": "17:00",
            "worst_start_mean": 150.0,
            "profile_saving_vs_0900_pct": 12.3,
            "profile_saving_vs_1700_pct": 30.1,
            "api_mae": 9.5,
            "api_mape": 8.1,
        },
        recent=[
            {
                "period_start_utc": "2026-09-19T23:00",
                "actual_gco2_kwh": 100,
                "forecast_gco2_kwh": 101,
            }
        ],
        api_snapshot=[
            {
                "period_start_utc": "2026-09-20T00:00",
                "forecast_gco2_kwh": 99,
                "issued_at_utc": "2026-09-20T00:00",
            }
        ],
        model_forecast=[
            {
                "issued_at_utc": "2026-09-20T00:00",
                "period_start_utc": "2026-09-20T00:00",
                "forecast_gco2_kwh": 98.0,
                "p10_gco2_kwh": 80.0,
                "p90_gco2_kwh": 120.0,
                "horizon": 1,
            }
        ],
        weekly_profile=[
            {
                "iso_day_of_week": d,
                "day_name": "x",
                "time_key": 0,
                "start_time_label": "00:00",
                "mean_actual_gco2_kwh": 100.0 + d,
            }
            for d in range(1, 8)
        ],
        start_slots=slots,
        strategies=[
            {
                "strategy": "oracle",
                "strategy_label": "Perfect foresight <b>",
                "days": 10,
                "mean_job_gco2_kwh": 70.0,
                "saving_vs_0900_pct": 30.0,
                "saving_vs_1700_pct": 40.0,
                "kg_co2_per_run": 28.0,
            }
        ],
        seasonal_profile=[
            {
                "season": "Winter",
                "time_key": 0,
                "start_time_label": "00:00",
                "mean_actual_gco2_kwh": 130.0,
            }
        ],
        monthly=[
            {
                "month_start": "2026-08-01",
                "year_month": "2026-08",
                "mean_actual_gco2_kwh": 120.0,
                "p10_actual_gco2_kwh": 60.0,
                "p90_actual_gco2_kwh": 190.0,
                "mean_low_carbon_pct": 60.0,
                "coverage_pct": 100.0,
            }
        ],
        regions=[
            {
                "region_short_name": "London",
                "nation": "England",
                "mean_forecast_gco2_kwh": 140.0,
                "winter_mean_gco2_kwh": 150.0,
                "summer_mean_gco2_kwh": 130.0,
                "mean_wind_pct": 25.0,
                "mean_low_carbon_pct": 40.0,
            }
        ],
        country_trend=[
            {
                "country_code": "ARE",
                "country_name": "United Arab Emirates",
                "peer_group": "GCC",
                "is_aggregate": False,
                "year": 2024,
                "intensity_gco2e_kwh": 467.5,
            }
        ],
        country_comparison=[],
        backtest_by_horizon=[
            {
                "horizon": 1,
                "hours_ahead": 0.5,
                "model": 20.0,
                "naive_yesterday": 30.0,
                "naive_last_week": 40.0,
                "api_forecast": 9.0,
            }
        ],
        backtest_metrics=[
            {
                "horizon_band": "24-48 h",
                "method": "model",
                "n": 10,
                "mae": 42.5,
                "rmse": 50.0,
                "mape": 40.0,
                "bias": 1.0,
            },
            {
                "horizon_band": "24-48 h",
                "method": "naive_yesterday",
                "n": 10,
                "mae": 47.9,
                "rmse": 60.0,
                "mape": 45.0,
                "bias": 0.0,
            },
        ],
        backtest_meta={"origins": 1, "first_origin_utc": "a", "last_origin_utc": "b"},
    )


def test_every_chart_has_a_spec_and_table() -> None:
    charts = all_charts(sample_data(), 4)
    assert [c.chart_id for c in charts] == [
        "next48",
        "slots",
        "week",
        "strategies",
        "seasons",
        "monthly",
        "regions",
        "countries",
        "backtest",
    ]
    for chart in charts:
        assert chart.spec is not None
        assert chart.rows, chart.chart_id
        assert all(len(row) == len(chart.columns) for row in chart.rows), chart.chart_id
        for trace in chart.spec["data"]:
            assert "role" in trace["meta"]


def test_kpi_tiles_include_the_model_when_backtested() -> None:
    tiles = kpi_tiles(sample_data())
    assert tiles[-1]["value"] == "42.5"
    # for the 24-48 h band this baseline is two days before the target, not "yesterday"
    assert tiles[-1]["detail"] == "Same half-hour, last known day: 47.9"
    assert tiles[1]["value"] == "10:30"


def test_render_writes_a_self_contained_site(tmp_path: Path) -> None:
    csv = tmp_path / "annual_grid_intensity.csv"
    csv.write_text("country_code,year\nARE,2024\n", encoding="utf-8")
    index = render(sample_data(), tmp_path / "site", csv)
    html = index.read_text(encoding="utf-8")
    assert "Perfect foresight &lt;b&gt;" not in html  # labels live in the JSON, not the markup
    assert "\\u003cb\\u003e" in html
    assert 'src="assets/plotly.min.js"' in html
    assert "https://" not in html.split("<footer")[0].replace(
        "https://ember-energy.org", ""
    ).replace("https://carbonintensity.org.uk", "")
    assert (tmp_path / "site" / "assets" / "plotly.min.js").stat().st_size > 1_000_000
    assert (tmp_path / "site" / "data" / "annual_grid_intensity.csv").exists()
