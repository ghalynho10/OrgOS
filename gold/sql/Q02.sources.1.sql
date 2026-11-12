-- Q02 sources, step 1 of the known answer: every qualifying issue, one row each.
-- The same three conditions as the answer file, steps 1 to 3 of the reading note.
SELECT DISTINCT i.issue_key AS issue
FROM model.issue AS i
WHERE i.project_key = 'CWD'
    AND i.resolution_name = 'Fixed'
    AND EXISTS (
        SELECT 1
        FROM model.fixed_in AS f
        INNER JOIN model.fix_version AS v ON v.version_id = f.version_id
        WHERE f.issue_key = i.issue_key
            AND v.is_released = false
    )
ORDER BY i.issue_key
