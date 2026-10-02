-- Question (d): how close the API's retained forecast is to its own actual value.
-- The API keeps only its latest forecast for a past half-hour, so this measures a
-- short-lead forecast, not a day-ahead one. MAPE skips half-hours with an actual of zero.
-- Scopes: every calendar year, every season of the report window, and the report window.
with pairs as (
    select
        dates.calendar_year,
        dates.season,
        dates.calendar_date,
        national.forecast_gco2_kwh as forecast,
        national.actual_gco2_kwh as actual,
        national.forecast_error_gco2_kwh as error
    from {{ ref('fct_national_intensity') }} as national
    inner join {{ ref('dim_date') }} as dates on national.date_key = dates.date_key
    where national.forecast_gco2_kwh is not null
      and national.actual_gco2_kwh is not null
),

scoped as (
    select 'year' as scope, cast(calendar_year as varchar) as scope_value, pairs.*
    from pairs
    union all
    select 'season' as scope, pairs.season as scope_value, pairs.*
    from pairs
    cross join {{ ref('rpt_report_window') }} as report_window
    where pairs.calendar_date between report_window.start_date and report_window.end_date
    union all
    select 'report_window' as scope,
        strftime(report_window.start_date, '%Y-%m-%d') || ' to '
            || strftime(report_window.end_date, '%Y-%m-%d') as scope_value,
        pairs.*
    from pairs
    cross join {{ ref('rpt_report_window') }} as report_window
    where pairs.calendar_date between report_window.start_date and report_window.end_date
)

select
    scope,
    scope_value,
    count(*) as periods,
    avg(abs(error)) as mae_gco2_kwh,
    sqrt(avg(error * error)) as rmse_gco2_kwh,
    100.0 * avg(abs(error) / actual) filter (where actual > 0) as mape_pct,
    avg(error) as bias_gco2_kwh,
    100.0 * avg(case when abs(error) <= 10 then 1 else 0 end) as within_10_pct,
    100.0 * avg(case when abs(error) <= 25 then 1 else 0 end) as within_25_pct,
    avg(actual) as mean_actual_gco2_kwh
from scoped
group by scope, scope_value
