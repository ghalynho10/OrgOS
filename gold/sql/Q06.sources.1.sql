-- Q06 sources, step 1 of the known answer: for every returned issue, each
-- contributing entry, one row per entry. The same conditions and cut as the
-- answer file, steps 1 to 5 of the reading note; the issue column repeats per
-- entry and is read as a set.
WITH entries AS (
    SELECT
        i.issue_key,
        t.history_id,
        t.seconds
    FROM model.logged_time AS t
    INNER JOIN model.issue AS i ON i.issue_key = t.issue_key
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND (i.resolution_name IS NULL OR i.resolution_name = '')
        AND t.seconds > 0
        AND t.logged_at >= TIMESTAMPTZ '2009-10-01 00:00:00+00'
        AND t.logged_at < TIMESTAMPTZ '2010-01-01 00:00:00+00'
),

ranked AS (
    SELECT
        issue_key,
        rank() OVER (ORDER BY sum(seconds) DESC) AS place
    FROM entries
    GROUP BY issue_key
)

SELECT DISTINCT
    e.issue_key AS issue,
    e.history_id AS time_entry
FROM entries AS e
INNER JOIN ranked AS r ON r.issue_key = e.issue_key
WHERE r.place <= 5
ORDER BY issue, time_entry
