-- Q15 sources, step 1 of the known answer: the parent record, every direct sub-task
-- with its parent hop, and every positive time entry on any of them. Steps refer to
-- the Q15 reading note. A NULL cell is no ID (spec 0011 AC-12, amended 2026-11-12).
WITH family AS (
    -- step 1: the parent record, CLOUD-643 in CLOUD; it has no parent hop here
    SELECT
        p.issue_key,
        NULL::text AS parent
    FROM model.issue AS p
    WHERE p.issue_key = 'CLOUD-643'
        AND p.project_key = 'CLOUD'
    UNION
    -- step 2: every direct child and its parent hop (the child key, ruling B)
    SELECT
        c.issue_key,
        c.issue_key AS parent
    FROM model.issue AS c
    INNER JOIN model.issue AS p ON p.issue_key = c.parent_key
    WHERE p.issue_key = 'CLOUD-643'
        AND p.project_key = 'CLOUD'
)

SELECT DISTINCT
    f.issue_key AS issue,
    f.parent,
    t.history_id AS time_entry
FROM family AS f
-- step 3: each positive entry on the parent or a child; an outer join, so an issue
-- with none keeps one row with a NULL time_entry (ruling 4)
LEFT JOIN model.logged_time AS t
    ON t.issue_key = f.issue_key
    AND t.seconds > 0
ORDER BY issue, parent, time_entry
