-- Q03 stage 1: the store's own count of FE issues (step 1).
-- Cumulative: the condition of step 1 of the Q03 reading note, nothing else.
SELECT count(DISTINCT i.issue_key) AS issues
FROM model.issue AS i
WHERE i.project_key = 'FE'
