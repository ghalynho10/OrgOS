-- Q20 answer: the people with the most positive logged seconds in [2009-07-01,
-- 2009-10-01) UTC among those who logged in at least two of the seven projects in
-- that interval, top five and every person tied with the fifth. Steps refer to the
-- Q20 reading note.
WITH entry AS (
    SELECT
        t.history_id,
        t.person_key,
        t.seconds,
        i.project_key
    FROM model.logged_time AS t
    -- each entry joins its one issue, so no entry is repeated
    INNER JOIN model.issue AS i ON i.issue_key = t.issue_key
    -- step 1: entries on issues in the seven projects, dated in the half open UTC
    -- interval with an explicit offset
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND t.logged_at >= TIMESTAMPTZ '2009-07-01 00:00:00+00'
        AND t.logged_at < TIMESTAMPTZ '2009-10-01 00:00:00+00'
        -- step 2: positive entries; the flagged bot set is empty for this corpus
        -- (spec 0006 AC-17) and every entry has an author (spec 0006 AC-16)
        AND t.seconds > 0
),

totals AS (
    SELECT
        e.person_key,
        -- step 4: the qualifying amounts summed per author, each entry once
        sum(e.seconds) AS seconds
    FROM entry AS e
    GROUP BY e.person_key
    -- step 3: at least two distinct projects among the author's qualifying entries
    HAVING count(DISTINCT e.project_key) >= 2
),

ranked AS (
    -- step 5: rank highest first; tied totals share a rank, so rank <= 5 keeps the
    -- top five and every person tied with the fifth, or all when fewer qualify
    SELECT
        t.person_key,
        t.seconds,
        rank() OVER (ORDER BY t.seconds DESC) AS place
    FROM totals AS t
)

SELECT
    r.person_key,
    r.seconds
FROM ranked AS r
WHERE r.place <= 5
ORDER BY r.seconds DESC, r.person_key
