-- Q03 stage 2: the store's own count of FE issues with a resolution (step 2).
-- Cumulative: the conditions of steps 1 to 2 of the Q03 reading note, nothing else.
SELECT count(DISTINCT i.issue_key) AS issues
FROM model.issue AS i
WHERE i.project_key = 'FE'
    AND i.resolution_name IS NOT NULL AND i.resolution_name <> ''
