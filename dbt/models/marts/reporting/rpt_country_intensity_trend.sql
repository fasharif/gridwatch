-- Question (b): annual lifecycle intensity from 2000 for each country, plus a
-- generation-weighted GCC aggregate for years in which all six GCC members have data.
with countries as (
    select
        annual.country_code,
        dims.country_name,
        dims.peer_group,
        annual.year,
        annual.generation_twh,
        annual.emissions_mtco2e,
        annual.intensity_gco2e_kwh
    from {{ ref('fct_country_intensity_annual') }} as annual
    inner join {{ ref('dim_country') }} as dims on annual.country_code = dims.country_code
    where annual.year >= 2000
),

gcc as (
    select
        'GCC' as country_code,
        'GCC (generation-weighted)' as country_name,
        'GCC aggregate' as peer_group,
        year,
        sum(generation_twh) as generation_twh,
        sum(emissions_mtco2e) as emissions_mtco2e,
        1000.0 * sum(emissions_mtco2e) / sum(generation_twh) as intensity_gco2e_kwh
    from countries
    where peer_group = 'GCC'
    group by year
    having count(*) = 6
)

select *, false as is_aggregate from countries
union all
select *, true as is_aggregate from gcc
