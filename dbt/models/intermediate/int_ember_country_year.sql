-- One row per country (or region such as the EU) and year, for the areas in the countries
-- seed. Shares come from Ember's own "Share of generation" column.
with ember as (
    select ember.*
    from {{ ref('stg_ember__yearly_electricity') }} as ember
    inner join {{ ref('countries') }} as countries on ember.area = countries.ember_area
),

pivoted as (
    select
        area,
        year,
        max(generation_twh) filter (where electricity_source = 'Total generation') as generation_twh,
        max(generation_twh) filter (where electricity_source = 'Demand') as demand_twh,
        max(generation_twh) filter (where electricity_source = 'Net imports') as net_imports_twh,
        max(emissions_mtco2e) filter (where electricity_source = 'Total generation')
            as emissions_mtco2e,
        max(emissions_intensity_gco2e_kwh) filter (where electricity_source = 'Total generation')
            as intensity_gco2e_kwh,
        max(share_of_generation_pct) filter (where electricity_source = 'Gas') as gas_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Coal') as coal_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Other fossil')
            as other_fossil_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Nuclear')
            as nuclear_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Solar') as solar_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Wind') as wind_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Hydro') as hydro_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Bioenergy')
            as bioenergy_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Renewables')
            as renewables_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Clean') as clean_share_pct,
        max(share_of_generation_pct) filter (where electricity_source = 'Fossil')
            as fossil_share_pct
    from ember
    group by area, year
)

select
    countries.country_code,
    pivoted.year,
    pivoted.generation_twh,
    pivoted.demand_twh,
    pivoted.net_imports_twh,
    pivoted.emissions_mtco2e,
    pivoted.intensity_gco2e_kwh,
    coalesce(pivoted.gas_share_pct, 0) as gas_share_pct,
    coalesce(pivoted.coal_share_pct, 0) as coal_share_pct,
    coalesce(pivoted.other_fossil_share_pct, 0) as other_fossil_share_pct,
    coalesce(pivoted.nuclear_share_pct, 0) as nuclear_share_pct,
    coalesce(pivoted.solar_share_pct, 0) as solar_share_pct,
    coalesce(pivoted.wind_share_pct, 0) as wind_share_pct,
    coalesce(pivoted.hydro_share_pct, 0) as hydro_share_pct,
    coalesce(pivoted.bioenergy_share_pct, 0) as bioenergy_share_pct,
    coalesce(pivoted.renewables_share_pct, 0) as renewables_share_pct,
    coalesce(pivoted.clean_share_pct, 0) as clean_share_pct,
    coalesce(pivoted.fossil_share_pct, 0) as fossil_share_pct
from pivoted
inner join {{ ref('countries') }} as countries on pivoted.area = countries.ember_area
where pivoted.generation_twh is not null
