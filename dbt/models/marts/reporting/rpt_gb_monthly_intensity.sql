-- Question (c): monthly distribution of GB national intensity over the full history, with
-- the generation shares that explain it. coverage_pct shows how complete each month is.
select
    dates.year_month,
    min(dates.calendar_date) as month_start,
    dates.calendar_year,
    dates.month_of_year,
    dates.season,
    avg(national.actual_gco2_kwh) as mean_actual_gco2_kwh,
    quantile_cont(national.actual_gco2_kwh, 0.1) as p10_actual_gco2_kwh,
    quantile_cont(national.actual_gco2_kwh, 0.9) as p90_actual_gco2_kwh,
    min(national.actual_gco2_kwh) as min_actual_gco2_kwh,
    max(national.actual_gco2_kwh) as max_actual_gco2_kwh,
    avg(national.wind_pct) as mean_wind_pct,
    avg(national.solar_pct) as mean_solar_pct,
    avg(national.gas_pct) as mean_gas_pct,
    avg(national.low_carbon_pct) as mean_low_carbon_pct,
    count(national.actual_gco2_kwh) as periods_with_actual,
    count(*) as periods,
    100.0 * count(national.actual_gco2_kwh) / count(*) as coverage_pct
from {{ ref('fct_national_intensity') }} as national
inner join {{ ref('dim_date') }} as dates on national.date_key = dates.date_key
group by dates.year_month, dates.calendar_year, dates.month_of_year, dates.season
