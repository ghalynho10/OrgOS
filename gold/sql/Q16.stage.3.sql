-- Q16 stage 3: the store's own count of those links whose other end is an unresolved
-- corpus issue (step 3), each link once.
-- Cumulative: the conditions of steps 1 to 3 of the Q16 reading note, nothing else.
WITH bug AS (
    SELECT b.issue_key
    FROM model.issue AS b
    WHERE b.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND b.issue_type_name = 'Bug'
        AND b.priority_name = 'Highest'
        AND (b.resolution_name IS NULL OR b.resolution_name = '')
),

connection AS (
    SELECT
        l.link_id,
        l.from_issue_key AS other_key
    FROM model.issue_link AS l
    INNER JOIN bug AS b ON b.issue_key = l.to_issue_key
    WHERE l.inward_label = 'is blocked by'
    UNION ALL
    SELECT
        l.link_id,
        l.to_issue_key AS other_key
    FROM model.issue_link AS l
    INNER JOIN bug AS b ON b.issue_key = l.from_issue_key
    WHERE l.outward_label = 'depends on'
)

SELECT count(DISTINCT c.link_id) AS links
FROM connection AS c
INNER JOIN model.issue AS o ON o.issue_key = c.other_key
WHERE o.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
    AND (o.resolution_name IS NULL OR o.resolution_name = '')
