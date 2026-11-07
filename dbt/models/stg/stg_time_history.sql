-- One row per timespent item of fieldtype jira in a changelog history of a
-- collapsed document (spec 0004 AC-7, spec 0006 AC-14). One row per item, not
-- per history, so a history holding two items keeps both raw values and
-- logged_time sums them. Every value stays raw text; the validity tests in
-- dbt/tests check the amounts, authors and offsets before logged_time builds.
select
    doc.key as issue_key,
    doc.mongo_id,
    doc.load_id,
    history.value ->> 'id' as history_id,
    history.value -> 'author' ->> 'key' as author_key,
    history.value ->> 'created' as created,
    item.ordinality as item_number,
    item.value ->> 'from' as from_value,
    item.value ->> 'to' as to_value,
    item.value ->> 'fromString' as from_string,
    item.value ->> 'toString' as to_string
from {{ ref('stg_issue_doc') }} as doc
cross join lateral {{ array_elements("doc.changelog -> 'histories'") }} as history (value)
cross join lateral {{ array_elements("history.value -> 'items'") }}
    with ordinality as item (value, ordinality)
where
    item.value ->> 'field' = 'timespent'
    and item.value ->> 'fieldtype' = 'jira'
