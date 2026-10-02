-- The perfect-foresight schedule is the minimum by definition. Any day where another rule
-- beats it means the job windows or the strategy logic are wrong.
select *
from {{ ref('rpt_batch_job_daily_strategies') }}
where oracle > least(
    fixed_0000,
    fixed_0900,
    fixed_1700,
    any_start,
    coalesce(profile_guided, fixed_0000),
    forecast_guided
) + 1e-9
