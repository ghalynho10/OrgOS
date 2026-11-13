-- Q19 sources, step 1 of the known answer: for every returned project, the project,
-- every counted issue, every qualifying link supporting it and the issue at the other
-- end. The same conditions and cut as the answer file, steps 1 to 5 of the Q19
-- reading note. The columns repeat per row and are read as sets.
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
    -- each end of each link, in its own project, with the link and the other end
    SELECT
        x.link_id,
        x.a_key AS issue_key,
        x.a_project AS project_key,
        x.b_key AS other_key
    FROM cross_link AS x
    UNION ALL
    SELECT
        x.link_id,
        x.b_key AS issue_key,
        x.b_project AS project_key,
        x.a_key AS other_key
    FROM cross_link AS x
),

ranked AS (
    SELECT
        e.project_key,
        rank() OVER (ORDER BY count(DISTINCT e.issue_key) DESC) AS place
    FROM end_point AS e
    GROUP BY e.project_key
),

supporting AS (
    -- the counted issues of the returned projects only, with their links
    SELECT
        e.link_id,
        e.issue_key,
        e.project_key,
        e.other_key
    FROM end_point AS e
    INNER JOIN ranked AS r ON r.project_key = e.project_key
    WHERE r.place <= 5
)

-- the returned project, each counted issue and its supporting link
SELECT
    s.project_key AS project,
    s.issue_key AS issue,
    s.link_id AS issue_link
FROM supporting AS s
UNION
-- the issue at the other end of each supporting link, which may sit in a project
-- that was not returned; its project is not listed (the accepted plan)
SELECT
    s.project_key AS project,
    s.other_key AS issue,
    s.link_id AS issue_link
FROM supporting AS s
ORDER BY project, issue, issue_link
