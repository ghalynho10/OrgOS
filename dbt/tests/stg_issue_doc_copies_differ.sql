-- Spec 0009 AC-8: two copies of one issue key that differ in any compared
-- column fail the build and name the key. Compared: every raw column except
-- mongo_id and the dlt columns. The ref attaches this test to stg_issue_doc, so
-- every model built on it is skipped when it fails.
-- depends_on: {{ ref('stg_issue_doc') }}
{%- set compared = [] -%}
{%- if execute -%}
    {%- for column in adapter.get_columns_in_relation(source('raw', 'jira_issue')) -%}
        {%- if column.name != 'mongo_id' and not column.name.startswith('_dlt_') -%}
            {%- do compared.append(adapter.quote(column.name)) -%}
        {%- endif -%}
    {%- endfor -%}
{%- endif %}

select
    key,
    count(*) as copies,
    count(distinct ({{ compared | join(', ') if compared else 'key' }})) as distinct_versions
from {{ source('raw', 'jira_issue') }}
group by key
having count(distinct ({{ compared | join(', ') if compared else 'key' }})) > 1
