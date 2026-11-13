// Q10 sources, step 1 of the known answer: every qualifying relationship's source
// and target issue, its link, and both assignees. Steps refer to the Q10 reading note.
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
// step 1: both ends in the seven projects, each with an assignee
MATCH
    (s)-[:IN_PROJECT]->(ps:Project),
    (t)-[:IN_PROJECT]->(pt:Project),
    (s)-[:ASSIGNED_TO]->(a:Person),
    (t)-[:ASSIGNED_TO]->(b:Person)
WHERE ps.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pt.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    // step 3: the two assignee keys differ
    AND a.personKey <> b.personKey
// one row for each end of each qualifying relationship: the issue, the link and
// that issue's assignee, so the target assignees are in the step too (ruling 4)
UNWIND [[s.issueKey, a.personKey], [t.issueKey, b.personKey]] AS side
RETURN DISTINCT side[0] AS issue, r.linkId AS issue_link, side[1] AS person
ORDER BY issue, issue_link, person
