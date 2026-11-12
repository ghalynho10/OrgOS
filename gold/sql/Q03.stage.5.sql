-- Q03 stage 5: the store's own count of FE issues counted once each (step 5: no new condition, the count itself).
-- Cumulative: the conditions of steps 1 to 5 of the Q03 reading note, nothing else.
SELECT count(DISTINCT i.issue_key) AS issues
FROM model.issue AS i
WHERE i.project_key = 'FE'
    AND i.resolution_name IS NOT NULL AND i.resolution_name <> ''
    AND i.resolution_date >= TIMESTAMPTZ '2012-04-01 00:00:00+00'
    AND i.resolution_date < TIMESTAMPTZ '2012-07-01 00:00:00+00'
    AND i.due_at IS NOT NULL AND i.resolution_date > i.due_at
