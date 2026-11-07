-- model.logged_time (spec 0007): model_int.logged_time with plain names (AC-5),
-- every entry kept with its sign; a query applies seconds > 0 itself.
select
    history_id,
    src_id as person_key,
    dst_id as issue_key,
    seconds,
    logged_at,
    source_doc_id,
    load_id
from {{ ref('int_logged_time') }}
