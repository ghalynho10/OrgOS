// Q03 stage 1: the store's own count of FE issues (step 1).
// Cumulative: the condition of step 1 of the Q03 reading note, nothing else.
MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey = 'FE'
RETURN count(DISTINCT i) AS issues
