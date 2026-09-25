-- Question (d), like-for-like: accuracy of the API forecast by lead time, using only the
-- snapshots this pipeline stored before the target half-hour happened. This table fills
-- up as the daily workflow runs; it is empty until actuals exist for stored snapshots.
select
    case
        when lead_minutes < 0 then 'already started'
        when lead_minutes < 360 then '0-6 h'
        when lead_minutes < 720 then '6-12 h'
        when lead_minutes < 1440 then '12-24 h'
        else '24-48 h'
    end as lead_band,
    min(lead_minutes) as min_lead_minutes,
    count(*) as pairs,
    count(distinct issued_at_utc) as snapshots,
    avg(abs(forecast_error_gco2_kwh)) as mae_gco2_kwh,
    sqrt(avg(forecast_error_gco2_kwh * forecast_error_gco2_kwh)) as rmse_gco2_kwh,
    100.0 * avg(abs(forecast_error_gco2_kwh) / actual_gco2_kwh)
        filter (where actual_gco2_kwh > 0) as mape_pct,
    avg(forecast_error_gco2_kwh) as bias_gco2_kwh
from {{ ref('fct_api_forecast_snapshots') }}
where actual_gco2_kwh is not null
  and forecast_gco2_kwh is not null
group by 1
