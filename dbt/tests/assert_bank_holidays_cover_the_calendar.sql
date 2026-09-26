-- dim_date takes England and Wales bank holidays from the uk_bank_holidays seed, which lists
-- a fixed range of years. Fail as soon as the calendar reaches a year the seed does not cover,
-- so that a holiday is never silently counted as a working day. To fix: raise LAST_YEAR in
-- scripts/generate_bank_holidays.py, run it, and commit the regenerated seed.
with seed as (
    select
        min(year(holiday_date)) as first_year,
        max(year(holiday_date)) as last_year
    from {{ ref('uk_bank_holidays') }}
),

calendar as (
    select
        min(calendar_year) as first_year,
        max(calendar_year) as last_year
    from {{ ref('dim_date') }}
)

select
    calendar.first_year,
    calendar.last_year,
    seed.first_year as seed_first_year,
    seed.last_year as seed_last_year
from calendar
cross join seed
where calendar.first_year < seed.first_year
   or calendar.last_year > seed.last_year
