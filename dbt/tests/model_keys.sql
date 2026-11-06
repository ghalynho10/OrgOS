-- Spec 0007 AC-11, AC-12, AC-15: model holds exactly the 7 primary keys and
-- the 10 foreign keys, read from pg_constraint by table and columns, never by
-- name (the automatic names carry __dbt_tmp). Rebuilding one referenced table
-- alone drops the foreign keys that point at it, and this lists them as
-- missing. It also proves no foreign key on issue.parent_key (AC-13). Depends
-- on all 7, so dbt build runs it last. Not null constraints (contype 'n' in
-- Postgres 18) are the subject of model_not_null_columns.
-- depends_on: {{ ref('issue') }}, {{ ref('project') }}, {{ ref('person') }}
-- depends_on: {{ ref('fix_version') }}, {{ ref('fixed_in') }}
-- depends_on: {{ ref('issue_link') }}, {{ ref('logged_time') }}
with expected (table_name, kind, key_columns, ref_table, ref_columns) as (
    values
        ('issue', 'p', 'issue_key', null, null),
        ('project', 'p', 'project_key', null, null),
        ('person', 'p', 'person_key', null, null),
        ('fix_version', 'p', 'version_id', null, null),
        ('fixed_in', 'p', 'issue_key, version_id', null, null),
        ('issue_link', 'p', 'link_id', null, null),
        ('logged_time', 'p', 'history_id', null, null),
        ('issue', 'f', 'project_key', 'model.project', 'project_key'),
        ('issue', 'f', 'assignee_key', 'model.person', 'person_key'),
        ('issue', 'f', 'reporter_key', 'model.person', 'person_key'),
        ('issue', 'f', 'creator_key', 'model.person', 'person_key'),
        ('logged_time', 'f', 'person_key', 'model.person', 'person_key'),
        ('logged_time', 'f', 'issue_key', 'model.issue', 'issue_key'),
        ('fixed_in', 'f', 'issue_key', 'model.issue', 'issue_key'),
        ('fixed_in', 'f', 'version_id', 'model.fix_version', 'version_id'),
        ('issue_link', 'f', 'from_issue_key', 'model.issue', 'issue_key'),
        ('issue_link', 'f', 'to_issue_key', 'model.issue', 'issue_key')
),

found as (
    select
        tbl.relname::text as table_name,
        con.contype::text as kind,
        (
            select string_agg(att.attname, ', ' order by key.ord)
            from unnest(con.conkey) with ordinality as key (attnum, ord)
            inner join pg_attribute as att
                on att.attrelid = con.conrelid and att.attnum = key.attnum
        ) as key_columns,
        case when con.contype = 'f' then ref_ns.nspname || '.' || ref_tbl.relname end as ref_table,
        (
            select string_agg(att.attname, ', ' order by key.ord)
            from unnest(con.confkey) with ordinality as key (attnum, ord)
            inner join pg_attribute as att
                on att.attrelid = con.confrelid and att.attnum = key.attnum
        ) as ref_columns
    from pg_constraint as con
    inner join pg_class as tbl on tbl.oid = con.conrelid
    left join pg_class as ref_tbl on ref_tbl.oid = con.confrelid
    left join pg_namespace as ref_ns on ref_ns.oid = ref_tbl.relnamespace
    where con.connamespace = 'model'::regnamespace
      and con.contype <> 'n'
      and tbl.relname in (select distinct table_name from expected)
)

select 'missing' as problem, * from (
    select * from expected
    except
    select * from found
) as missing
union all
select 'extra', * from (
    select * from found
    except
    select * from expected
) as extra
