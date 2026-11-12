// Q06 answer: the unresolved issues with the most positive seconds logged in
// Q4 2009 (UTC), top five and every issue tied with the fifth, with their
// projects. Steps refer to the Q06 reading note.
// step 1: entries on issues in the seven project population, currently unresolved
MATCH (:Person)-[t:LOGGED_TIME]->(i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND (i.resolutionName IS NULL OR i.resolutionName = '')
    // step 2: positive entries logged in the half open UTC interval, in the Z form
    AND t.seconds > 0
    AND t.loggedAt >= datetime('2009-10-01T00:00:00Z')
    AND t.loggedAt < datetime('2010-01-01T00:00:00Z')
// step 3 and step 4: an issue with no retained entry forms no row; the retained
// amounts summed per issue, in seconds
WITH i, p, sum(t.seconds) AS seconds
// step 5: rank highest first
ORDER BY seconds DESC
WITH collect({issue_key: i.issueKey, project_key: p.projectKey, seconds: seconds}) AS ranked
// the fifth total (index 4 of the sorted list), or the lowest when fewer than five
// qualify; keeping every total at or above it keeps the top five and every tie
WITH ranked, coalesce(ranked[4].seconds, ranked[-1].seconds) AS cutoff
UNWIND ranked AS r
WITH r, cutoff
WHERE r.seconds >= cutoff
RETURN r.issue_key AS issue_key, r.project_key AS project_key, r.seconds AS seconds
ORDER BY seconds DESC, issue_key
