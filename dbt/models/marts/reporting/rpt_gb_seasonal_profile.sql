-- Question (c): the average day in each meteorological season (UK local time), across the
-- report window.
select
    dates.season,
    national.time_key,
    times.start_time_label,
    avg(national.actual_gco2_kwh) as mean_actual_gco2_kwh,
    avg(national.solar_pct) as mean_solar_pct,
    avg(national.wind_pct) as mean_wind_pct,
    count(national.actual_gco2_kwh) as periods
from {{ ref('fct_national_intensity') }} as national
inner join {{ ref('dim_date') }} as dates on national.date_key = dates.date_key
inner join {{ ref('dim_time_of_day') }} as times on national.time_key = times.time_key
cross join {{ ref('rpt_report_window') }} as report_window
where dates.calendar_date between report_window.start_date and report_window.end_date
  and national.actual_gco2_kwh is not null
group by all
