-- Q16 stage 1: the store's own count of open Highest priority corpus bugs (step 1).
-- Cumulative: the condition of step 1 of the Q16 reading note, nothing else.
SELECT count(DISTINCT b.issue_key) AS issues
FROM model.issue AS b
WHERE b.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
    AND b.issue_type_name = 'Bug'
    AND b.priority_name = 'Highest'
    AND (b.resolution_name IS NULL OR b.resolution_name = '')
