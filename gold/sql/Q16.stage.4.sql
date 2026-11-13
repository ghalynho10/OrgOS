-- Q16 stage 4: the store's own count of distinct bugs on those links (step 4).
-- Cumulative: the conditions of steps 1 to 4 of the Q16 reading note, nothing else.
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
        l.to_issue_key AS bug_key,
        l.from_issue_key AS other_key
    FROM model.issue_link AS l
    INNER JOIN bug AS b ON b.issue_key = l.to_issue_key
    WHERE l.inward_label = 'is blocked by'
    UNION ALL
    SELECT
        l.from_issue_key AS bug_key,
        l.to_issue_key AS other_key
    FROM model.issue_link AS l
    INNER JOIN bug AS b ON b.issue_key = l.from_issue_key
    WHERE l.outward_label = 'depends on'
)

SELECT count(DISTINCT c.bug_key) AS issues
FROM connection AS c
INNER JOIN model.issue AS o ON o.issue_key = c.other_key
WHERE o.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
    AND (o.resolution_name IS NULL OR o.resolution_name = '')
