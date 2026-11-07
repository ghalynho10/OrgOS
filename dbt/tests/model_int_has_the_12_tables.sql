-- Spec 0006 AC-1, AC-2: model_int holds exactly the 12 tables, nothing more
-- (no derived table fits, since the contracts fix every column). Lists any
-- extra table or view and any missing one. Depends on all 12, so it runs last.
-- depends_on: {{ ref('int_issue') }}, {{ ref('int_project') }}, {{ ref('int_person') }}
-- depends_on: {{ ref('int_fix_version') }}, {{ ref('int_in_project') }}
-- depends_on: {{ ref('int_assigned_to') }}, {{ ref('int_reported_by') }}
-- depends_on: {{ ref('int_created_by') }}, {{ ref('int_has_parent') }}
-- depends_on: {{ ref('int_issue_link') }}, {{ ref('int_fixed_in') }}, {{ ref('int_logged_time') }}
with expected (table_name) as (
    values
        ('issue'), ('project'), ('person'), ('fix_version'),
        ('in_project'), ('assigned_to'), ('reported_by'), ('created_by'),
        ('has_parent'), ('issue_link'), ('fixed_in'), ('logged_time')
),

found as (
    select table_name::text as table_name
    from information_schema.tables
    where table_schema = 'model_int'
)

select 'extra' as problem, table_name from found
where table_name not in (select table_name from expected)
union all
select 'missing', table_name from expected
where table_name not in (select table_name from found)
