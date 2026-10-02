-- Question (a): the same job, by UK local day of week and start time, to find the
-- lowest-carbon time of the week.
select
    dates.iso_day_of_week,
    dates.day_name,
    jobs.time_key,
    times.start_time_label,
    avg(jobs.job_actual_avg) as mean_job_gco2_kwh,
    count(*) as jobs,
    rank() over (order by avg(jobs.job_actual_avg)) as rank_lowest
from {{ ref('int_batch_job_windows') }} as jobs
inner join {{ ref('dim_date') }} as dates on jobs.date_key = dates.date_key
inner join {{ ref('dim_time_of_day') }} as times on jobs.time_key = times.time_key
cross join {{ ref('rpt_report_window') }} as report_window
where jobs.job_start_date between report_window.start_date and report_window.end_date
group by all
