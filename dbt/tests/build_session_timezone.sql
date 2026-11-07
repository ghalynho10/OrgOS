-- Spec 0009 AC-14: every build runs in the session zone America/St_Johns
-- (orgos.config.DBT_TIMEZONE, passed as PGTZ), a zone with a half hour offset,
-- so a timestamp read in the session zone by mistake cannot pass the tests.
-- A dbt run without the helper (no PGTZ) fails here even on a UTC server. The
-- ref makes it run right after stg_issue_doc and skip everything built on it.
-- depends_on: {{ ref('stg_issue_doc') }}
select current_setting('timezone') as session_timezone
where current_setting('timezone') <> 'America/St_Johns'
