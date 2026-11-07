-- model.person (spec 0007): a copy of model_int.person.
select
    person_key,
    login_name,
    is_active,
    source_doc_id,
    load_id
from {{ ref('int_person') }}
