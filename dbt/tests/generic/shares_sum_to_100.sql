{#- Fails for every group whose percentage shares do not add up to 100 within tolerance.
    The API rounds each fuel share to one decimal place, so small drift is expected. -#}
{% test shares_sum_to_100(model, column_name, group_by, tolerance=1.0) %}

select
    {{ group_by | join(', ') }},
    sum({{ column_name }}) as total_share
from {{ model }}
group by {{ group_by | join(', ') }}
having abs(sum({{ column_name }}) - 100) > {{ tolerance }}

{% endtest %}
