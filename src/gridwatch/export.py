"""CSV exports: the reusable annual intensity file and the Power BI star schema."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ANNUAL_INTENSITY_SQL = """
select
    country_code, country_name, peer_group, year, intensity_gco2e_per_kwh,
    generation_twh, emissions_mtco2e, emissions_basis, source, source_url, licence,
    is_latest_year
from reporting.rpt_annual_grid_intensity_export
order by peer_group, country_code, year
"""

# Star schema for Power BI. Dimensions are exported in full. The half-hourly fact covers
# the last ``half_hourly_days`` days to keep the files small; daily facts cover all history.
POWERBI_TABLES: Mapping[str, str] = {
    "dim_date": "select * from marts.dim_date order by date_key",
    "dim_time_of_day": "select * from marts.dim_time_of_day order by time_key",
    "dim_region": "select * from marts.dim_region order by region_id",
    "dim_fuel": "select * from marts.dim_fuel order by sort_order",
    "dim_country": "select * from marts.dim_country order by sort_order",
    "fct_national_intensity_half_hourly": """
        select
            strftime(period_start_utc, '%Y-%m-%d %H:%M') as period_start_utc,
            strftime(period_start_local, '%Y-%m-%d %H:%M') as period_start_local,
            date_key, time_key, forecast_gco2_kwh, actual_gco2_kwh,
            forecast_error_gco2_kwh,
            round(gas_pct, 1) as gas_pct, round(wind_pct, 1) as wind_pct,
            round(solar_pct, 1) as solar_pct, round(nuclear_pct, 1) as nuclear_pct,
            round(low_carbon_pct, 1) as low_carbon_pct
        from marts.fct_national_intensity
        where period_start_utc >= (
            select max(period_start_utc) from marts.fct_national_intensity
        ) - to_days({half_hourly_days})
        order by period_start_utc
    """,
    "fct_national_intensity_daily": """
        select
            date_key,
            round(avg(actual_gco2_kwh), 1) as mean_actual_gco2_kwh,
            min(actual_gco2_kwh) as min_actual_gco2_kwh,
            max(actual_gco2_kwh) as max_actual_gco2_kwh,
            round(avg(forecast_gco2_kwh), 1) as mean_forecast_gco2_kwh,
            round(avg(abs(forecast_error_gco2_kwh)), 2) as api_forecast_mae_gco2_kwh,
            round(avg(low_carbon_pct), 1) as mean_low_carbon_pct,
            count(actual_gco2_kwh) as periods_with_actual
        from marts.fct_national_intensity
        group by date_key
        order by date_key
    """,
    "fct_generation_mix_daily": """
        select date_key, fuel, round(avg(share_pct), 2) as mean_share_pct, count(*) as periods
        from marts.fct_generation_mix
        group by date_key, fuel
        order by date_key, fuel
    """,
    "fct_regional_intensity_daily": """
        select
            date_key, region_id,
            round(avg(forecast_gco2_kwh), 1) as mean_forecast_gco2_kwh,
            min(forecast_gco2_kwh) as min_forecast_gco2_kwh,
            max(forecast_gco2_kwh) as max_forecast_gco2_kwh,
            count(*) as periods
        from marts.fct_regional_intensity
        group by date_key, region_id
        order by date_key, region_id
    """,
    "fct_country_intensity_annual": """
        select * from marts.fct_country_intensity_annual order by country_code, year
    """,
}


@dataclass(frozen=True)
class ExportResult:
    files: dict[str, int]  # path -> rows


def _copy(con: duckdb.DuckDBPyConnection, sql: str, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    relation = con.sql(sql)
    rows = int(relation.aggregate("count(*)").fetchone()[0])  # type: ignore[index]
    relation.write_csv(str(path), header=True)
    return rows


def export_annual_intensity(warehouse: Path, target: Path) -> int:
    with duckdb.connect(str(warehouse), read_only=True) as con:
        return _copy(con, ANNUAL_INTENSITY_SQL, target)


def export_powerbi(warehouse: Path, target_dir: Path, half_hourly_days: int = 365) -> ExportResult:
    if half_hourly_days < 1:
        raise ValueError("half_hourly_days must be positive")
    files: dict[str, int] = {}
    with duckdb.connect(str(warehouse), read_only=True) as con:
        for name, sql in POWERBI_TABLES.items():
            path = target_dir / f"{name}.csv"
            files[str(path)] = _copy(con, sql.format(half_hourly_days=half_hourly_days), path)
        data_through = con.sql(
            "select max(period_start_utc) from marts.fct_national_intensity "
            "where actual_gco2_kwh is not null"
        ).fetchone()
    manifest = {
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "national_actuals_through_utc": str(data_through[0]) if data_through else None,
        "half_hourly_days": half_hourly_days,
        "tables": {Path(p).name: rows for p, rows in files.items()},
        "sources": [
            "Carbon Intensity API, National Energy System Operator (NESO), CC BY 4.0",
            "Ember Yearly Electricity Data, CC BY 4.0",
        ],
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return ExportResult(files)
