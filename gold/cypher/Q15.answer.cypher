// Q15 answer: the positive logged seconds on CLOUD-643 and its direct sub-tasks,
// summed together. Steps refer to the Q15 reading note.
// step 1: the parent record, CLOUD-643 in CLOUD
MATCH (p:Issue {issueKey: 'CLOUD-643'})-[:IN_PROJECT]->(:Project {projectKey: 'CLOUD'})
CALL (p) {
    RETURN p AS i
    UNION
    // step 2: every direct child, one HAS_PARENT hop
    MATCH (c:Issue)-[:HAS_PARENT]->(p)
    RETURN c AS i
}
// step 3: positive entries on the parent and the children; each entry belongs to
// one issue, so none is counted twice. An entry with no author still counts, and
// the flagged bot set is empty for this corpus (spec 0006 AC-17), so no bot clause
OPTIONAL MATCH ()-[t:LOGGED_TIME]->(i)
WHERE t.seconds > 0
// step 4: the retained amounts summed once each; no entry at all gives 0, as the
// note says an issue with none contributes zero
RETURN coalesce(sum(t.seconds), 0) AS seconds
