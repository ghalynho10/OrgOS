-- Spec 0007 AC-4: in every model table, source_doc_id and load_id are the
-- last two columns, in that order. Lists each table whose last two differ.
-- depends_on: {{ ref('issue') }}, {{ ref('project') }}, {{ ref('person') }}
-- depends_on: {{ ref('fix_version') }}, {{ ref('fixed_in') }}
-- depends_on: {{ ref('issue_link') }}, {{ ref('logged_time') }}
with ranked as (
    select
        table_name::text as table_name,
        column_name::text as column_name,
        row_number() over (partition by table_name order by ordinal_position desc) as from_end
    from information_schema.columns
    where table_schema = 'model'
      and table_name in (
          'issue', 'project', 'person', 'fix_version', 'fixed_in', 'issue_link', 'logged_time'
      )
)

select table_name, string_agg(column_name, ', ' order by from_end desc) as last_two
from ranked
where from_end <= 2
group by table_name
having string_agg(column_name, ', ' order by from_end desc) <> 'source_doc_id, load_id'
