-- Q03 answer: the FE issues resolved in Q2 2012 (UTC) whose resolution came
-- strictly after their due date. Steps refer to the Q03 reading note.
SELECT
    -- step 5: count never sum, so no match gives 0; DISTINCT counts each issue once
    count(DISTINCT i.issue_key) AS issues
FROM model.issue AS i
-- step 1: the FE population
WHERE i.project_key = 'FE'
    -- step 2: resolved means a resolution name that is present and not ''
    AND i.resolution_name IS NOT NULL AND i.resolution_name <> ''
    -- step 3: resolution date in the half open UTC interval, with an explicit offset
    AND i.resolution_date >= TIMESTAMPTZ '2012-04-01 00:00:00+00'
    AND i.resolution_date < TIMESTAMPTZ '2012-07-01 00:00:00+00'
    -- step 4: a present due date, and the resolution strictly later than it;
    -- a date only due value is already midnight UTC in model (spec 0006 AC-18)
    AND i.due_at IS NOT NULL AND i.resolution_date > i.due_at
