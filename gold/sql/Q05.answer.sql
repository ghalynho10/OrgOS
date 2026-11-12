-- Q05 answer: per time entry author, the positive seconds they logged on issues
-- assigned to someone else. Steps refer to the Q05 reading note.
SELECT
    t.person_key,
    -- step 4: the retained amounts summed per author, in seconds
    sum(t.seconds) AS seconds
FROM model.logged_time AS t
-- each entry joins its one issue, so no entry is repeated
INNER JOIN model.issue AS i ON i.issue_key = t.issue_key
-- step 1: the seven project population
WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
    -- step 2: drop unassigned issues (missing, null or exactly ''); the flagged bot
    -- set is empty for this corpus (spec 0006 AC-17), so no assignee is a bot
    AND i.assignee_key IS NOT NULL AND i.assignee_key <> ''
    -- step 3: positive entries only (spec 0006 AC-15), by an author who is not
    -- the issue's current assignee
    AND t.seconds > 0
    AND t.person_key <> i.assignee_key
GROUP BY t.person_key
ORDER BY t.person_key
