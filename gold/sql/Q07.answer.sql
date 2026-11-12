-- Q07 answer: the issues with the largest absolute gap between their original
-- estimate and their positive logged seconds, top 10 and every issue tied with
-- the tenth. Steps refer to the Q07 reading note.
WITH totals AS (
    SELECT
        i.issue_key,
        -- step 3: the retained amounts summed per issue; step 4: the absolute
        -- difference from the issue's own estimate (one estimate per issue)
        abs(i.time_original_estimate_s - sum(t.seconds)) AS difference
    FROM model.logged_time AS t
    -- each entry joins its one issue, so no entry is repeated
    INNER JOIN model.issue AS i ON i.issue_key = t.issue_key
    -- step 1: the seven project population with an original estimate above 0
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND i.time_original_estimate_s > 0
        -- step 2: positive entries only; an issue with none forms no group
        AND t.seconds > 0
    GROUP BY i.issue_key, i.time_original_estimate_s
),

ranked AS (
    -- step 5: rank highest first; tied differences share a rank, so rank <= 10
    -- keeps the top 10 and every issue tied with the tenth, or all when fewer qualify
    SELECT
        issue_key,
        difference,
        rank() OVER (ORDER BY difference DESC) AS place
    FROM totals
)

SELECT issue_key, difference
FROM ranked
WHERE place <= 10
ORDER BY difference DESC, issue_key
