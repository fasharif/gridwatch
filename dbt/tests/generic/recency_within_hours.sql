{#- Freshness: fails when the newest timestamp is older than max_hours before "now".
    "now" is the as_of var when set (CI pins it to the fixture recording time), otherwise
    the clock. -#}
{% test recency_within_hours(model, column_name, max_hours) %}

with latest as (
    select max({{ column_name }}) as latest_ts
    from {{ model }}
)

select
    latest_ts,
    {{ as_of() }} as as_of_utc,
    {{ max_hours }} as max_hours
from latest
where latest_ts is null
   or latest_ts < {{ as_of() }} - to_hours({{ max_hours }})

{% endtest %}
