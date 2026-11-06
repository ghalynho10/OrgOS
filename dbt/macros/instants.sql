{#- Spec 0006 AC-18: every instant is timestamptz in UTC, converted from the
    string's own +HHMM offset. The offset test on stg (AC-29) proves every
    string carries one, so the session zone never applies. An unparseable
    value fails the cast and so the build. -#}
{% macro to_utc_instant(expr) -%}
    ({{ expr }})::timestamptz
{%- endmacro %}

{#- A date only due value is midnight UTC at the start of that day, built
    explicitly in UTC, never in the session zone; any other value is an
    instant with an offset. -#}
{% macro due_to_utc_instant(expr) -%}
    case
        when ({{ expr }}) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            then (({{ expr }})::date)::timestamp at time zone 'UTC'
        else ({{ expr }})::timestamptz
    end
{%- endmacro %}
