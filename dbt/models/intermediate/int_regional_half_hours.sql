select
    regional.period_start_utc,
    {{ to_uk_local('regional.period_start_utc') }} as period_start_local,
    regional.region_id,
    regional.forecast_gco2_kwh,
    regional.intensity_index
from {{ ref('stg_carbon_intensity__regional_intensity') }} as regional
