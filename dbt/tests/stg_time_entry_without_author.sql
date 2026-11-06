-- Spec 0006 AC-16: a time entry with no author stops the build and names the
-- issue; it is never dropped silently.
select distinct issue_key, history_id
from {{ ref('stg_time_history') }}
where author_key is null
