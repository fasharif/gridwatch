-- Grain: one row per half-hour and region. The API publishes only a forecast (no measured
-- actual) for regions, so this is modelled intensity.
select
    regional.period_start_utc,
    regional.region_id,
    cast(strftime(regional.period_start_local, '%Y%m%d') as integer) as date_key,
    {{ half_hour_slot('regional.period_start_local') }} as time_key,
    regional.period_start_local,
    regional.forecast_gco2_kwh,
    regional.intensity_index
from {{ ref('int_regional_half_hours') }} as regional
