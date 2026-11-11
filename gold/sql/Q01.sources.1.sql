-- Q01 sources, step 1 of the known answer: every qualifying issue, one row each.
-- The same four conditions as the answer file, steps 1 to 4 of the reading note.
SELECT DISTINCT i.issue_key AS issue
FROM model.issue AS i
WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
    AND i.created_at >= TIMESTAMPTZ '2013-01-01 00:00:00+00'
    AND i.created_at < TIMESTAMPTZ '2013-04-01 00:00:00+00'
    AND (i.resolution_name IS NULL OR i.resolution_name = '')
    AND (i.assignee_key IS NULL OR i.assignee_key = '')
ORDER BY i.issue_key
