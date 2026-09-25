-- Question (a): mean intensity per scheduling rule over the same set of days, and the saving
-- against each fixed schedule. kg_per_run is an illustration for a job drawing
-- batch_job_kw kW for batch_job_hours hours; it scales linearly with the job's energy.
{%- set kw = var('batch_job_kw', 100) -%}
{%- set hours = var('batch_job_hours') %}
with complete_days as (
    select *
    from {{ ref('rpt_batch_job_daily_strategies') }}
    where profile_guided is not null
      and forecast_guided is not null
),

long as (
    unpivot complete_days
    on fixed_0000, fixed_0900, fixed_1700, any_start, profile_guided, forecast_guided, oracle
    into name strategy value job_gco2_kwh
),

summary as (
    select
        strategy,
        avg(job_gco2_kwh) as mean_job_gco2_kwh,
        count(*) as days
    from long
    group by strategy
),

baselines as (
    select
        max(mean_job_gco2_kwh) filter (where strategy = 'fixed_0000') as base_0000,
        max(mean_job_gco2_kwh) filter (where strategy = 'fixed_0900') as base_0900,
        max(mean_job_gco2_kwh) filter (where strategy = 'fixed_1700') as base_1700
    from summary
)

select
    summary.strategy,
    case summary.strategy
        when 'fixed_0000' then 'Fixed start 00:00 UK time'
        when 'fixed_0900' then 'Fixed start 09:00 UK time'
        when 'fixed_1700' then 'Fixed start 17:00 UK time'
        when 'any_start' then 'Average over all start times'
        when 'profile_guided' then 'Historical profile (previous {{ var("profile_lookback_days") }} days)'
        when 'forecast_guided' then 'API forecast (short lead, optimistic)'
        when 'oracle' then 'Perfect foresight (lower bound)'
    end as strategy_label,
    case summary.strategy
        when 'fixed_0000' then 1 when 'fixed_0900' then 2 when 'fixed_1700' then 3
        when 'any_start' then 4 when 'profile_guided' then 5 when 'forecast_guided' then 6
        else 7
    end as sort_order,
    summary.days,
    summary.mean_job_gco2_kwh,
    baselines.base_0900 - summary.mean_job_gco2_kwh as saving_vs_0900_gco2_kwh,
    100.0 * (baselines.base_0900 - summary.mean_job_gco2_kwh) / baselines.base_0900
        as saving_vs_0900_pct,
    baselines.base_1700 - summary.mean_job_gco2_kwh as saving_vs_1700_gco2_kwh,
    100.0 * (baselines.base_1700 - summary.mean_job_gco2_kwh) / baselines.base_1700
        as saving_vs_1700_pct,
    baselines.base_0000 - summary.mean_job_gco2_kwh as saving_vs_0000_gco2_kwh,
    100.0 * (baselines.base_0000 - summary.mean_job_gco2_kwh) / baselines.base_0000
        as saving_vs_0000_pct,
    {{ kw }} as illustrative_job_kw,
    {{ hours }} as batch_job_hours,
    summary.mean_job_gco2_kwh * {{ kw }} * {{ hours }} / 1000.0 as kg_co2_per_run
from summary
cross join baselines
