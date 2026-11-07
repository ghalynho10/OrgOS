-- model_int.in_project (spec 0006 AC-7): every issue to its current project.
select
    'in_project:' || key as edge_id,
    key as src_id,
    fields -> 'project' ->> 'key' as dst_id,
    'in_project' as edge_type,
    mongo_id as source_doc_id,
    load_id
from {{ ref('stg_issue_doc') }}
