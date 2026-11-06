{#- Spec 0006 AC-19: no text column of a model_int table holds an empty
    string. Applied once per table, it checks every text column the table
    has, so a new column cannot be forgotten. Each failing row names the
    column and the row's key. -#}
{% test no_empty_strings(model, key_column) %}
{%- set text_columns = [] -%}
{%- if execute -%}
    {%- for column in adapter.get_columns_in_relation(model) -%}
        {%- if column.is_string() -%}
            {%- do text_columns.append(column.name) -%}
        {%- endif -%}
    {%- endfor -%}
{%- endif -%}
{%- for name in text_columns %}
select '{{ name }}' as column_name, {{ adapter.quote(key_column) }}::text as row_key
from {{ model }}
where {{ adapter.quote(name) }} = ''
{% if not loop.last %}union all{% endif %}
{%- endfor %}
{%- if not text_columns %}
select null::text as column_name, null::text as row_key where false
{%- endif %}
{% endtest %}
