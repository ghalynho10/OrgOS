-- Q10 answer: the distinct assignees of source issues in a qualifying relationship
-- to an issue assigned to someone else. Steps refer to the Q10 reading note.
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
    -- step 2: a row reads "from_issue_key outward_label to_issue_key", so an
    -- outward label makes the outward side the source
    SELECT
        l.link_id,
        l.from_issue_key AS source_key,
        l.to_issue_key AS target_key
    FROM model.issue_link AS l
    WHERE l.outward_label IN ('blocks', 'depends on', 'duplicates')
    UNION ALL
    -- and "to_issue_key inward_label from_issue_key" reads the other way, so the
    -- inward label "supersedes" makes the inward side the source
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

-- step 4: the distinct source assignees
SELECT DISTINCT q.source_person AS person_key
FROM qualifying AS q
ORDER BY person_key
