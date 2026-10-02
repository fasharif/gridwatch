-- Question (c): mean forecast intensity per region, overall and per season, across the
-- report window, with the mean wind and low-carbon shares from the regional mix.
-- Regional values are the API's modelled forecasts; there is no regional actual.
with regional as (
    select
        regional.region_id,
        dates.season,
        regional.forecast_gco2_kwh
    from {{ ref('fct_regional_intensity') }} as regional
    inner join {{ ref('dim_date') }} as dates on regional.date_key = dates.date_key
    cross join {{ ref('rpt_report_window') }} as report_window
    where dates.calendar_date between report_window.start_date and report_window.end_date
      and regional.forecast_gco2_kwh is not null
),

intensity as (
    select
        region_id,
        avg(forecast_gco2_kwh) as mean_forecast_gco2_kwh,
        avg(forecast_gco2_kwh) filter (where season = 'Winter') as winter_mean_gco2_kwh,
        avg(forecast_gco2_kwh) filter (where season = 'Spring') as spring_mean_gco2_kwh,
        avg(forecast_gco2_kwh) filter (where season = 'Summer') as summer_mean_gco2_kwh,
        avg(forecast_gco2_kwh) filter (where season = 'Autumn') as autumn_mean_gco2_kwh,
        quantile_cont(forecast_gco2_kwh, 0.1) as p10_forecast_gco2_kwh,
        quantile_cont(forecast_gco2_kwh, 0.9) as p90_forecast_gco2_kwh,
        count(*) as periods
    from regional
    group by region_id
),

mix as (
    select
        mix.region_id,
        sum(mix.mean_share_pct * mix.periods) filter (where mix.fuel = 'wind')
            / sum(mix.periods) filter (where mix.fuel = 'wind') as mean_wind_pct,
        sum(mix.mean_share_pct * mix.periods) filter (where fuels.is_low_carbon)
            / sum(mix.periods) filter (where mix.fuel = 'wind') as mean_low_carbon_pct,
        sum(mix.mean_share_pct * mix.periods) filter (where fuels.is_fossil)
            / sum(mix.periods) filter (where mix.fuel = 'wind') as mean_fossil_pct
    from {{ ref('fct_regional_generation_mix_daily') }} as mix
    inner join {{ ref('dim_fuel') }} as fuels on mix.fuel = fuels.fuel
    inner join {{ ref('dim_date') }} as dates on mix.date_key = dates.date_key
    cross join {{ ref('rpt_report_window') }} as report_window
    where dates.calendar_date between report_window.start_date and report_window.end_date
    group by mix.region_id
)

select
    regions.region_id,
    regions.region_short_name,
    regions.nation,
    regions.is_aggregate,
    intensity.mean_forecast_gco2_kwh,
    intensity.winter_mean_gco2_kwh,
    intensity.spring_mean_gco2_kwh,
    intensity.summer_mean_gco2_kwh,
    intensity.autumn_mean_gco2_kwh,
    intensity.p10_forecast_gco2_kwh,
    intensity.p90_forecast_gco2_kwh,
    mix.mean_wind_pct,
    mix.mean_low_carbon_pct,
    mix.mean_fossil_pct,
    intensity.periods,
    case when not regions.is_aggregate
        then rank() over (
            partition by regions.is_aggregate order by intensity.mean_forecast_gco2_kwh
        )
    end as rank_lowest
from intensity
inner join {{ ref('dim_region') }} as regions on intensity.region_id = regions.region_id
left join mix on intensity.region_id = mix.region_id
