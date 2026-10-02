select
    cast(issued_at_utc as timestamp) as issued_at_utc,
    cast(period_start_utc as timestamp) as period_start_utc,
    cast(forecast_gco2_kwh as integer) as forecast_gco2_kwh,
    intensity_index,
    cast(date_diff('minute', issued_at_utc, period_start_utc) as integer) as lead_minutes
from {{ source('carbon_intensity', 'forecast_snapshots') }}
