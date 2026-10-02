{#- Fails for every non-null value outside [min_value, max_value]. Either bound may be
    omitted. Used for "plausible value" checks such as intensity between 0 and 1000. -#}
{% test accepted_range(model, column_name, min_value=none, max_value=none) %}

select {{ column_name }} as offending_value
from {{ model }}
where {{ column_name }} is not null
  and (
      {%- if min_value is not none %} {{ column_name }} < {{ min_value }} {% else %} false {% endif -%}
      or
      {%- if max_value is not none %} {{ column_name }} > {{ max_value }} {% else %} false {% endif -%}
  )

{% endtest %}
