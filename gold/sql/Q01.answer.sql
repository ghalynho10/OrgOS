-- Q01 answer: per project, the issues created in Q1 2013 (UTC) that are
-- still unresolved and have no assignee. Steps refer to the Q01 reading note.
SELECT
    p.project_key,
    -- step 5: count never sum, so no match gives 0; DISTINCT counts issues, not join rows
    count(DISTINCT i.issue_key) AS issues
FROM model.project AS p
-- step 1 and the zeros: every project row stays, matched issues or not
LEFT JOIN model.issue AS i
    ON i.project_key = p.project_key
    -- step 2: half open UTC interval, written with an explicit offset
    AND i.created_at >= TIMESTAMPTZ '2013-01-01 00:00:00+00'
    AND i.created_at < TIMESTAMPTZ '2013-04-01 00:00:00+00'
    -- step 3: unresolved means missing, null or exactly ''
    AND (i.resolution_name IS NULL OR i.resolution_name = '')
    -- step 4: no assignee means missing, null or exactly ''
    AND (i.assignee_key IS NULL OR i.assignee_key = '')
-- step 1: the seven project population
WHERE p.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
GROUP BY p.project_key
ORDER BY p.project_key
