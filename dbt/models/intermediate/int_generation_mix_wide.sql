-- National generation mix pivoted to one row per half-hour, with grouped shares that use
-- the fuel classification in the fuels seed.
with mix as (
    select
        mix.period_start_utc,
        mix.fuel,
        mix.share_pct,
        fuels.is_fossil,
        fuels.is_renewable,
        fuels.is_low_carbon
    from {{ ref('stg_carbon_intensity__generation_mix') }} as mix
    inner join {{ ref('fuels') }} as fuels on mix.fuel = fuels.fuel
)

select
    period_start_utc,
    sum(share_pct) filter (where fuel = 'gas') as gas_pct,
    sum(share_pct) filter (where fuel = 'coal') as coal_pct,
    sum(share_pct) filter (where fuel = 'nuclear') as nuclear_pct,
    sum(share_pct) filter (where fuel = 'wind') as wind_pct,
    sum(share_pct) filter (where fuel = 'solar') as solar_pct,
    sum(share_pct) filter (where fuel = 'hydro') as hydro_pct,
    sum(share_pct) filter (where fuel = 'biomass') as biomass_pct,
    sum(share_pct) filter (where fuel = 'imports') as imports_pct,
    sum(share_pct) filter (where fuel = 'other') as other_pct,
    coalesce(sum(share_pct) filter (where is_fossil), 0) as fossil_pct,
    coalesce(sum(share_pct) filter (where is_renewable), 0) as renewable_pct,
    coalesce(sum(share_pct) filter (where is_low_carbon), 0) as low_carbon_pct,
    sum(share_pct) as total_pct
from mix
group by period_start_utc
