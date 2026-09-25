{#- Convert a naive UTC timestamp to naive UK local time (Europe/London, with BST). -#}
{% macro to_uk_local(column) -%}
    timezone('Europe/London', timezone('UTC', {{ column }}))
{%- endmacro %}

{#- Half-hour slot of the day, 0 to 47, for a naive timestamp. -#}
{% macro half_hour_slot(column) -%}
    (hour({{ column }}) * 2 + minute({{ column }}) // 30)
{%- endmacro %}

{#- The pipeline's notion of "now" as a naive UTC timestamp. Tests pass --vars as_of to make
    freshness checks reproducible against recorded fixtures. -#}
{% macro as_of() -%}
    {%- if var('as_of') is not none -%}
        cast('{{ var("as_of") }}' as timestamp)
    {%- else -%}
        cast(timezone('UTC', current_timestamp) as timestamp)
    {%- endif -%}
{%- endmacro %}

{#- Meteorological season for a month number. -#}
{% macro season(month_column) -%}
    case
        when {{ month_column }} in (12, 1, 2) then 'Winter'
        when {{ month_column }} in (3, 4, 5) then 'Spring'
        when {{ month_column }} in (6, 7, 8) then 'Summer'
        else 'Autumn'
    end
{%- endmacro %}
