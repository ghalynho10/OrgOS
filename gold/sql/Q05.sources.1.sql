-- Q05 sources, step 1 of the known answer: every contributing time entry with its
-- issue, one row per entry. The same conditions as the answer file, steps 1 to 3
-- of the reading note; the issue column repeats per entry and is read as a set.
SELECT DISTINCT
    i.issue_key AS issue,
    t.history_id AS time_entry
FROM model.logged_time AS t
INNER JOIN model.issue AS i ON i.issue_key = t.issue_key
WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
    AND i.assignee_key IS NOT NULL AND i.assignee_key <> ''
    AND t.seconds > 0
    AND t.person_key <> i.assignee_key
ORDER BY issue, time_entry
