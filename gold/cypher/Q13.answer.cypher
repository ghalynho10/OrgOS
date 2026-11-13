// Q13 answer: the direct sub-tasks of CLOV-108, by key. Steps refer to the Q13
// reading note. Assignee, status and hours are display values, not stored in the
// answer (ruling 5 and the 1d793bd commit body).
// step 1: the parent record, CLOV-108 in CLOV
MATCH (p:Issue {issueKey: 'CLOV-108'})-[:IN_PROJECT]->(:Project {projectKey: 'CLOV'})
// step 2: every direct child, one HAS_PARENT hop; no filter on assignee or time,
// so a sub-task with neither is kept
MATCH (c:Issue)-[:HAS_PARENT]->(p)
RETURN DISTINCT c.issueKey AS issue_key
ORDER BY issue_key
