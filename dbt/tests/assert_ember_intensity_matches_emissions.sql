-- Ember's intensity column should equal emissions divided by generation. A mismatch of more
-- than 2% would mean the file changed meaning or units.
select
    country_code,
    year,
    intensity_gco2e_kwh,
    1000.0 * emissions_mtco2e / generation_twh as derived_intensity
from {{ ref('fct_country_intensity_annual') }}
where generation_twh > 0.5
  and abs(intensity_gco2e_kwh - 1000.0 * emissions_mtco2e / generation_twh)
      > 0.02 * intensity_gco2e_kwh
