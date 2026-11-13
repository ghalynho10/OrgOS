-- Q14 sources, step 1 of the known answer: the parent record and every direct
-- sub-task with its parent hop, returned only when the answer is yes; a no has
-- empty sources under AC-32 (ruling 7). Steps refer to the Q14 reading note. A
-- NULL cell is no ID (spec 0011 AC-12, amended 2026-11-12).
WITH parent AS (
    -- step 1: the parent record, CLOV-538 in CLOV
    SELECT p.issue_key
    FROM model.issue AS p
    WHERE p.issue_key = 'CLOV-538'
        AND p.project_key = 'CLOV'
),

child AS (
    -- step 2: every direct child, by the child's parent key, one hop only
    SELECT
        c.issue_key,
        c.resolution_name
    FROM model.issue AS c
    INNER JOIN parent AS p ON p.issue_key = c.parent_key
),

answer AS (
    -- step 3 and step 4: the same boolean as the answer file
    SELECT
        count(*) > 0
        AND coalesce(bool_and(coalesce(c.resolution_name, '') <> ''), FALSE) AS yes
    FROM child AS c
)

-- the parent's own issue record
SELECT
    p.issue_key AS issue,
    NULL::text AS parent
FROM parent AS p
CROSS JOIN answer AS a
WHERE a.yes
UNION
-- every direct child, resolved or not: its issue record and its parent hop (the
-- child key, ruling 5)
SELECT
    c.issue_key AS issue,
    c.issue_key AS parent
FROM child AS c
CROSS JOIN answer AS a
WHERE a.yes
ORDER BY issue, parent
