-- Question (a) and (c): mean actual intensity for each UK local day of week and half-hour
-- slot across the report window. Drives the week heatmap.
select
    dates.iso_day_of_week,
    dates.day_name,
    national.time_key,
    times.start_time_label,
    avg(national.actual_gco2_kwh) as mean_actual_gco2_kwh,
    quantile_cont(national.actual_gco2_kwh, 0.5) as median_actual_gco2_kwh,
    count(national.actual_gco2_kwh) as periods
from {{ ref('fct_national_intensity') }} as national
inner join {{ ref('dim_date') }} as dates on national.date_key = dates.date_key
inner join {{ ref('dim_time_of_day') }} as times on national.time_key = times.time_key
cross join {{ ref('rpt_report_window') }} as report_window
where dates.calendar_date between report_window.start_date and report_window.end_date
  and national.actual_gco2_kwh is not null
group by all
