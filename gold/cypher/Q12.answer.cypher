// Q12 answer: parent issues in CLOUD or CLOV whose direct sub-tasks have at least
// two distinct assignees. Steps refer to the Q12 reading note.
// step 2: direct sub-tasks, one HAS_PARENT hop from child to parent
MATCH (c:Issue)-[:HAS_PARENT]->(p:Issue)
// step 1: the parent in CLOUD or CLOV
MATCH (p)-[:IN_PROJECT]->(pp:Project)
WHERE pp.projectKey IN ['CLOUD', 'CLOV']
// step 3: the sub-task in CLOUD or CLOV with an assignee; the flagged bot set is
// empty for this corpus (spec 0006 AC-17)
MATCH (c)-[:IN_PROJECT]->(pc:Project), (c)-[:ASSIGNED_TO]->(a:Person)
WHERE pc.projectKey IN ['CLOUD', 'CLOV']
// step 4: distinct assignee keys per parent
WITH p, collect(DISTINCT a.personKey) AS people
// step 5: parents with at least two
WHERE size(people) >= 2
RETURN p.issueKey AS issue_key
ORDER BY issue_key
