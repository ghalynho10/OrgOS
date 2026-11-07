-- model_int.fixed_in (spec 0006): issue to each of its fix versions. One
-- version listed twice on one issue would repeat the edge_id and fail the key.
select
    'fixed_in:' || doc.key || ':' || (fix.value ->> 'id') as edge_id,
    doc.key as src_id,
    fix.value ->> 'id' as dst_id,
    'fixed_in' as edge_type,
    doc.mongo_id as source_doc_id,
    doc.load_id
from {{ ref('stg_issue_doc') }} as doc
cross join lateral {{ array_elements("doc.fields -> 'fixVersions'") }} as fix (value)
