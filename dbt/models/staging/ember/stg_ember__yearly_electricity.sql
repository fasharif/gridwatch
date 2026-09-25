select
    area,
    nullif(iso3_code, '') as iso3_code,
    area_type,
    cast(year as integer) as year,
    electricity_source,
    is_aggregated_source,
    generation_twh,
    share_of_generation_pct,
    capacity_gw,
    emissions_mtco2e,
    emissions_intensity_gco2e_kwh,
    is_eu_member
from {{ source('ember', 'yearly_electricity') }}
