// Q03 stage 2: the store's own count of FE issues with a resolution (step 2).
// Cumulative: the conditions of steps 1 to 2 of the Q03 reading note, nothing else.
MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey = 'FE'
    AND i.resolutionName IS NOT NULL AND i.resolutionName <> ''
RETURN count(DISTINCT i) AS issues
