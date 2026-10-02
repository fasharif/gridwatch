select
    cast(period_start_utc as timestamp) as period_start_utc,
    lower(fuel) as fuel,
    cast(share_pct as double) as share_pct
from {{ source('carbon_intensity', 'generation_mix') }}
