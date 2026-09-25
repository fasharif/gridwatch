select
    fuel,
    fuel_label,
    fuel_category,
    is_fossil,
    is_renewable,
    is_low_carbon,
    sort_order
from {{ ref('fuels') }}
