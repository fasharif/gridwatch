{%- set periods = var('batch_job_hours') * 2 -%}
-- For every half-hour, the mean intensity of a job that starts then and runs for
-- batch_job_hours hours. The national fact sits on a gap-free spine, so a ROWS window of
-- (2 x batch_job_hours) rows is exactly that many hours of clock time.
-- Windows with any missing actual or forecast value are dropped.
with national as (
    select
        period_start_utc,
        period_start_local,
        date_key,
        time_key,
        actual_gco2_kwh,
        forecast_gco2_kwh
    from {{ ref('int_national_half_hours') }}
),

windowed as (
    select
        period_start_utc,
        period_start_local,
        date_key,
        time_key,
        avg(actual_gco2_kwh) over job as job_actual_avg,
        count(actual_gco2_kwh) over job as job_actual_n,
        avg(forecast_gco2_kwh) over job as job_forecast_avg,
        count(forecast_gco2_kwh) over job as job_forecast_n,
        count(*) over job as job_periods
    from national
    window job as (
        order by period_start_utc rows between current row and {{ periods - 1 }} following
    )
)

select
    period_start_utc as job_start_utc,
    period_start_local as job_start_local,
    cast(period_start_local as date) as job_start_date,
    date_key,
    time_key,
    job_actual_avg,
    job_forecast_avg
from windowed
where job_periods = {{ periods }}
  and job_actual_n = {{ periods }}
  and job_forecast_n = {{ periods }}
