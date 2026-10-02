-- The reusable CSV (exports/annual_grid_intensity.csv): annual average grid intensity for
-- the UAE, the other GCC countries, the UK and the EU, with source and licence on every row
-- so the file stays attributable when copied into another project.
select
    annual.country_code,
    dims.country_name,
    dims.peer_group,
    annual.year,
    round(annual.intensity_gco2e_kwh, 1) as intensity_gco2e_per_kwh,
    round(annual.generation_twh, 2) as generation_twh,
    round(annual.emissions_mtco2e, 3) as emissions_mtco2e,
    'lifecycle, CO2-equivalent' as emissions_basis,
    'Ember Yearly Electricity Data' as source,
    'https://ember-energy.org/data/yearly-electricity-data/' as source_url,
    'CC BY 4.0' as licence,
    annual.year = dims.latest_year as is_latest_year
from {{ ref('fct_country_intensity_annual') }} as annual
inner join {{ ref('dim_country') }} as dims on annual.country_code = dims.country_code
where dims.peer_group in ('GCC', 'UK', 'EU')
  and annual.year >= 2000
