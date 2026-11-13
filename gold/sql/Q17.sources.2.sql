-- Q17 sources, step 2 of the known answer: the shared person hop, every returned
-- person and each of their contributing entries on their qualifying issues. The same
-- conditions as the answer file, steps 1 to 4 of the Q17 reading note.
WITH corpus AS (
    SELECT
        i.issue_key,
        i.project_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
),

cross_link AS (
    SELECT
        l.link_id,
        l.from_issue_key AS a_key,
        l.to_issue_key AS b_key
    FROM model.issue_link AS l
    INNER JOIN corpus AS a ON a.issue_key = l.from_issue_key
    INNER JOIN corpus AS b ON b.issue_key = l.to_issue_key
    WHERE a.project_key <> b.project_key
),

entry AS (
    SELECT
        t.history_id,
        t.person_key,
        t.issue_key
    FROM model.logged_time AS t
    INNER JOIN corpus AS c ON c.issue_key = t.issue_key
    WHERE t.seconds > 0
),

logger AS (
    SELECT DISTINCT
        e.person_key,
        e.issue_key
    FROM entry AS e
),

pair AS (
    SELECT
        x.a_key,
        x.b_key,
        la.person_key
    FROM cross_link AS x
    INNER JOIN logger AS la ON la.issue_key = x.a_key
    INNER JOIN logger AS lb
        ON lb.issue_key = x.b_key
        AND lb.person_key = la.person_key
),

person_issue AS (
    SELECT
        p.person_key,
        p.a_key AS issue_key
    FROM pair AS p
    UNION
    SELECT
        p.person_key,
        p.b_key AS issue_key
    FROM pair AS p
)

SELECT DISTINCT
    e.person_key AS person,
    e.history_id AS time_entry
FROM person_issue AS pi
INNER JOIN entry AS e
    ON e.person_key = pi.person_key
    AND e.issue_key = pi.issue_key
ORDER BY person, time_entry
