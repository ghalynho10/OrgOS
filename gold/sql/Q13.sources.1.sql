-- Q13 sources, step 1 of the known answer: the parent record, every direct sub-task
-- with its parent hop, and every positive time entry on a sub-task. Steps refer to
-- the Q13 reading note. A NULL cell is no ID (spec 0011 AC-12, amended 2026-11-12).
WITH child AS (
    SELECT c.issue_key
    -- step 2: every direct child of CLOV-108, by the child's parent key
    FROM model.issue AS c
    -- step 1: the parent record, CLOV-108 in CLOV
    INNER JOIN model.issue AS p ON p.issue_key = c.parent_key
    WHERE p.issue_key = 'CLOV-108'
        AND p.project_key = 'CLOV'
)

-- step 1: the parent's own issue record
SELECT
    p.issue_key AS issue,
    NULL::text AS parent,
    NULL::text AS time_entry
FROM model.issue AS p
WHERE p.issue_key = 'CLOV-108'
    AND p.project_key = 'CLOV'
UNION
-- step 2 and step 3: each sub-task's issue record and its parent hop (the child
-- key, ruling 5); step 4: each positive entry on it, an outer join so a sub-task
-- with none keeps a NULL time_entry. The flagged bot set is empty for this corpus
-- (spec 0006 AC-17), so no bot clause is written.
SELECT
    c.issue_key AS issue,
    c.issue_key AS parent,
    t.history_id AS time_entry
FROM child AS c
LEFT JOIN model.logged_time AS t
    ON t.issue_key = c.issue_key
    AND t.seconds > 0
ORDER BY issue, parent, time_entry
