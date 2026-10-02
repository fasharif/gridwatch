{%- set lo = var('plausible_min_gco2_kwh') -%}
{%- set hi = var('plausible_max_gco2_kwh') -%}
-- Grain: one row per forecast issue time and target half-hour. Each pipeline run stores the
-- API's 48-hour forecast as it stood at that moment, so accuracy by lead time can be
-- measured once the actual values arrive. The API itself keeps only its latest forecast
-- for past periods, so these snapshots are gridwatch's only day-ahead record.
-- Forecasts outside the plausible range [{{ lo }}, {{ hi }}] gCO2/kWh are set to null and
-- flagged, with the same rule int_national_half_hours applies to the national series; the
-- raw value stays in the staging view.
select
    snapshots.issued_at_utc,
    snapshots.period_start_utc,
    snapshots.lead_minutes,
    cast(strftime({{ to_uk_local('snapshots.period_start_utc') }}, '%Y%m%d') as integer)
        as date_key,
    case
        when snapshots.forecast_gco2_kwh between {{ lo }} and {{ hi }}
            then snapshots.forecast_gco2_kwh
    end as forecast_gco2_kwh,
    coalesce(snapshots.forecast_gco2_kwh not between {{ lo }} and {{ hi }}, false)
        as is_forecast_implausible,
    national.actual_gco2_kwh,
    case
        when snapshots.forecast_gco2_kwh between {{ lo }} and {{ hi }}
            then snapshots.forecast_gco2_kwh - national.actual_gco2_kwh
    end as forecast_error_gco2_kwh
from {{ ref('stg_carbon_intensity__forecast_snapshots') }} as snapshots
left join {{ ref('int_national_half_hours') }} as national
    on snapshots.period_start_utc = national.period_start_utc
