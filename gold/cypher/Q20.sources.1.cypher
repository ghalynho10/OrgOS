// Q20 sources, step 1 of the known answer: for every returned person, each qualifying
// entry, its issue and that issue's project, and the person. The same conditions and
// cut as the answer file, steps 1 to 5 of the Q20 reading note. The columns repeat
// per row and are read as sets.
MATCH (x:Person)-[t:LOGGED_TIME]->(i:Issue)-[:IN_PROJECT]->(pr:Project)
WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND t.loggedAt >= datetime('2009-07-01T00:00:00Z')
    AND t.loggedAt < datetime('2009-10-01T00:00:00Z')
    AND t.seconds > 0
WITH x, count(DISTINCT pr) AS projects, sum(t.seconds) AS seconds,
    collect({issue: i.issueKey, project: pr.projectKey, entry: t.historyId}) AS entries
WHERE projects >= 2
ORDER BY seconds DESC
WITH collect({person_key: x.personKey, seconds: seconds, entries: entries}) AS ranked
WITH ranked, coalesce(ranked[4].seconds, ranked[-1].seconds) AS cutoff
UNWIND ranked AS r
WITH r, cutoff
WHERE r.seconds >= cutoff
// only the returned people's entries (ruling 3); the projects are those of the
// listed issues and entries (ruling 2)
UNWIND r.entries AS e
RETURN DISTINCT e.issue AS issue, r.person_key AS person, e.project AS project,
    e.entry AS time_entry
ORDER BY issue, person, project, time_entry
