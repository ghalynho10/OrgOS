// Q11 sources, step 1 of the known answer: every shared issue, and every positive
// time entry on it, one row per entry. Steps refer to the Q11 reading note. An
// issue with no positive entry keeps one row whose time_entry cell is null, which
// is no ID (spec 0011 AC-12, amended 2026-11-12).
// step 1: issues in the seven projects
MATCH (i:Issue)-[:IN_PROJECT]->(pr:Project)
WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
CALL (i) {
    // step 2: assignee, reporter and creator
    MATCH (i)-[:ASSIGNED_TO|REPORTED_BY|CREATED_BY]->(x:Person)
    RETURN x
    UNION
    // step 3: authors of positive time entries on the issue
    MATCH (x:Person)-[t:LOGGED_TIME]->(i)
    WHERE t.seconds > 0
    RETURN x
}
WITH i, collect(DISTINCT x.personKey) AS people
// step 4: an issue connects people when two distinct people hold a role on it
WHERE size(people) >= 2
// step 3: every positive entry that gives a logging role on a shared issue
OPTIONAL MATCH (:Person)-[t:LOGGED_TIME]->(i)
WHERE t.seconds > 0
RETURN DISTINCT i.issueKey AS issue, t.historyId AS time_entry
ORDER BY issue, time_entry
