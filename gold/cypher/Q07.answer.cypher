// Q07 answer: the issues with the largest absolute gap between their original
// estimate and their positive logged seconds, top 10 and every issue tied with
// the tenth. Steps refer to the Q07 reading note.
// step 1: entries on issues in the seven project population with an estimate above 0
MATCH (:Person)-[t:LOGGED_TIME]->(i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND i.timeOriginalEstimateS > 0
    // step 2: positive entries only; an issue with none forms no row
    AND t.seconds > 0
// step 3: the retained amounts summed per issue
WITH i, sum(t.seconds) AS logged
// step 4: the absolute difference from the issue's own estimate
WITH i, abs(i.timeOriginalEstimateS - logged) AS difference
// step 5: rank highest first
ORDER BY difference DESC
WITH collect({issue_key: i.issueKey, difference: difference}) AS ranked
// the tenth difference (index 9 of the sorted list), or the lowest when fewer than
// ten qualify; keeping every difference at or above it keeps the top 10 and every tie
WITH ranked, coalesce(ranked[9].difference, ranked[-1].difference) AS cutoff
UNWIND ranked AS r
WITH r, cutoff
WHERE r.difference >= cutoff
RETURN r.issue_key AS issue_key, r.difference AS difference
ORDER BY difference DESC, issue_key
