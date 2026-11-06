-- model_int.has_parent (spec 0006 AC-12, AC-13): child issue to parent issue,
-- read from the child's fields.parent.key, kept only when the parent is a row
-- of issue (presence, never key prefix).
with child as (
    select
        key,
        fields -> 'parent' ->> 'key' as parent_key,
        mongo_id,
        load_id
    from {{ ref('stg_issue_doc') }}
    where fields -> 'parent' ->> 'key' is not null
)

select
    'has_parent:' || child.key as edge_id,
    child.key as src_id,
    child.parent_key as dst_id,
    'has_parent' as edge_type,
    child.mongo_id as source_doc_id,
    child.load_id
from child
inner join {{ ref('int_issue') }} as parent on child.parent_key = parent.issue_key
