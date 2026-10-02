{#- Fails for timestamps that are not exactly on :00 or :30. -#}
{% test aligned_to_half_hour(model, column_name) %}

select {{ column_name }} as offending_value
from {{ model }}
where {{ column_name }} is not null
  and (minute({{ column_name }}) not in (0, 30) or second({{ column_name }}) <> 0)

{% endtest %}
