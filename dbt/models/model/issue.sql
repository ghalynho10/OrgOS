-- model.issue (spec 0007): model_int.issue with its five to one relationships
-- folded in as key columns (AC-2). Left joins, so no edge row means null (AC-14).
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
    assigned_to.dst_id as assignee_key,
    reported_by.dst_id as reporter_key,
    created_by.dst_id as creator_key,
    has_parent.dst_id as parent_key,
    issue.source_doc_id,
    issue.load_id
from {{ ref('int_issue') }} as issue
left join {{ ref('int_in_project') }} as in_project on issue.issue_key = in_project.src_id
left join {{ ref('int_assigned_to') }} as assigned_to on issue.issue_key = assigned_to.src_id
left join {{ ref('int_reported_by') }} as reported_by on issue.issue_key = reported_by.src_id
left join {{ ref('int_created_by') }} as created_by on issue.issue_key = created_by.src_id
left join {{ ref('int_has_parent') }} as has_parent on issue.issue_key = has_parent.src_id
