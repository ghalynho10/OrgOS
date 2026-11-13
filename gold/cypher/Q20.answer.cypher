// Q20 answer: the people with the most positive logged seconds in [2009-07-01,
// 2009-10-01) UTC among those who logged in at least two of the seven projects in
// that interval, top five and every person tied with the fifth. Steps refer to the
// Q20 reading note.
// step 1: entries on issues in the seven projects, dated in the half open UTC
// interval, in the Z form
MATCH (x:Person)-[t:LOGGED_TIME]->(i:Issue)-[:IN_PROJECT]->(pr:Project)
WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND t.loggedAt >= datetime('2009-07-01T00:00:00Z')
    AND t.loggedAt < datetime('2009-10-01T00:00:00Z')
    // step 2: positive entries; the flagged bot set is empty for this corpus
    // (spec 0006 AC-17)
    AND t.seconds > 0
// step 3: distinct projects per author; step 4: the qualifying amounts summed per
// author, each entry once, since each relationship is one entry
WITH x, count(DISTINCT pr) AS projects, sum(t.seconds) AS seconds
// step 3: at least two distinct projects
WHERE projects >= 2
// step 5: rank highest first
ORDER BY seconds DESC
WITH collect({person_key: x.personKey, seconds: seconds}) AS ranked
// the fifth total (index 4 of the sorted list), or the lowest when fewer than five
// qualify; keeping every total at or above it keeps the top five and every tie
WITH ranked, coalesce(ranked[4].seconds, ranked[-1].seconds) AS cutoff
UNWIND ranked AS r
WITH r, cutoff
WHERE r.seconds >= cutoff
RETURN r.person_key AS person_key, r.seconds AS seconds
ORDER BY seconds DESC, person_key
