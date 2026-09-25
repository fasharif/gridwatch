-- GB national intensity per calendar year (UK local dates). is_complete_year is true only
-- when the year has at least 99% of its half-hours with an actual value.
select
    dates.calendar_year,
    avg(national.actual_gco2_kwh) as mean_actual_gco2_kwh,
    avg(national.low_carbon_pct) as mean_low_carbon_pct,
    avg(national.wind_pct) as mean_wind_pct,
    avg(national.gas_pct) as mean_gas_pct,
    avg(national.coal_pct) as mean_coal_pct,
    count(national.actual_gco2_kwh) as periods_with_actual,
    min(dates.calendar_date) as first_date,
    max(dates.calendar_date) as last_date,
    count(national.actual_gco2_kwh)
        >= 0.99 * 48 * (case when (dates.calendar_year % 4 = 0) then 366 else 365 end)
        as is_complete_year
from {{ ref('fct_national_intensity') }} as national
inner join {{ ref('dim_date') }} as dates on national.date_key = dates.date_key
group by dates.calendar_year
