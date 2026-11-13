// Q16 stage 1: the store's own count of open Highest priority corpus bugs (step 1).
// Cumulative: the condition of step 1 of the Q16 reading note, nothing else.
MATCH (b:Issue)-[:IN_PROJECT]->(bp:Project)
WHERE bp.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND b.issueTypeName = 'Bug'
    AND b.priorityName = 'Highest'
    AND (b.resolutionName IS NULL OR b.resolutionName = '')
RETURN count(DISTINCT b) AS issues
