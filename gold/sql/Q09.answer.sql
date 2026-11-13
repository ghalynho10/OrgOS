-- Q09 answer: complete chains of issue links followed in either direction between
-- issues with different assignees, at most five hops, one row per chain and
-- direction. Steps refer to the Q09 reading note.
WITH RECURSIVE assigned AS (
    -- step 1: issues in the seven projects with an assignee; a NULL or "" key is
    -- none, and the flagged bot set is empty for this corpus (spec 0006 AC-17)
    SELECT
        i.issue_key,
        i.assignee_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND coalesce(i.assignee_key, '') <> ''
),

link AS (
    -- step 2: links of any type whose two ends both qualify and have different
    -- assignee keys; a link from an issue to itself has equal keys and drops here
    SELECT
        l.link_id,
        l.from_issue_key,
        l.to_issue_key
    FROM model.issue_link AS l
    INNER JOIN assigned AS s ON s.issue_key = l.from_issue_key
    INNER JOIN assigned AS d ON d.issue_key = l.to_issue_key
    WHERE s.assignee_key <> d.assignee_key
),

hop AS (
    -- step 3: each link can be followed in either direction, so it gives one hop
    -- each way; several links between the same two issues, in either direction,
    -- are one hop with every link ID
    SELECT
        e.src,
        e.dst,
        jsonb_agg(e.link_id ORDER BY e.link_id) AS links
    FROM (
        SELECT
            from_issue_key AS src,
            to_issue_key AS dst,
            link_id
        FROM link
        UNION ALL
        SELECT
            to_issue_key AS src,
            from_issue_key AS dst,
            link_id
        FROM link
    ) AS e
    GROUP BY e.src, e.dst
),

walk AS (
    -- step 3: simple paths, one hop to start
    SELECT
        ARRAY[h.src, h.dst] AS path,
        jsonb_build_array(h.links) AS links
    FROM hop AS h
    UNION ALL
    -- one more hop from the last issue, never to an issue already on the path,
    -- and never past five hops (six issues)
    SELECT
        w.path || h.dst,
        w.links || jsonb_build_array(h.links)
    FROM walk AS w
    INNER JOIN hop AS h ON h.src = w.path[cardinality(w.path)]
    WHERE cardinality(w.path) < 6
        AND NOT h.dst = ANY(w.path)
)

SELECT
    w.path,
    w.links AS issue_link,
    -- each hop's two issues, in path order
    (
        SELECT jsonb_agg(jsonb_build_array(w.path[k], w.path[k + 1]) ORDER BY k)
        FROM generate_series(1, cardinality(w.path) - 1) AS k
    ) AS issue
FROM walk AS w
-- step 4: complete chains only. No qualifying hop joins the first issue or the
-- last issue to an issue off the path. For a five hop path the second test is the
-- hop past five: if it finds one, the path is a section of a longer chain and is
-- left out. Both directions of a chain pass, so each chain is two rows.
WHERE NOT EXISTS (
    SELECT 1
    FROM hop AS h
    WHERE h.src = w.path[1]
        AND NOT h.dst = ANY(w.path)
)
AND NOT EXISTS (
    SELECT 1
    FROM hop AS h
    WHERE h.src = w.path[cardinality(w.path)]
        AND NOT h.dst = ANY(w.path)
)
ORDER BY w.path
