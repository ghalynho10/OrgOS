// Q14 answer: whether every direct sub-task of CLOV-538 is resolved, one row.
// Steps refer to the Q14 reading note.
// step 1: the parent record, CLOV-538 in CLOV; optional, so a missing parent still
// gives one row
OPTIONAL MATCH (p:Issue {issueKey: 'CLOV-538'})-[:IN_PROJECT]->(:Project {projectKey: 'CLOV'})
// step 2: every direct child, one HAS_PARENT hop; collect skips the null of no child
OPTIONAL MATCH (c:Issue)-[:HAS_PARENT]->(p)
WITH collect(DISTINCT c) AS children
// step 2: at least one exists; step 3: each one's resolution is present and not "";
// step 4: the boolean
RETURN
    size(children) > 0
    AND all(child IN children WHERE coalesce(child.resolutionName, '') <> '') AS resolved
