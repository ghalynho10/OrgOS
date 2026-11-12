// Q07 sources, step 1 of the known answer: for every returned issue, each
// contributing entry with its author, one row per entry. The same conditions and
// cut as the answer file, steps 1 to 5 of the reading note; step 6 is the person
// column. The issue and person columns repeat per entry and are read as sets.
MATCH (author:Person)-[t:LOGGED_TIME]->(i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND i.timeOriginalEstimateS > 0
    AND t.seconds > 0
WITH i, sum(t.seconds) AS logged, collect({person: author.personKey, entry: t.historyId}) AS entries
WITH i, abs(i.timeOriginalEstimateS - logged) AS difference, entries
ORDER BY difference DESC
WITH collect({issue_key: i.issueKey, difference: difference, entries: entries}) AS ranked
WITH ranked, coalesce(ranked[9].difference, ranked[-1].difference) AS cutoff
UNWIND ranked AS r
WITH r, cutoff
WHERE r.difference >= cutoff
UNWIND r.entries AS e
RETURN DISTINCT r.issue_key AS issue, e.person AS person, e.entry AS time_entry
ORDER BY issue, person, time_entry
