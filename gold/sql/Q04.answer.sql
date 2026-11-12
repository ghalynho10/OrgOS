-- Q04 answer: per project, total positive seconds logged minus total original
-- estimated seconds. Steps refer to the Q04 reading note.
WITH logged AS (
    SELECT
        i.project_key,
        -- step 3: the positive entry amounts summed per project
        sum(t.seconds) AS seconds
    FROM model.logged_time AS t
    -- each entry joins its one issue, so no entry is repeated
    INNER JOIN model.issue AS i ON i.issue_key = t.issue_key
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        -- step 2: positive entries only
        AND t.seconds > 0
    GROUP BY i.project_key
),

estimated AS (
    SELECT
        i.project_key,
        -- step 4: original estimates summed per project, a missing estimate as 0
        sum(coalesce(i.time_original_estimate_s, 0)) AS seconds
    FROM model.issue AS i
    -- step 1: the population, issues with a present estimate or a positive entry
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND (
            i.time_original_estimate_s IS NOT NULL
            OR EXISTS (
                SELECT 1
                FROM model.logged_time AS t
                WHERE t.issue_key = i.issue_key
                    AND t.seconds > 0
            )
        )
    GROUP BY i.project_key
)

SELECT
    p.project_key,
    -- step 5: logged minus estimated; a project with neither gives 0 - 0
    coalesce(l.seconds, 0) - coalesce(e.seconds, 0) AS difference
-- every project of the seven stays, joined to its two totals or to none
FROM model.project AS p
LEFT JOIN logged AS l ON l.project_key = p.project_key
LEFT JOIN estimated AS e ON e.project_key = p.project_key
WHERE p.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
ORDER BY p.project_key
