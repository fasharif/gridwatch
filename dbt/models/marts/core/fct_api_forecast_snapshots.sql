-- Grain: one row per forecast issue time and target half-hour. Each pipeline run stores the
-- API's 48-hour forecast as it stood at that moment, so accuracy by lead time can be
-- measured once the actual values arrive. The API itself keeps only its latest forecast
-- for past periods, which is why these snapshots are the only day-ahead record.
select
    snapshots.issued_at_utc,
    snapshots.period_start_utc,
    snapshots.lead_minutes,
    cast(strftime({{ to_uk_local('snapshots.period_start_utc') }}, '%Y%m%d') as integer)
        as date_key,
    snapshots.forecast_gco2_kwh,
    national.actual_gco2_kwh,
    snapshots.forecast_gco2_kwh - national.actual_gco2_kwh as forecast_error_gco2_kwh
from {{ ref('stg_carbon_intensity__forecast_snapshots') }} as snapshots
left join {{ ref('int_national_half_hours') }} as national
    on snapshots.period_start_utc = national.period_start_utc
