{#- Fails for every gap in a half-hourly series: consecutive distinct timestamps that are
    more than 30 minutes apart. Gaps that the upstream API itself has (listed with a
    reason in the known_source_gaps seed for this dataset) are allowed, so the test catches
    new ingestion failures without failing forever on history nobody can repair.
    partition_by checks each partition (for example each region) separately. -#}
{% test no_half_hour_gaps(model, column_name, dataset, partition_by=none) %}

with timestamps as (
    select distinct
        {{ column_name }} as ts
        {%- if partition_by %}, {{ partition_by }} as part{% else %}, 1 as part{% endif %}
    from {{ model }}
    where {{ column_name }} is not null
),

ordered as (
    select
        part,
        ts,
        lead(ts) over (partition by part order by ts) as next_ts
    from timestamps
),

gaps as (
    select
        part,
        ts as gap_after_utc,
        next_ts as resumes_at_utc,
        cast(date_diff('minute', ts, next_ts) / 30 - 1 as integer) as missing_periods
    from ordered
    where next_ts is not null
      and next_ts <> ts + interval 30 minute
)

select gaps.*
from gaps
where not exists (
    select 1
    from {{ ref('known_source_gaps') }} as known
    where known.dataset = '{{ dataset }}'
      and gaps.gap_after_utc >= known.gap_after_utc
      and gaps.resumes_at_utc <= known.resumes_at_utc
)

{% endtest %}
