// Q03 answer: the FE issues resolved in Q2 2012 (UTC) whose resolution came
// strictly after their due date. Steps refer to the Q03 reading note.
// step 1: the FE population
MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey = 'FE'
    // step 2: resolved means a resolution name that is present and not ''
    AND i.resolutionName IS NOT NULL AND i.resolutionName <> ''
    // step 3: resolution date in the half open UTC interval, in the Z form
    AND i.resolutionDate >= datetime('2012-04-01T00:00:00Z')
    AND i.resolutionDate < datetime('2012-07-01T00:00:00Z')
    // step 4: a present due date, and the resolution strictly later than it;
    // a date only due value is already midnight UTC in the graph (spec 0006 AC-18)
    AND i.dueAt IS NOT NULL AND i.resolutionDate > i.dueAt
// step 5: count never sum, so no match gives 0; DISTINCT counts each issue once
RETURN count(DISTINCT i) AS issues
