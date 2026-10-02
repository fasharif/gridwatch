-- Grain: one row per GB half-hour and fuel.
select
    mix.period_start_utc,
    mix.fuel,
    half_hours.date_key,
    half_hours.time_key,
    mix.share_pct
from {{ ref('stg_carbon_intensity__generation_mix') }} as mix
inner join {{ ref('int_national_half_hours') }} as half_hours
    on mix.period_start_utc = half_hours.period_start_utc
