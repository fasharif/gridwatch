-- Grain: one row per GB settlement half-hour (UTC start), gap-free between the first and
-- last period received. Grouped generation shares are denormalised onto the row because
-- nearly every analysis of intensity also looks at the mix.
select
    half_hours.period_start_utc,
    half_hours.period_end_utc,
    half_hours.period_start_local,
    half_hours.date_key,
    half_hours.time_key,
    half_hours.forecast_gco2_kwh,
    half_hours.actual_gco2_kwh,
    half_hours.forecast_gco2_kwh - half_hours.actual_gco2_kwh as forecast_error_gco2_kwh,
    half_hours.intensity_index,
    half_hours.is_missing_from_source,
    half_hours.is_actual_missing,
    half_hours.is_forecast_implausible,
    half_hours.is_actual_implausible,
    mix.period_start_utc is null as is_mix_missing,
    mix.gas_pct,
    mix.coal_pct,
    mix.nuclear_pct,
    mix.wind_pct,
    mix.solar_pct,
    mix.hydro_pct,
    mix.biomass_pct,
    mix.imports_pct,
    mix.other_pct,
    mix.fossil_pct,
    mix.renewable_pct,
    mix.low_carbon_pct
from {{ ref('int_national_half_hours') }} as half_hours
left join {{ ref('int_generation_mix_wide') }} as mix
    on half_hours.period_start_utc = mix.period_start_utc
