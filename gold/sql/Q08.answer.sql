-- Q08 answer: complete chains of outward "depends on" links between unresolved
-- issues, at most five hops, one row per chain. Steps refer to the Q08 reading note.
WITH RECURSIVE unresolved AS (
    -- step 1: unresolved issues in the seven projects; a NULL or "" resolution
    SELECT i.issue_key
    FROM model.issue AS i
    WHERE i.project_key IN ('BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER')
        AND coalesce(i.resolution_name, '') = ''
),

hop AS (
    -- step 2: outward "depends on" links whose two ends are both unresolved;
    -- several links between the same two issues are one hop with every link ID
    SELECT
        l.from_issue_key AS src,
        l.to_issue_key AS dst,
        jsonb_agg(l.link_id ORDER BY l.link_id) AS links
    FROM model.issue_link AS l
    INNER JOIN unresolved AS s ON s.issue_key = l.from_issue_key
    INNER JOIN unresolved AS d ON d.issue_key = l.to_issue_key
    WHERE l.outward_label = 'depends on'
    GROUP BY l.from_issue_key, l.to_issue_key
),

walk AS (
    -- step 3: simple paths in the outward direction, one hop to start
    SELECT
        ARRAY[h.src, h.dst] AS path,
        jsonb_build_array(h.links) AS links
    FROM hop AS h
    WHERE h.src <> h.dst
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
-- step 4: complete chains only. No qualifying hop leads into the first issue from
-- an issue off the path, and none leads out of the last issue to an issue off the
-- path. For a five hop path this second test is the hop past five: if it finds
-- one, the path is a section of a longer chain and is left out.
WHERE NOT EXISTS (
    SELECT 1
    FROM hop AS h
    WHERE h.dst = w.path[1]
        AND NOT h.src = ANY(w.path)
)
AND NOT EXISTS (
    SELECT 1
    FROM hop AS h
    WHERE h.src = w.path[cardinality(w.path)]
        AND NOT h.dst = ANY(w.path)
)
ORDER BY w.path
