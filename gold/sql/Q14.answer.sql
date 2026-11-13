-- Q14 answer: whether every direct sub-task of CLOV-538 is resolved, one row.
-- Steps refer to the Q14 reading note.
SELECT
    -- step 2: at least one direct sub-task exists; step 3: each one's resolution is
    -- present and not "" (bool_and of no rows is NULL, read as not established);
    -- step 4: the boolean
    count(c.issue_key) > 0
    AND coalesce(bool_and(coalesce(c.resolution_name, '') <> ''), FALSE) AS resolved
-- step 1: the parent record, CLOV-538 in CLOV
FROM model.issue AS p
-- step 2: every direct child, by the child's parent key, one hop only; an outer
-- join, so a parent with no child still gives one row and counts 0
LEFT JOIN model.issue AS c ON c.parent_key = p.issue_key
WHERE p.issue_key = 'CLOV-538'
    AND p.project_key = 'CLOV'
