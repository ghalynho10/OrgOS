-- Q20 sources, step 1 of the known answer: for every returned person, each qualifying
-- entry, its issue and that issue's project, and the person. The same conditions and
-- cut as the answer file, steps 1 to 5 of the Q20 reading note. The columns repeat
-- per row and are read as sets.
WITH entry AS (
    SELECT
        t.history_id,
        t.person_key,
        t.issue_key,
        t.seconds,
        i.project_key
    FROM model.logged_time AS t
    INNER JOIN model.issue AS i ON i.issue_key = t.issue_key
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND t.logged_at >= TIMESTAMPTZ '2009-07-01 00:00:00+00'
        AND t.logged_at < TIMESTAMPTZ '2009-10-01 00:00:00+00'
        AND t.seconds > 0
),

ranked AS (
    SELECT
        e.person_key,
        rank() OVER (ORDER BY sum(e.seconds) DESC) AS place
    FROM entry AS e
    GROUP BY e.person_key
    HAVING count(DISTINCT e.project_key) >= 2
)

-- only the returned people's entries (ruling 3); the projects are those of the
-- listed issues and entries (ruling 2)
SELECT DISTINCT
    e.issue_key AS issue,
    e.person_key AS person,
    e.project_key AS project,
    e.history_id AS time_entry
FROM entry AS e
INNER JOIN ranked AS r ON r.person_key = e.person_key
WHERE r.place <= 5
ORDER BY issue, person, project, time_entry
