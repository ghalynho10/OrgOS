-- Spec 0007 AC-17: every model table was analyzed by this build, after its
-- swap, so the planner has fresh statistics before any query. Autoanalyze
-- does not count. Lists each table whose last_analyze is missing or older
-- than the start of this dbt run.
-- depends_on: {{ ref('issue') }}, {{ ref('project') }}, {{ ref('person') }}
-- depends_on: {{ ref('fix_version') }}, {{ ref('fixed_in') }}
-- depends_on: {{ ref('issue_link') }}, {{ ref('logged_time') }}
with expected (table_name) as (
    values
        ('issue'), ('project'), ('person'), ('fix_version'),
        ('fixed_in'), ('issue_link'), ('logged_time')
)

select expected.table_name, stats.last_analyze
from expected
left join pg_stat_user_tables as stats
    on stats.schemaname = 'model' and stats.relname = expected.table_name
where stats.last_analyze is null
   or stats.last_analyze < '{{ run_started_at.isoformat() }}'::timestamptz
