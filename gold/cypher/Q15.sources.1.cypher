// Q15 sources, step 1 of the known answer: the parent record, every direct sub-task
// with its parent hop, and every positive time entry on any of them. Steps refer to
// the Q15 reading note. A null cell is no ID (spec 0011 AC-12, amended 2026-11-12).
// step 1: the parent record, CLOUD-643 in CLOUD
MATCH (p:Issue {issueKey: 'CLOUD-643'})-[:IN_PROJECT]->(:Project {projectKey: 'CLOUD'})
CALL (p) {
    // the parent has no parent hop here
    RETURN p AS i, null AS parent
    UNION
    // step 2: every direct child and its parent hop (the child key, ruling B)
    MATCH (c:Issue)-[:HAS_PARENT]->(p)
    RETURN c AS i, c.issueKey AS parent
}
// step 3: each positive entry on the parent or a child; the WHERE sits inside the
// OPTIONAL MATCH, so an issue with none keeps a null time_entry (ruling 4)
OPTIONAL MATCH ()-[t:LOGGED_TIME]->(i)
WHERE t.seconds > 0
RETURN DISTINCT i.issueKey AS issue, parent, t.historyId AS time_entry
ORDER BY issue, parent, time_entry
