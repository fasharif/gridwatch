-- Calendar dimension in UK local dates, covering the national series plus two days for
-- forecasts. Bank holidays are England and Wales holidays from the uk_bank_holidays seed.
with bounds as (
    select
        min(local_date) as first_date,
        max(local_date) + 2 as last_date
    from {{ ref('int_national_half_hours') }}
),

days as (
    select cast(unnest(generate_series(first_date, last_date, interval 1 day)) as date) as calendar_date
    from bounds
    where first_date is not null
),

holidays as (
    select holiday_date, min(holiday_name) as holiday_name
    from {{ ref('uk_bank_holidays') }}
    group by holiday_date
)

select
    cast(strftime(days.calendar_date, '%Y%m%d') as integer) as date_key,
    days.calendar_date,
    year(days.calendar_date) as calendar_year,
    quarter(days.calendar_date) as calendar_quarter,
    month(days.calendar_date) as month_of_year,
    strftime(days.calendar_date, '%B') as month_name,
    cast(strftime(days.calendar_date, '%Y-%m') as varchar) as year_month,
    day(days.calendar_date) as day_of_month,
    isodow(days.calendar_date) as iso_day_of_week,
    strftime(days.calendar_date, '%A') as day_name,
    isodow(days.calendar_date) in (6, 7) as is_weekend,
    holidays.holiday_date is not null as is_bank_holiday,
    holidays.holiday_name as bank_holiday_name,
    isodow(days.calendar_date) not in (6, 7) and holidays.holiday_date is null as is_working_day,
    {{ season('month(days.calendar_date)') }} as season
from days
left join holidays on days.calendar_date = holidays.holiday_date
