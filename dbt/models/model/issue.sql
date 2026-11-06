-- model.issue (spec 0007): model_int.issue with its project folded in as a
-- column. Milestone 1 of spec 0009 carries only the foreign keys whose target
-- tables exist; the person and parent columns follow with model.person.
select
    issue.issue_key,
    issue.created_at,
    issue.resolution_date,
    issue.due_at,
    issue.time_original_estimate_s,
    issue.resolution_name,
    issue.issue_type_name,
    issue.priority_name,
    issue.status_name,
    in_project.dst_id as project_key,
    issue.source_doc_id,
    issue.load_id
from {{ ref('int_issue') }} as issue
left join {{ ref('int_in_project') }} as in_project on issue.issue_key = in_project.src_id
