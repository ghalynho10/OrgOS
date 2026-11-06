-- Spec 0007 AC-1: model holds exactly the 7 tables and no view, materialized
-- view or other relation (pg_class, since information_schema hides
-- materialized views). Lists any extra relation and any missing table. Depends
-- on all 7, so it runs after them; the scaffold probe exists only in the
-- safety test, which builds nothing else.
-- depends_on: {{ ref('issue') }}, {{ ref('project') }}, {{ ref('person') }}
-- depends_on: {{ ref('fix_version') }}, {{ ref('fixed_in') }}
-- depends_on: {{ ref('issue_link') }}, {{ ref('logged_time') }}
with expected (table_name) as (
    values
        ('issue'), ('project'), ('person'), ('fix_version'),
        ('fixed_in'), ('issue_link'), ('logged_time')
),

found as (
    select relname::text as table_name, relkind::text as kind
    from pg_class
    where relnamespace = 'model'::regnamespace
      and relkind in ('r', 'p', 'v', 'm', 'f')
)

select 'extra' as problem, table_name, kind from found
where table_name not in (select table_name from expected) or kind <> 'r'
union all
select 'missing', table_name, null from expected
where table_name not in (select table_name from found where kind = 'r')
