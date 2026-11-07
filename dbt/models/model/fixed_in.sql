-- model.fixed_in (spec 0007): model_int.fixed_in with plain names (AC-5);
-- edge_id and edge_type are not served, each is rebuilt from these columns.
select
    src_id as issue_key,
    dst_id as version_id,
    source_doc_id,
    load_id
from {{ ref('int_fixed_in') }}
