-- Q10 sources, step 1 of the known answer: every qualifying relationship's source
-- and target issue, its link, and both assignees. Steps refer to the Q10 reading note.
WITH assigned AS (
    -- step 1: issues in the seven projects with an assignee; a NULL or "" key is
    -- none, and the flagged bot set is empty for this corpus (spec 0006 AC-17)
    SELECT
        i.issue_key,
        i.assignee_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND coalesce(i.assignee_key, '') <> ''
),

relation AS (
    -- step 2: an outward label makes the outward side the source
    SELECT
        l.link_id,
        l.from_issue_key AS source_key,
        l.to_issue_key AS target_key
    FROM model.issue_link AS l
    WHERE l.outward_label IN ('blocks', 'depends on', 'duplicates')
    UNION ALL
    -- the inward label "supersedes" makes the inward side the source
    SELECT
        l.link_id,
        l.to_issue_key AS source_key,
        l.from_issue_key AS target_key
    FROM model.issue_link AS l
    WHERE l.inward_label = 'supersedes'
),

qualifying AS (
    -- step 1 on both ends, and step 3: the two assignee keys differ
    SELECT
        r.link_id,
        r.source_key,
        s.assignee_key AS source_person,
        r.target_key,
        t.assignee_key AS target_person
    FROM relation AS r
    INNER JOIN assigned AS s ON s.issue_key = r.source_key
    INNER JOIN assigned AS t ON t.issue_key = r.target_key
    WHERE s.assignee_key <> t.assignee_key
)

-- one row for each end of each qualifying relationship: the issue, the link and
-- that issue's assignee, so the target assignees are in the step too (ruling 4)
SELECT
    q.source_key AS issue,
    q.link_id AS issue_link,
    q.source_person AS person
FROM qualifying AS q
UNION
SELECT
    q.target_key AS issue,
    q.link_id AS issue_link,
    q.target_person AS person
FROM qualifying AS q
ORDER BY issue, issue_link, person
