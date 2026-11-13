-- Q18 answer: the people who are reporter or assignee in at least two of the seven
-- projects, each with their count of distinct projects. Steps refer to the Q18
-- reading note.
WITH role AS (
    -- step 1: issues in the seven project population
    -- step 2: the current reporter key, a NULL or "" key is none; the flagged bot
    -- set is empty for this corpus (spec 0006 AC-17)
    SELECT
        i.issue_key,
        i.project_key,
        i.reporter_key AS person_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND coalesce(i.reporter_key, '') <> ''
    UNION
    -- the current assignee key, the same rule
    SELECT
        i.issue_key,
        i.project_key,
        i.assignee_key AS person_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND coalesce(i.assignee_key, '') <> ''
)

SELECT
    r.person_key,
    -- step 3 and step 5: distinct projects per person across both roles, so many
    -- issues or both roles in one project count one project
    count(DISTINCT r.project_key) AS projects
FROM role AS r
GROUP BY r.person_key
-- step 4: at least two distinct projects
HAVING count(DISTINCT r.project_key) >= 2
ORDER BY r.person_key
