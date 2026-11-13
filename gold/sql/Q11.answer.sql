-- Q11 answer: every person in a shared issue connection and every project of such
-- an issue, one set. Steps refer to the Q11 reading note.
WITH population AS (
    -- step 1: issues in the seven projects
    SELECT
        i.issue_key,
        i.project_key,
        i.assignee_key,
        i.reporter_key,
        i.creator_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
),

holds_role AS (
    -- step 2: assignee, reporter and creator keys, a NULL or "" key is none; the
    -- flagged bot set is empty for this corpus (spec 0006 AC-17)
    SELECT
        p.issue_key,
        p.assignee_key AS person_key
    FROM population AS p
    WHERE coalesce(p.assignee_key, '') <> ''
    UNION
    SELECT
        p.issue_key,
        p.reporter_key
    FROM population AS p
    WHERE coalesce(p.reporter_key, '') <> ''
    UNION
    SELECT
        p.issue_key,
        p.creator_key
    FROM population AS p
    WHERE coalesce(p.creator_key, '') <> ''
    UNION
    -- step 3: authors of positive time entries on the issue
    SELECT
        t.issue_key,
        t.person_key
    FROM model.logged_time AS t
    INNER JOIN population AS p ON p.issue_key = t.issue_key
    WHERE t.seconds > 0
        AND coalesce(t.person_key, '') <> ''
),

shared AS (
    -- step 4: an issue connects people when two distinct people hold a role on it
    SELECT h.issue_key
    FROM holds_role AS h
    GROUP BY h.issue_key
    HAVING count(DISTINCT h.person_key) >= 2
)

-- step 5: the distinct people on those issues and their projects, in one set
SELECT DISTINCT
    'person' AS kind,
    h.person_key AS id
FROM holds_role AS h
INNER JOIN shared AS s ON s.issue_key = h.issue_key
UNION
SELECT DISTINCT
    'project' AS kind,
    p.project_key AS id
FROM population AS p
INNER JOIN shared AS s ON s.issue_key = p.issue_key
ORDER BY kind, id
