// Q03 stage 5: the store's own count of FE issues counted once each (step 5: no new condition, the count itself).
// Cumulative: the conditions of steps 1 to 5 of the Q03 reading note, nothing else.
MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey = 'FE'
    AND i.resolutionName IS NOT NULL AND i.resolutionName <> ''
    AND i.resolutionDate >= datetime('2012-04-01T00:00:00Z')
    AND i.resolutionDate < datetime('2012-07-01T00:00:00Z')
    AND i.dueAt IS NOT NULL AND i.resolutionDate > i.dueAt
RETURN count(DISTINCT i) AS issues
