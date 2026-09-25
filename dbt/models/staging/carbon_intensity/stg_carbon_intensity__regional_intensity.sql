select
    cast(period_start_utc as timestamp) as period_start_utc,
    cast(region_id as smallint) as region_id,
    region_short_name,
    cast(forecast_gco2_kwh as integer) as forecast_gco2_kwh,
    intensity_index
from {{ source('carbon_intensity', 'regional_intensity') }}
