-- Spec 0006 AC-7: every issue has exactly one in_project row. `unique` on
-- in_project.src_id proves at most one; this proves at least one.
select issue.issue_key
from {{ ref('int_issue') }} as issue
left join {{ ref('int_in_project') }} as in_project on issue.issue_key = in_project.src_id
where in_project.src_id is null
