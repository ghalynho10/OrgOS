// Q11 answer: every person in a shared issue connection and every project of such
// an issue, one set. Steps refer to the Q11 reading note.
// step 1: issues in the seven projects
MATCH (i:Issue)-[:IN_PROJECT]->(pr:Project)
WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
CALL (i) {
    // step 2: assignee, reporter and creator; the flagged bot set is empty for
    // this corpus (spec 0006 AC-17)
    MATCH (i)-[:ASSIGNED_TO|REPORTED_BY|CREATED_BY]->(x:Person)
    RETURN x
    UNION
    // step 3: authors of positive time entries on the issue
    MATCH (x:Person)-[t:LOGGED_TIME]->(i)
    WHERE t.seconds > 0
    RETURN x
}
WITH i, pr, collect(DISTINCT x.personKey) AS people
// step 4: an issue connects people when two distinct people hold a role on it
WHERE size(people) >= 2
// step 5: the distinct people on those issues and their projects, in one set
CALL (pr, people) {
    UNWIND people AS key
    RETURN 'person' AS kind, key AS id
    UNION
    RETURN 'project' AS kind, pr.projectKey AS id
}
RETURN DISTINCT kind, id
ORDER BY kind, id
