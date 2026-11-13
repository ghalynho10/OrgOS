-- Q11 sources, step 1 of the known answer: every shared issue, and every positive
-- time entry on it, one row per entry. Steps refer to the Q11 reading note. An
-- issue with no positive entry keeps one row whose time_entry cell is NULL, which
-- is no ID (spec 0011 AC-12, amended 2026-11-12).
WITH population AS (
    -- step 1: issues in the seven projects
    SELECT
        i.issue_key,
        i.assignee_key,
        i.reporter_key,
        i.creator_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
),

holds_role AS (
    -- step 2: assignee, reporter and creator keys, a NULL or "" key is none
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

SELECT DISTINCT
    s.issue_key AS issue,
    t.history_id AS time_entry
FROM shared AS s
-- step 3: every positive entry that gives a logging role on a shared issue; an
-- outer join, so a shared issue with no entry stays in the issue column
LEFT JOIN model.logged_time AS t
    ON t.issue_key = s.issue_key
    AND t.seconds > 0
    AND coalesce(t.person_key, '') <> ''
ORDER BY issue, time_entry
