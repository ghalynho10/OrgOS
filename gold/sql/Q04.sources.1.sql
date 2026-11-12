-- Q04 sources, step 1 of the known answer: every issue of the population, and
-- every positive entry on it, one row per entry. Steps 1 and 2 of the reading
-- note. An issue with no positive entry keeps one row whose time_entry cell is
-- NULL, which is no ID (spec 0011 AC-12, amended 2026-11-12); the issue column
-- repeats per entry and is read as a set.
SELECT DISTINCT
    i.issue_key AS issue,
    t.history_id AS time_entry
FROM model.issue AS i
-- an outer join, so an issue with no positive entry stays in the issue column
LEFT JOIN model.logged_time AS t
    ON t.issue_key = i.issue_key
    AND t.seconds > 0
WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
    AND (
        i.time_original_estimate_s IS NOT NULL
        OR EXISTS (
            SELECT 1
            FROM model.logged_time AS e
            WHERE e.issue_key = i.issue_key
                AND e.seconds > 0
        )
    )
ORDER BY issue, time_entry
