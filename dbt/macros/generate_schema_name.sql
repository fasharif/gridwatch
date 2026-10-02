{#- Use the configured schema name as-is (staging, marts, ...) instead of prefixing it
    with the target schema, so the warehouse layout is the same in every environment. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
