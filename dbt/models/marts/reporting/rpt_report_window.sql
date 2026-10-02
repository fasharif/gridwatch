-- The period the GB reports summarise: by default the last 12 complete calendar months
-- (UK local dates) with actual data, clipped to the data available. Override with
-- --vars '{report_start_date: 2025-01-01, report_end_date: 2025-12-31}'.
with bounds as (
    select
        min(local_date) filter (where not is_actual_missing) as first_date,
        max(local_date) filter (where not is_actual_missing) as last_date
    from {{ ref('int_national_half_hours') }}
),

candidate as (
    select
        first_date,
        last_date,
        case
            when last_date = last_day(last_date) then last_date
            else cast(date_trunc('month', last_date) - interval 1 day as date)
        end as last_full_month_end
    from bounds
),

computed as (
    select
        {% if var('report_start_date') is not none -%}
        cast('{{ var("report_start_date") }}' as date)
        {%- else -%}
        greatest(first_date, cast(date_trunc('month', last_full_month_end) - interval 11 month as date))
        {%- endif %} as start_date,
        {% if var('report_end_date') is not none -%}
        cast('{{ var("report_end_date") }}' as date)
        {%- else -%}
        last_full_month_end
        {%- endif %} as end_date,
        first_date,
        last_date
    from candidate
)

select
    case when end_date >= start_date then start_date else first_date end as start_date,
    case when end_date >= start_date then end_date else last_date end as end_date,
    (case when end_date >= start_date then end_date else last_date end)
        - (case when end_date >= start_date then start_date else first_date end) + 1 as days,
    {{ var('batch_job_hours') }} as batch_job_hours
from computed
