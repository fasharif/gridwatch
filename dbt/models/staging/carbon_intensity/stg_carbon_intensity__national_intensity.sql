select
    cast(period_start_utc as timestamp) as period_start_utc,
    cast(period_end_utc as timestamp) as period_end_utc,
    cast(forecast_gco2_kwh as integer) as forecast_gco2_kwh,
    cast(actual_gco2_kwh as integer) as actual_gco2_kwh,
    intensity_index,
    cast(fetched_at_utc as timestamp) as fetched_at_utc
from {{ source('carbon_intensity', 'national_intensity') }}
