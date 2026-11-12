-- Q06 answer: the unresolved issues with the most positive seconds logged in
-- Q4 2009 (UTC), top five and every issue tied with the fifth, with their
-- projects. Steps refer to the Q06 reading note.
WITH totals AS (
    SELECT
        i.issue_key,
        i.project_key,
        -- step 4: the retained amounts summed per issue, in seconds
        sum(t.seconds) AS seconds
    FROM model.logged_time AS t
    -- each entry joins its one issue, so no entry is repeated
    INNER JOIN model.issue AS i ON i.issue_key = t.issue_key
    -- step 1: the seven project population, currently unresolved
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND (i.resolution_name IS NULL OR i.resolution_name = '')
        -- step 2: positive entries logged in the half open UTC interval
        AND t.seconds > 0
        AND t.logged_at >= TIMESTAMPTZ '2009-10-01 00:00:00+00'
        AND t.logged_at < TIMESTAMPTZ '2010-01-01 00:00:00+00'
    -- step 3: an issue with no retained entry forms no group
    GROUP BY i.issue_key, i.project_key
),

ranked AS (
    -- step 5: rank highest first; tied totals share a rank, so rank <= 5 keeps the
    -- top five and every issue tied with the fifth, or all when fewer qualify
    SELECT
        issue_key,
        project_key,
        seconds,
        rank() OVER (ORDER BY seconds DESC) AS place
    FROM totals
)

SELECT issue_key, project_key, seconds
FROM ranked
WHERE place <= 5
ORDER BY seconds DESC, issue_key
