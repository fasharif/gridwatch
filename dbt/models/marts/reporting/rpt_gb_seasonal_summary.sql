-- Question (c): one row per season in the report window: level, spread within the day, and
-- the generation shares behind it.
with daily as (
    select
        dates.calendar_date,
        dates.season,
        avg(national.actual_gco2_kwh) as day_mean,
        max(national.actual_gco2_kwh) - min(national.actual_gco2_kwh) as day_range
    from {{ ref('fct_national_intensity') }} as national
    inner join {{ ref('dim_date') }} as dates on national.date_key = dates.date_key
    cross join {{ ref('rpt_report_window') }} as report_window
    where dates.calendar_date between report_window.start_date and report_window.end_date
      and national.actual_gco2_kwh is not null
    group by all
),

half_hourly as (
    select
        dates.season,
        avg(national.actual_gco2_kwh) as mean_actual_gco2_kwh,
        quantile_cont(national.actual_gco2_kwh, 0.1) as p10_actual_gco2_kwh,
        quantile_cont(national.actual_gco2_kwh, 0.9) as p90_actual_gco2_kwh,
        avg(national.wind_pct) as mean_wind_pct,
        avg(national.solar_pct) as mean_solar_pct,
        avg(national.gas_pct) as mean_gas_pct,
        count(national.actual_gco2_kwh) as periods
    from {{ ref('fct_national_intensity') }} as national
    inner join {{ ref('dim_date') }} as dates on national.date_key = dates.date_key
    cross join {{ ref('rpt_report_window') }} as report_window
    where dates.calendar_date between report_window.start_date and report_window.end_date
      and national.actual_gco2_kwh is not null
    group by dates.season
)

select
    half_hourly.season,
    case half_hourly.season
        when 'Winter' then 1 when 'Spring' then 2 when 'Summer' then 3 else 4
    end as season_order,
    half_hourly.mean_actual_gco2_kwh,
    half_hourly.p10_actual_gco2_kwh,
    half_hourly.p90_actual_gco2_kwh,
    avg(daily.day_range) as mean_daily_range_gco2_kwh,
    half_hourly.mean_wind_pct,
    half_hourly.mean_solar_pct,
    half_hourly.mean_gas_pct,
    half_hourly.periods,
    count(daily.calendar_date) as days
from half_hourly
inner join daily on half_hourly.season = daily.season
group by all
