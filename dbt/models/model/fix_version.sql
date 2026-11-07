-- model.fix_version (spec 0007): a copy of model_int.fix_version.
select
    version_id,
    version_name,
    is_released,
    source_doc_id,
    load_id
from {{ ref('int_fix_version') }}
