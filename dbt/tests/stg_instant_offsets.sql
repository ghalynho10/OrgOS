-- Spec 0006 AC-29: every raw instant string ends in a +HHMM or -HHMM offset,
-- and duedate is either that or a plain YYYY-MM-DD. Without this, Postgres
-- would read a string with no offset in the session zone and AC-18's stop
-- would never fire. Attached to stg_issue_doc and stg_time_history, so a
-- failure stops the build before model_int and names the issue and the field.
-- A history time counts only on a time entry, the one place it is used.
{%- set offset = "'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?[+-][0-9]{4}$'" %}

with instant as (
    select key, 'created' as field, fields ->> 'created' as value
    from {{ ref('stg_issue_doc') }}
    union all
    select key, 'resolutiondate', fields ->> 'resolutiondate'
    from {{ ref('stg_issue_doc') }}
    union all
    select issue_key, 'changelog history ' || history_id || ' created', created
    from {{ ref('stg_time_history') }}
)

select key, field, value
from instant
where value is not null and value !~ {{ offset }}

union all

select key, 'duedate', fields ->> 'duedate'
from {{ ref('stg_issue_doc') }}
where fields ->> 'duedate' is not null
    and fields ->> 'duedate' !~ {{ offset }}
    and fields ->> 'duedate' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
