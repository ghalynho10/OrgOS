-- model_int.issue_link (spec 0006 AC-9 to AC-13): one row per link ID whose
-- two ends are both rows of issue (presence, never key prefix). src_id is the
-- outward side, so a row reads "src outward_label dst". Both views agree (the
-- test on stg_link_entry), so the lowest holding mongo_id only picks
-- source_doc_id (AC-31).
with kept as (
    select distinct on (entry.link_id) entry.*
    from {{ ref('stg_link_entry') }} as entry
    inner join {{ ref('int_issue') }} as src on entry.src_id = src.issue_key
    inner join {{ ref('int_issue') }} as dst on entry.dst_id = dst.issue_key
    order by entry.link_id, entry.holder_mongo_id collate "C"
)

select
    'issue_link:' || link_id as edge_id,
    src_id,
    dst_id,
    link_type_name as edge_type,
    link_id,
    link_type_name,
    outward_label,
    inward_label,
    holder_mongo_id as source_doc_id,
    load_id
from kept
