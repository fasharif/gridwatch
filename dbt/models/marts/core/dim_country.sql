-- Countries and regions compared in the international analysis. EU is Ember's EU-27
-- aggregate and World is included only as a benchmark.
select
    countries.country_code,
    countries.display_name as country_name,
    countries.ember_area,
    countries.peer_group,
    countries.is_gcc,
    countries.sort_order,
    min(country_year.year) as first_year,
    max(country_year.year) as latest_year
from {{ ref('countries') }} as countries
left join {{ ref('int_ember_country_year') }} as country_year
    on countries.country_code = country_year.country_code
group by all
