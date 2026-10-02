-- Grain: one row per UK local date, region and fuel: the mean half-hourly share. Kept at
-- daily grain because the half-hourly regional mix is large and no analysis needs it.
select
    cast(strftime({{ to_uk_local('mix.period_start_utc') }}, '%Y%m%d') as integer) as date_key,
    mix.region_id,
    mix.fuel,
    avg(mix.share_pct) as mean_share_pct,
    count(*) as periods
from {{ ref('stg_carbon_intensity__regional_generation_mix') }} as mix
group by all
