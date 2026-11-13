-- Q13 answer: the direct sub-tasks of CLOV-108, by key. Steps refer to the Q13
-- reading note. Assignee, status and hours are display values, not stored in the
-- answer (ruling 5 and the 1d793bd commit body).
SELECT c.issue_key
-- step 2: every direct child, by the child's parent key, one hop only; no filter
-- on assignee or time, so a sub-task with neither is kept
FROM model.issue AS c
-- step 1: the parent record, CLOV-108 in CLOV
INNER JOIN model.issue AS p ON p.issue_key = c.parent_key
WHERE p.issue_key = 'CLOV-108'
    AND p.project_key = 'CLOV'
ORDER BY c.issue_key
