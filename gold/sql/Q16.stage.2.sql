-- Q16 stage 2: the store's own count of links with the specified label and direction
-- on those bugs (step 2), each link once.
-- Cumulative: the conditions of steps 1 to 2 of the Q16 reading note, nothing else.
WITH bug AS (
    SELECT b.issue_key
    FROM model.issue AS b
    WHERE b.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND b.issue_type_name = 'Bug'
        AND b.priority_name = 'Highest'
        AND (b.resolution_name IS NULL OR b.resolution_name = '')
),

connection AS (
    SELECT l.link_id
    FROM model.issue_link AS l
    INNER JOIN bug AS b ON b.issue_key = l.to_issue_key
    WHERE l.inward_label = 'is blocked by'
    UNION ALL
    SELECT l.link_id
    FROM model.issue_link AS l
    INNER JOIN bug AS b ON b.issue_key = l.from_issue_key
    WHERE l.outward_label = 'depends on'
)

SELECT count(DISTINCT c.link_id) AS links
FROM connection AS c
