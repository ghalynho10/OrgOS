-- Q15 answer: the positive logged seconds on CLOUD-643 and its direct sub-tasks,
-- summed together. Steps refer to the Q15 reading note.
WITH family AS (
    -- step 1: the parent record, CLOUD-643 in CLOUD
    SELECT p.issue_key
    FROM model.issue AS p
    WHERE p.issue_key = 'CLOUD-643'
        AND p.project_key = 'CLOUD'
    UNION
    -- step 2: every direct child, by the child's parent key, one hop only
    SELECT c.issue_key
    FROM model.issue AS c
    INNER JOIN model.issue AS p ON p.issue_key = c.parent_key
    WHERE p.issue_key = 'CLOUD-643'
        AND p.project_key = 'CLOUD'
)

SELECT
    -- step 4: the retained amounts summed once each; no entry at all gives 0, as
    -- the note says an issue with none contributes zero
    coalesce(sum(t.seconds), 0) AS seconds
FROM family AS f
-- step 3: positive entries on the parent and the children; each entry belongs to
-- one issue, so none is counted twice. An entry with no author still counts, and
-- the flagged bot set is empty for this corpus (spec 0006 AC-17), so no bot clause
INNER JOIN model.logged_time AS t
    ON t.issue_key = f.issue_key
    AND t.seconds > 0
