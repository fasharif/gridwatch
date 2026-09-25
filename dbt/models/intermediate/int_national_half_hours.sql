{%- set lo = var('plausible_min_gco2_kwh') -%}
{%- set hi = var('plausible_max_gco2_kwh') -%}
-- One row for every half-hour between the first and last period the API returned, so that
-- gaps in the source become explicit rows with null measures instead of silently missing.
-- Values outside the plausible range [{{ lo }}, {{ hi }}] gCO2/kWh are upstream errors (the
-- history holds forecasts of 13,579 and actuals of 0) and are set to null here; the raw
-- value is kept in the staging view and the row is flagged.
with national as (
    select * from {{ ref('stg_carbon_intensity__national_intensity') }}
),

bounds as (
    select
        min(period_start_utc) as first_period,
        max(period_start_utc) as last_period
    from national
),

spine as (
    select unnest(generate_series(first_period, last_period, interval 30 minute)) as period_start_utc
    from bounds
    where first_period is not null
),

localised as (
    select
        period_start_utc,
        {{ to_uk_local('period_start_utc') }} as period_start_local
    from spine
)

select
    localised.period_start_utc,
    localised.period_start_utc + interval 30 minute as period_end_utc,
    localised.period_start_local,
    cast(localised.period_start_local as date) as local_date,
    cast(strftime(localised.period_start_local, '%Y%m%d') as integer) as date_key,
    {{ half_hour_slot('localised.period_start_local') }} as time_key,
    case
        when national.forecast_gco2_kwh between {{ lo }} and {{ hi }}
            then national.forecast_gco2_kwh
    end as forecast_gco2_kwh,
    case
        when national.actual_gco2_kwh between {{ lo }} and {{ hi }}
            then national.actual_gco2_kwh
    end as actual_gco2_kwh,
    national.intensity_index,
    national.period_start_utc is null as is_missing_from_source,
    coalesce(national.forecast_gco2_kwh not between {{ lo }} and {{ hi }}, false)
        as is_forecast_implausible,
    coalesce(national.actual_gco2_kwh not between {{ lo }} and {{ hi }}, false)
        as is_actual_implausible,
    national.actual_gco2_kwh is null
        or national.actual_gco2_kwh not between {{ lo }} and {{ hi }} as is_actual_missing
from localised
left join national on localised.period_start_utc = national.period_start_utc
