-- Q02 answer: the CWD issues resolved as Fixed that have at least one
-- unreleased fix version. Steps refer to the Q02 reading note.
SELECT
    -- step 4: count never sum, so no match gives 0; DISTINCT counts each issue once
    count(DISTINCT i.issue_key) AS issues
FROM model.issue AS i
-- step 1: the CWD population, no date bounds
WHERE i.project_key = 'CWD'
    -- step 2: marked Fixed means the resolution name is exactly 'Fixed'
    AND i.resolution_name = 'Fixed'
    -- step 3: at least one fix version whose released flag is false; EXISTS
    -- tests the issue once, however many unreleased versions it has
    AND EXISTS (
        SELECT 1
        FROM model.fixed_in AS f
        INNER JOIN model.fix_version AS v ON v.version_id = f.version_id
        WHERE f.issue_key = i.issue_key
            AND v.is_released = false
    )
