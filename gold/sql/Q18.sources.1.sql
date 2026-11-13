-- Q18 sources, step 1 of the known answer: for every returned person, each issue
-- where they hold a qualifying role, the person, and the project of that issue. The
-- same conditions as the answer file, steps 1 to 4 of the Q18 reading note. The
-- columns repeat per row and are read as sets.
WITH role AS (
    SELECT
        i.issue_key,
        i.project_key,
        i.reporter_key AS person_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND coalesce(i.reporter_key, '') <> ''
    UNION
    SELECT
        i.issue_key,
        i.project_key,
        i.assignee_key AS person_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND coalesce(i.assignee_key, '') <> ''
),

returned AS (
    SELECT r.person_key
    FROM role AS r
    GROUP BY r.person_key
    HAVING count(DISTINCT r.project_key) >= 2
)

-- only the returned people's role rows, so a reporter or assignee who is not
-- returned is never listed as a person (ruling 3)
SELECT DISTINCT
    r.issue_key AS issue,
    r.person_key AS person,
    r.project_key AS project
FROM role AS r
INNER JOIN returned AS p ON p.person_key = r.person_key
ORDER BY issue, person, project
