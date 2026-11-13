-- Q12 answer: parent issues in CLOUD or CLOV whose direct sub-tasks have at least
-- two distinct assignees. Steps refer to the Q12 reading note.
WITH contributing AS (
    SELECT
        c.issue_key AS child_key,
        c.parent_key,
        c.assignee_key
    -- step 2: direct sub-tasks, by the child's parent key, one hop only
    FROM model.issue AS c
    -- step 1: their parents in CLOUD or CLOV
    INNER JOIN model.issue AS p ON p.issue_key = c.parent_key
    WHERE p.project_key IN ('CLOUD', 'CLOV')
        -- step 3: sub-tasks in CLOUD or CLOV with an assignee; a NULL or "" key is
        -- none, and the flagged bot set is empty for this corpus (spec 0006 AC-17)
        AND c.project_key IN ('CLOUD', 'CLOV')
        AND coalesce(c.assignee_key, '') <> ''
)

-- step 4: distinct assignee keys per parent; step 5: parents with at least two
SELECT q.parent_key AS issue_key
FROM contributing AS q
GROUP BY q.parent_key
HAVING count(DISTINCT q.assignee_key) >= 2
ORDER BY issue_key
