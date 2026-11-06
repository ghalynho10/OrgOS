-- model.project (spec 0007): a copy of model_int.project.
select
    project_key,
    project_name,
    source_doc_id,
    load_id
from {{ ref('int_project') }}
