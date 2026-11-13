-- Q17 sources, step 1 of the known answer: the issue link hop, every issue and link of
-- a qualifying pair of a returned person. The same conditions as the answer file,
-- steps 1 and 2 of the Q17 reading note.
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

logger AS (
    SELECT DISTINCT
        t.person_key,
        t.issue_key
    FROM model.logged_time AS t
    INNER JOIN corpus AS c ON c.issue_key = t.issue_key
    WHERE t.seconds > 0
),

pair AS (
    SELECT DISTINCT
        x.link_id,
        x.a_key,
        x.b_key
    FROM cross_link AS x
    INNER JOIN logger AS la ON la.issue_key = x.a_key
    INNER JOIN logger AS lb
        ON lb.issue_key = x.b_key
        AND lb.person_key = la.person_key
)

-- both ends of each qualifying pair, each with its supporting link
SELECT
    p.a_key AS issue,
    p.link_id AS issue_link
FROM pair AS p
UNION
SELECT
    p.b_key AS issue,
    p.link_id AS issue_link
FROM pair AS p
ORDER BY issue, issue_link
