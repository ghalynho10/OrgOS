-- model.issue_link (spec 0007): model_int.issue_link with plain names (AC-5).
-- A row reads "from_issue_key outward_label to_issue_key" (AC-6).
select
    link_id,
    src_id as from_issue_key,
    dst_id as to_issue_key,
    link_type_name,
    outward_label,
    inward_label,
    source_doc_id,
    load_id
from {{ ref('int_issue_link') }}
