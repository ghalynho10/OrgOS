// Q14 sources, step 1 of the known answer: the parent record and every direct
// sub-task with its parent hop, returned only when the answer is yes; a no has
// empty sources under AC-32 (ruling 7). Steps refer to the Q14 reading note. A
// null cell is no ID (spec 0011 AC-12, amended 2026-11-12).
// step 1: the parent record, CLOV-538 in CLOV
MATCH (p:Issue {issueKey: 'CLOV-538'})-[:IN_PROJECT]->(:Project {projectKey: 'CLOV'})
// step 2: every direct child, one HAS_PARENT hop
OPTIONAL MATCH (c:Issue)-[:HAS_PARENT]->(p)
WITH p, collect(DISTINCT c) AS children
// step 3 and step 4: the same boolean as the answer file
WHERE size(children) > 0
    AND all(child IN children WHERE coalesce(child.resolutionName, '') <> '')
// the parent's own issue record, then every direct child, resolved or not: its
// issue record and its parent hop (the child key, ruling 5)
UNWIND
    [{issue: p.issueKey, parent: null}]
    + [child IN children | {issue: child.issueKey, parent: child.issueKey}] AS row
RETURN DISTINCT row.issue AS issue, row.parent AS parent
ORDER BY issue, parent
