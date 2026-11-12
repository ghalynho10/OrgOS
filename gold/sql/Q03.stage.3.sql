-- Q03 stage 3: the store's own count of FE issues also resolved in the UTC interval (step 3).
-- Cumulative: the conditions of steps 1 to 3 of the Q03 reading note, nothing else.
SELECT count(DISTINCT i.issue_key) AS issues
FROM model.issue AS i
WHERE i.project_key = 'FE'
    AND i.resolution_name IS NOT NULL AND i.resolution_name <> ''
    AND i.resolution_date >= TIMESTAMPTZ '2012-04-01 00:00:00+00'
    AND i.resolution_date < TIMESTAMPTZ '2012-07-01 00:00:00+00'
