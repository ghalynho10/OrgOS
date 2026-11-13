// Q13 sources, step 1 of the known answer: the parent record, every direct sub-task
// with its parent hop, and every positive time entry on a sub-task. Steps refer to
// the Q13 reading note. A null cell is no ID (spec 0011 AC-12, amended 2026-11-12).
// step 1: the parent record, CLOV-108 in CLOV
MATCH (p:Issue {issueKey: 'CLOV-108'})-[:IN_PROJECT]->(:Project {projectKey: 'CLOV'})
CALL (p) {
    // the parent's own issue record
    RETURN p.issueKey AS issue, null AS parent, null AS time_entry
    UNION
    // step 2 and step 3: each sub-task's issue record and its parent hop (the child
    // key, ruling 5)
    MATCH (c:Issue)-[:HAS_PARENT]->(p)
    // step 4: each positive entry on it; the WHERE sits inside the OPTIONAL MATCH, so
    // a sub-task with none keeps a null time_entry. The flagged bot set is empty for
    // this corpus (spec 0006 AC-17), so no bot clause is written.
    OPTIONAL MATCH (:Person)-[t:LOGGED_TIME]->(c)
    WHERE t.seconds > 0
    RETURN c.issueKey AS issue, c.issueKey AS parent, t.historyId AS time_entry
}
RETURN DISTINCT issue, parent, time_entry
ORDER BY issue, parent, time_entry
