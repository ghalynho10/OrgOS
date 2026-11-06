-- One row per fields.issuelinks[] entry per holding issue (spec 0006 Link
-- direction). An entry with outwardIssue X on issue H reads H to X; one with
-- inwardIssue X reads X to H. Both views of a link give the same src and dst,
-- which the test on this view proves before issue_link builds (AC-10).
with entry as (
    select
        doc.key as holder_key,
        doc.mongo_id as holder_mongo_id,
        doc.load_id,
        link.ordinality as entry_number,
        link.value ->> 'id' as link_id,
        link.value -> 'type' ->> 'name' as link_type_name,
        link.value -> 'type' ->> 'outward' as outward_label,
        link.value -> 'type' ->> 'inward' as inward_label,
        link.value -> 'outwardIssue' ->> 'key' as outward_issue_key,
        link.value -> 'inwardIssue' ->> 'key' as inward_issue_key
    from {{ ref('stg_issue_doc') }} as doc
    cross join lateral {{ array_elements("doc.fields -> 'issuelinks'") }}
        with ordinality as link (value, ordinality)
)

select
    *,
    case when outward_issue_key is not null then holder_key else inward_issue_key end as src_id,
    case when outward_issue_key is not null then outward_issue_key else holder_key end as dst_id
from entry
