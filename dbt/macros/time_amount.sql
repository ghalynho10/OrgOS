{#- Spec 0004 AC-7, as spec 0006 AC-14 applies it: one side of a timespent
    item is its raw value, or its String value when the raw one is null or
    empty; null here means both are empty, which counts as 0. The offset of
    `to` minus `from` is the entry's amount in seconds. -#}
{% macro used_amount(raw_column, string_column) -%}
    coalesce(nullif({{ raw_column }}, ''), nullif({{ string_column }}, ''))
{%- endmacro %}

{% macro item_seconds(from_raw, from_string, to_raw, to_string) -%}
    (
        coalesce({{ used_amount(to_raw, to_string) }}, '0')::bigint
        - coalesce({{ used_amount(from_raw, from_string) }}, '0')::bigint
    )
{%- endmacro %}

{#- The elements of a json array, none when the key is missing or json null. -#}
{% macro array_elements(expr) -%}
    jsonb_array_elements(nullif({{ expr }}, 'null'::jsonb))
{%- endmacro %}
