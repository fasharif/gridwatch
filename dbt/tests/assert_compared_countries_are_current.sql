-- Every compared area must have data within two years of the most recent area, otherwise
-- the "latest common year" comparison would silently fall back to stale figures.
with latest as (
    select max(latest_year) as newest from {{ ref('dim_country') }}
)

select dim_country.*
from {{ ref('dim_country') }}
cross join latest
where dim_country.latest_year is null
   or dim_country.latest_year < latest.newest - 2
