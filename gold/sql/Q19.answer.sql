-- Q19 answer: the projects with the most distinct issues directly linked to an issue
-- in another corpus project, top five and every project tied with the fifth. Steps
-- refer to the Q19 reading note.
WITH corpus AS (
    -- step 1: corpus issues and their current project
    SELECT
        i.issue_key,
        i.project_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
),

cross_link AS (
    -- step 2: direct links of any type whose two ends are corpus issues in different
    -- projects; link_id is the table's key, so each link is one row
    SELECT
        l.link_id,
        a.issue_key AS a_key,
        a.project_key AS a_project,
        b.issue_key AS b_key,
        b.project_key AS b_project
    FROM model.issue_link AS l
    INNER JOIN corpus AS a ON a.issue_key = l.from_issue_key
    INNER JOIN corpus AS b ON b.issue_key = l.to_issue_key
    WHERE a.project_key <> b.project_key
),

end_point AS (
    -- direction is ignored: each end of each link, in its own project
    SELECT
        x.a_key AS issue_key,
        x.a_project AS project_key
    FROM cross_link AS x
    UNION ALL
    SELECT
        x.b_key AS issue_key,
        x.b_project AS project_key
    FROM cross_link AS x
),

counted AS (
    -- step 3 and step 4: distinct qualifying issues per project, each issue once in
    -- its own project however many links or partner projects it has; only projects
    -- with at least one such issue form a group
    SELECT
        e.project_key,
        count(DISTINCT e.issue_key) AS issues
    FROM end_point AS e
    GROUP BY e.project_key
),

ranked AS (
    -- step 4 and step 5: rank highest first; tied counts share a rank, so rank <= 5
    -- keeps the top five and every project tied with the fifth, or all when fewer
    SELECT
        c.project_key,
        c.issues,
        rank() OVER (ORDER BY c.issues DESC) AS place
    FROM counted AS c
)

SELECT
    r.project_key,
    r.issues
FROM ranked AS r
WHERE r.place <= 5
ORDER BY r.issues DESC, r.project_key
