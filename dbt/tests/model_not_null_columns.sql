-- Spec 0007 AC-14: `not null` sits exactly on the complete list of AC-14 and
-- nowhere else in model, even where model_int is stricter. Lists a column that
-- should be `not null` and is not, and one that is and should not be.
-- depends_on: {{ ref('issue') }}, {{ ref('project') }}, {{ ref('person') }}
-- depends_on: {{ ref('fix_version') }}, {{ ref('fixed_in') }}
-- depends_on: {{ ref('issue_link') }}, {{ ref('logged_time') }}
with expected (table_name, column_name) as (
    values
        ('issue', 'issue_key'), ('issue', 'project_key'),
        ('issue', 'source_doc_id'), ('issue', 'load_id'),
        ('project', 'project_key'), ('project', 'source_doc_id'), ('project', 'load_id'),
        ('person', 'person_key'), ('person', 'source_doc_id'), ('person', 'load_id'),
        ('fix_version', 'version_id'), ('fix_version', 'source_doc_id'),
        ('fix_version', 'load_id'),
        ('fixed_in', 'issue_key'), ('fixed_in', 'version_id'),
        ('fixed_in', 'source_doc_id'), ('fixed_in', 'load_id'),
        ('issue_link', 'link_id'), ('issue_link', 'from_issue_key'),
        ('issue_link', 'to_issue_key'), ('issue_link', 'source_doc_id'),
        ('issue_link', 'load_id'),
        ('logged_time', 'history_id'), ('logged_time', 'person_key'),
        ('logged_time', 'issue_key'), ('logged_time', 'seconds'),
        ('logged_time', 'logged_at'), ('logged_time', 'source_doc_id'),
        ('logged_time', 'load_id')
),

found as (
    select table_name::text as table_name, column_name::text as column_name
    from information_schema.columns
    where table_schema = 'model'
      and is_nullable = 'NO'
      and table_name in (select distinct table_name from expected)
)

select 'should allow null' as problem, table_name, column_name from found
except
select 'should allow null', table_name, column_name from expected
union all
(
    select 'should be not null', table_name, column_name from expected
    except
    select 'should be not null', table_name, column_name from found
)
