-- GB regions used by the regional endpoint: 14 distribution network operator (DNO) regions
-- plus the England, Scotland, Wales and GB aggregates.
select
    region_id,
    region_short_name,
    nation,
    is_aggregate
from {{ ref('gb_regions') }}
