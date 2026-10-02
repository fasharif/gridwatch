select
    cast(period_start_utc as timestamp) as period_start_utc,
    cast(region_id as smallint) as region_id,
    lower(fuel) as fuel,
    cast(share_pct as double) as share_pct
from {{ source('carbon_intensity', 'regional_generation_mix') }}
