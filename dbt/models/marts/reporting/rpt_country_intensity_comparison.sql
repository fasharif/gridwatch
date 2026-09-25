-- Question (b): each country against the UK and the EU in the latest year that every
-- compared area has data for, with the change since 2015 and the mix behind it.
with trend as (
    select * from {{ ref('rpt_country_intensity_trend') }}
),

common_year as (
    select min(latest_year) as year
    from (
        select country_code, max(year) as latest_year
        from trend
        where country_code <> 'WLD'
        group by country_code
    )
),

latest as (
    select trend.*
    from trend
    inner join common_year on trend.year = common_year.year
),

reference as (
    select
        max(intensity_gco2e_kwh) filter (where country_code = 'GBR') as uk_intensity,
        max(intensity_gco2e_kwh) filter (where country_code = 'EU27') as eu_intensity
    from latest
),

baseline_2015 as (
    select country_code, intensity_gco2e_kwh as intensity_2015
    from trend
    where year = 2015
),

mix as (
    select *
    from {{ ref('fct_country_intensity_annual') }}
)

select
    latest.country_code,
    latest.country_name,
    latest.peer_group,
    latest.is_aggregate,
    latest.year as comparison_year,
    latest.intensity_gco2e_kwh,
    latest.intensity_gco2e_kwh / reference.uk_intensity as ratio_to_uk,
    latest.intensity_gco2e_kwh / reference.eu_intensity as ratio_to_eu,
    baseline_2015.intensity_2015 as intensity_2015_gco2e_kwh,
    100.0 * (latest.intensity_gco2e_kwh - baseline_2015.intensity_2015)
        / baseline_2015.intensity_2015 as change_since_2015_pct,
    latest.generation_twh,
    mix.gas_share_pct,
    mix.coal_share_pct,
    mix.other_fossil_share_pct,
    mix.nuclear_share_pct,
    mix.solar_share_pct,
    mix.wind_share_pct,
    mix.clean_share_pct
from latest
cross join reference
left join baseline_2015 on latest.country_code = baseline_2015.country_code
left join mix on latest.country_code = mix.country_code and latest.year = mix.year
