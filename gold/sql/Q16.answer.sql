-- Q16 answer: the open Highest priority bugs in the seven projects that are blocked
-- by, or depend on, at least one unresolved corpus issue. Steps refer to the Q16
-- reading note.
WITH bug AS (
    -- step 1: corpus bugs of priority Highest whose resolution is missing, null or ''
    SELECT b.issue_key
    FROM model.issue AS b
    WHERE b.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND b.issue_type_name = 'Bug'
        AND b.priority_name = 'Highest'
        AND (b.resolution_name IS NULL OR b.resolution_name = '')
),

connection AS (
    -- step 2: "is blocked by" is the inward label, so the bug is the inward side
    -- (to_issue_key) and the issue blocking it is the outward side (from_issue_key)
    SELECT
        l.link_id,
        l.to_issue_key AS bug_key,
        l.from_issue_key AS other_key
    FROM model.issue_link AS l
    INNER JOIN bug AS b ON b.issue_key = l.to_issue_key
    WHERE l.inward_label = 'is blocked by'
    UNION ALL
    -- "depends on" is the outward label, so the bug is the outward side
    -- (from_issue_key) and the issue it depends on is the inward side (to_issue_key)
    SELECT
        l.link_id,
        l.from_issue_key AS bug_key,
        l.to_issue_key AS other_key
    FROM model.issue_link AS l
    INNER JOIN bug AS b ON b.issue_key = l.from_issue_key
    WHERE l.outward_label = 'depends on'
)

SELECT
    -- step 4: count never sum, so no match gives 0; DISTINCT counts each bug once
    -- however many qualifying links it has
    count(DISTINCT c.bug_key) AS issues
FROM connection AS c
-- step 3: the other end is a corpus issue whose resolution is missing, null or ''
INNER JOIN model.issue AS o ON o.issue_key = c.other_key
WHERE o.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
    AND (o.resolution_name IS NULL OR o.resolution_name = '')
