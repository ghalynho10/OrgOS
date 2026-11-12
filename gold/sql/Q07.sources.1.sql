-- Q07 sources, step 1 of the known answer: for every returned issue, each
-- contributing entry with its author, one row per entry. The same conditions and
-- cut as the answer file, steps 1 to 5 of the reading note; step 6 is the person
-- column. The issue and person columns repeat per entry and are read as sets.
WITH entries AS (
    SELECT
        i.issue_key,
        i.time_original_estimate_s AS estimate,
        t.person_key,
        t.history_id,
        t.seconds
    FROM model.logged_time AS t
    INNER JOIN model.issue AS i ON i.issue_key = t.issue_key
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND i.time_original_estimate_s > 0
        AND t.seconds > 0
),

ranked AS (
    SELECT
        issue_key,
        rank() OVER (ORDER BY abs(estimate - sum(seconds)) DESC) AS place
    FROM entries
    GROUP BY issue_key, estimate
)

SELECT DISTINCT
    e.issue_key AS issue,
    e.person_key AS person,
    e.history_id AS time_entry
FROM entries AS e
INNER JOIN ranked AS r ON r.issue_key = e.issue_key
WHERE r.place <= 10
ORDER BY issue, person, time_entry
