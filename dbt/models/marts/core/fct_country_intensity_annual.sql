-- Grain: one row per country and year. Intensity is Ember's lifecycle figure in gCO2e/kWh
-- (upstream methane, supply chain and construction included), which is not comparable
-- with the GB API's operational gCO2/kWh.
select
    country_code,
    year,
    generation_twh,
    demand_twh,
    net_imports_twh,
    emissions_mtco2e,
    intensity_gco2e_kwh,
    intensity_gco2e_kwh - lag(intensity_gco2e_kwh) over (
        partition by country_code order by year
    ) as intensity_change_gco2e_kwh,
    gas_share_pct,
    coal_share_pct,
    other_fossil_share_pct,
    nuclear_share_pct,
    solar_share_pct,
    wind_share_pct,
    hydro_share_pct,
    bioenergy_share_pct,
    renewables_share_pct,
    clean_share_pct,
    fossil_share_pct
from {{ ref('int_ember_country_year') }}
