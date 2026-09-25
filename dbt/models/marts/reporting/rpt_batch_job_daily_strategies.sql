{%- set lookback = var('profile_lookback_days') -%}
-- Question (a): one run of the batch job per UK local day, scheduled by different rules.
-- Each column is the mean actual intensity the job experienced under that rule.
--
--   fixed_0000 / fixed_0900 / fixed_1700  start at the same local time every day
--   any_start          mean over every start time that day (a job started at random)
--   profile_guided     start at the slot with the lowest mean over the previous
--                      {{ lookback }} days, skipping the day before (its late windows run
--                      into today, so they are not fully known when the choice is made)
--   forecast_guided    start at the slot whose API forecast is lowest. The API keeps only
--                      its latest, short-lead forecast for past periods, so this is an
--                      optimistic stand-in for a day-ahead forecast.
--   oracle             the best start with perfect knowledge (a lower bound, not a plan)
with jobs as (
    select * from {{ ref('int_batch_job_windows') }}
),

slot_daily as (
    select job_start_date, time_key, avg(job_actual_avg) as slot_mean
    from jobs
    group by job_start_date, time_key
),

trailing_profile as (
    select
        job_start_date,
        time_key,
        avg(slot_mean) over lookback_window as trailing_mean,
        count(slot_mean) over lookback_window as trailing_days
    from slot_daily
    window lookback_window as (
        partition by time_key
        order by job_start_date
        range between interval {{ lookback + 1 }} day preceding and interval 2 day preceding
    )
),

profile_choice as (
    select job_start_date, arg_min(time_key, trailing_mean) as profile_time_key
    from trailing_profile
    where trailing_days >= {{ (lookback * 0.7) | round | int }}
    group by job_start_date
),

per_day as (
    select
        jobs.job_start_date,
        jobs.date_key,
        count(*) as start_options,
        max(jobs.job_actual_avg) filter (where jobs.time_key = 0) as fixed_0000,
        max(jobs.job_actual_avg) filter (where jobs.time_key = 18) as fixed_0900,
        max(jobs.job_actual_avg) filter (where jobs.time_key = 34) as fixed_1700,
        avg(jobs.job_actual_avg) as any_start,
        arg_min(jobs.job_actual_avg, jobs.job_forecast_avg) as forecast_guided,
        arg_min(jobs.time_key, jobs.job_forecast_avg) as forecast_time_key,
        min(jobs.job_actual_avg) as oracle,
        arg_min(jobs.time_key, jobs.job_actual_avg) as oracle_time_key
    from jobs
    group by jobs.job_start_date, jobs.date_key
),

profile_realised as (
    select
        profile_choice.job_start_date,
        profile_choice.profile_time_key,
        max(jobs.job_actual_avg) as profile_guided
    from profile_choice
    inner join jobs
        on profile_choice.job_start_date = jobs.job_start_date
        and profile_choice.profile_time_key = jobs.time_key
    group by all
)

select
    per_day.job_start_date,
    per_day.date_key,
    per_day.start_options,
    per_day.fixed_0000,
    per_day.fixed_0900,
    per_day.fixed_1700,
    per_day.any_start,
    profile_realised.profile_guided,
    profile_realised.profile_time_key,
    per_day.forecast_guided,
    per_day.forecast_time_key,
    per_day.oracle,
    per_day.oracle_time_key
from per_day
left join profile_realised on per_day.job_start_date = profile_realised.job_start_date
cross join {{ ref('rpt_report_window') }} as report_window
where per_day.job_start_date between report_window.start_date and report_window.end_date
  -- keep complete days only: 48 start options (46 or 50 on clock-change days)
  and per_day.start_options >= 46
  and per_day.fixed_0000 is not null
  and per_day.fixed_0900 is not null
  and per_day.fixed_1700 is not null
