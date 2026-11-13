// Q10 answer: the distinct assignees of source issues in a qualifying relationship
// to an issue assigned to someone else. Steps refer to the Q10 reading note.
// step 2: a link runs from its outward side to its inward side
CALL () {
    // an outward label makes the start node the source
    MATCH (s:Issue)-[r]->(t:Issue)
    WHERE r.outwardLabel IN ['blocks', 'depends on', 'duplicates']
    RETURN s, r, t
    UNION
    // the inward label "supersedes" makes the end node the source
    MATCH (t:Issue)-[r]->(s:Issue)
    WHERE r.inwardLabel = 'supersedes'
    RETURN s, r, t
}
// step 1: both ends in the seven projects, each with an assignee; the flagged bot
// set is empty for this corpus (spec 0006 AC-17)
MATCH
    (s)-[:IN_PROJECT]->(ps:Project),
    (t)-[:IN_PROJECT]->(pt:Project),
    (s)-[:ASSIGNED_TO]->(a:Person),
    (t)-[:ASSIGNED_TO]->(b:Person)
WHERE ps.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pt.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    // step 3: the two assignee keys differ
    AND a.personKey <> b.personKey
// step 4: the distinct source assignees
RETURN DISTINCT a.personKey AS person_key
ORDER BY person_key
