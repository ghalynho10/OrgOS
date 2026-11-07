-- Spec 0007 AC-16: besides the primary key indexes, model has exactly the 10
-- btree indexes on key columns, and none on a value column. Lists a missing
-- index and any extra one, with its columns and access method.
-- depends_on: {{ ref('issue') }}, {{ ref('project') }}, {{ ref('person') }}
-- depends_on: {{ ref('fix_version') }}, {{ ref('fixed_in') }}
-- depends_on: {{ ref('issue_link') }}, {{ ref('logged_time') }}
with expected (table_name, index_columns, method) as (
    values
        ('issue', 'project_key', 'btree'),
        ('issue', 'assignee_key', 'btree'),
        ('issue', 'reporter_key', 'btree'),
        ('issue', 'creator_key', 'btree'),
        ('issue', 'parent_key', 'btree'),
        ('issue_link', 'from_issue_key', 'btree'),
        ('issue_link', 'to_issue_key', 'btree'),
        ('logged_time', 'person_key', 'btree'),
        ('logged_time', 'issue_key', 'btree'),
        ('fixed_in', 'version_id', 'btree')
),

found as (
    select
        tbl.relname::text as table_name,
        (
            select string_agg(att.attname, ', ' order by key.ord)
            from unnest(idx.indkey::smallint []) with ordinality as key (attnum, ord)
            inner join pg_attribute as att
                on att.attrelid = idx.indrelid and att.attnum = key.attnum
        ) as index_columns,
        am.amname::text as method
    from pg_index as idx
    inner join pg_class as tbl on tbl.oid = idx.indrelid
    inner join pg_class as ix on ix.oid = idx.indexrelid
    inner join pg_am as am on am.oid = ix.relam
    where tbl.relnamespace = 'model'::regnamespace
      and not idx.indisprimary
      and tbl.relname in (
          'issue', 'project', 'person', 'fix_version', 'fixed_in', 'issue_link', 'logged_time'
      )
)

select 'missing' as problem, * from (
    select * from expected
    except all
    select * from found
) as missing
union all
select 'extra', * from (
    select * from found
    except all
    select * from expected
) as extra
