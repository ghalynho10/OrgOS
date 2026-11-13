-- Q17 answer: per person, the positive seconds they logged on the issues of their
-- qualifying pairs (two directly linked corpus issues in different projects, with a
-- positive entry by that person on each). Steps refer to the Q17 reading note.
WITH corpus AS (
    -- the population: issues in the seven projects, with their current project
    SELECT
        i.issue_key,
        i.project_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
),

cross_link AS (
    -- step 1: directly linked corpus issues in different projects; any link type,
    -- direction ignored, so each link is read once with its two ends
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
    -- positive entries on corpus issues; the flagged bot set is empty for this
    -- corpus (spec 0006 AC-17) and every entry has an author (spec 0006 AC-16)
    SELECT
        t.history_id,
        t.person_key,
        t.issue_key,
        t.seconds
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
    -- step 2: the same author has a positive entry on both ends
    SELECT
        x.link_id,
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
    -- step 3: the distinct issues in at least one qualifying pair, per author;
    -- UNION keeps each (person, issue) once, however many pairs it sits in
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

SELECT
    pi.person_key,
    -- step 4: the author's positive entries on those issues, each entry once
    sum(e.seconds) AS seconds
FROM person_issue AS pi
INNER JOIN entry AS e
    ON e.person_key = pi.person_key
    AND e.issue_key = pi.issue_key
GROUP BY pi.person_key
ORDER BY pi.person_key
