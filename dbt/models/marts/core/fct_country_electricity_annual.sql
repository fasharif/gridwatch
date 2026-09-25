-- Grain: one row per country, year and electricity source (Ember's categories, including
-- its aggregates such as "Fossil" and "Total generation", flagged by is_aggregated_source).
select
    countries.country_code,
    ember.year,
    ember.electricity_source,
    ember.is_aggregated_source,
    ember.generation_twh,
    ember.share_of_generation_pct,
    ember.capacity_gw,
    ember.emissions_mtco2e,
    ember.emissions_intensity_gco2e_kwh
from {{ ref('stg_ember__yearly_electricity') }} as ember
inner join {{ ref('countries') }} as countries on ember.area = countries.ember_area
