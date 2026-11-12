// Q03 stage 4: the store's own count of FE issues also resolved strictly after a present due date (step 4).
// Cumulative: the conditions of steps 1 to 4 of the Q03 reading note, nothing else.
MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey = 'FE'
    AND i.resolutionName IS NOT NULL AND i.resolutionName <> ''
    AND i.resolutionDate >= datetime('2012-04-01T00:00:00Z')
    AND i.resolutionDate < datetime('2012-07-01T00:00:00Z')
    AND i.dueAt IS NOT NULL AND i.resolutionDate > i.dueAt
RETURN count(DISTINCT i) AS issues
