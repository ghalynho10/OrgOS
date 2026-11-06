-- model_int.issue (spec 0006): one row per collapsed corpus document. Every
-- instant is converted to UTC from its own offset (AC-18); no prose field and
-- no timespent (AC-21, AC-33).
select
    key as issue_key,
    {{ to_utc_instant("fields ->> 'created'") }} as created_at,
    {{ to_utc_instant("fields ->> 'resolutiondate'") }} as resolution_date,
    {{ due_to_utc_instant("fields ->> 'duedate'") }} as due_at,
    (fields ->> 'timeoriginalestimate')::bigint as time_original_estimate_s,
    fields -> 'resolution' ->> 'name' as resolution_name,
    fields -> 'issuetype' ->> 'name' as issue_type_name,
    fields -> 'priority' ->> 'name' as priority_name,
    fields -> 'status' ->> 'name' as status_name,
    mongo_id as source_doc_id,
    load_id
from {{ ref('stg_issue_doc') }}
