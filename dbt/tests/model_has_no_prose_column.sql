-- Spec 0007 AC-24: no prose field, no timespent and no displayName in model
-- (spec 0006 AC-21 and AC-33 carry through). Lists any column whose name
-- looks like one.
-- depends_on: {{ ref('issue') }}, {{ ref('project') }}, {{ ref('person') }}
-- depends_on: {{ ref('fix_version') }}, {{ ref('fixed_in') }}
-- depends_on: {{ ref('issue_link') }}, {{ ref('logged_time') }}
select table_name, column_name
from information_schema.columns
where table_schema = 'model'
  and column_name ~ 'timespent|description|summary|comment|environment|display'
