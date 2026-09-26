{{ config(severity='warn') }}
-- An early warning for assert_bank_holidays_cover_the_calendar: warn while the seed covers
-- the calendar but not the following year, so the seed can be extended before the error-level
-- test starts failing the daily build.
with seed as (
    select max(year(holiday_date)) as last_year
    from {{ ref('uk_bank_holidays') }}
),

calendar as (
    select max(calendar_year) as last_year
    from {{ ref('dim_date') }}
)

select calendar.last_year, seed.last_year as seed_last_year
from calendar
cross join seed
where calendar.last_year + 1 > seed.last_year
