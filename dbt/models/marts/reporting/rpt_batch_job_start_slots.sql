-- Question (a): for a flexible job of batch_job_hours hours, the mean intensity it would
-- have seen for each UK local start time, across the report window, overall and split into
-- working days and non-working days (weekends and bank holidays).
with jobs as (
    select
        jobs.time_key,
        jobs.job_actual_avg,
        dates.is_working_day
    from {{ ref('int_batch_job_windows') }} as jobs
    inner join {{ ref('dim_date') }} as dates on jobs.date_key = dates.date_key
    cross join {{ ref('rpt_report_window') }} as report_window
    where jobs.job_start_date between report_window.start_date and report_window.end_date
),

by_slot as (
    select
        time_key,
        avg(job_actual_avg) as mean_job_gco2_kwh,
        avg(job_actual_avg) filter (where is_working_day) as working_day_mean_job_gco2_kwh,
        avg(job_actual_avg) filter (where not is_working_day) as non_working_day_mean_job_gco2_kwh,
        count(*) as jobs
    from jobs
    group by time_key
)

select
    by_slot.time_key,
    times.start_time_label,
    by_slot.mean_job_gco2_kwh,
    by_slot.working_day_mean_job_gco2_kwh,
    by_slot.non_working_day_mean_job_gco2_kwh,
    by_slot.jobs,
    rank() over (order by by_slot.mean_job_gco2_kwh) as rank_lowest
from by_slot
inner join {{ ref('dim_time_of_day') }} as times on by_slot.time_key = times.time_key
