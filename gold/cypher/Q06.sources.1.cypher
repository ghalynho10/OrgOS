// Q06 sources, step 1 of the known answer: for every returned issue, each
// contributing entry, one row per entry. The same conditions and cut as the
// answer file, steps 1 to 5 of the reading note; the issue column repeats per
// entry and is read as a set.
MATCH (:Person)-[t:LOGGED_TIME]->(i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND (i.resolutionName IS NULL OR i.resolutionName = '')
    AND t.seconds > 0
    AND t.loggedAt >= datetime('2009-10-01T00:00:00Z')
    AND t.loggedAt < datetime('2010-01-01T00:00:00Z')
WITH i, sum(t.seconds) AS seconds, collect(t.historyId) AS entries
ORDER BY seconds DESC
WITH collect({issue_key: i.issueKey, seconds: seconds, entries: entries}) AS ranked
WITH ranked, coalesce(ranked[4].seconds, ranked[-1].seconds) AS cutoff
UNWIND ranked AS r
WITH r, cutoff
WHERE r.seconds >= cutoff
UNWIND r.entries AS entry
RETURN DISTINCT r.issue_key AS issue, entry AS time_entry
ORDER BY issue, time_entry
