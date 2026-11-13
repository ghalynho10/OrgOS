-- Q12 sources, step 1 of the known answer: each qualifying parent, and every
-- contributing sub-task with its parent hop and assignee. Steps refer to the Q12
-- reading note. A parent's own row has NULL parent and person cells, which are no
-- ID (spec 0011 AC-12, amended 2026-11-12).
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
        -- step 3: sub-tasks in CLOUD or CLOV with an assignee
        AND c.project_key IN ('CLOUD', 'CLOV')
        AND coalesce(c.assignee_key, '') <> ''
),

qualifying AS (
    -- step 4 and step 5: parents with at least two distinct assignee keys
    SELECT q.parent_key
    FROM contributing AS q
    GROUP BY q.parent_key
    HAVING count(DISTINCT q.assignee_key) >= 2
)

-- the parent's own issue record
SELECT
    f.parent_key AS issue,
    NULL::text AS parent,
    NULL::text AS person
FROM qualifying AS f
UNION
-- each contributing sub-task: its issue record, its parent hop (the child key,
-- ruling 5) and its assignee
SELECT
    c.child_key AS issue,
    c.child_key AS parent,
    c.assignee_key AS person
FROM contributing AS c
INNER JOIN qualifying AS f ON f.parent_key = c.parent_key
ORDER BY issue, parent, person
