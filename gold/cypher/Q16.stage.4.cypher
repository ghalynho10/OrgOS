// Q16 stage 4: the store's own count of distinct bugs on those links (step 4).
// Cumulative: the conditions of steps 1 to 4 of the Q16 reading note, nothing else.
MATCH (b:Issue)-[:IN_PROJECT]->(bp:Project)
WHERE bp.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND b.issueTypeName = 'Bug'
    AND b.priorityName = 'Highest'
    AND (b.resolutionName IS NULL OR b.resolutionName = '')
CALL (b) {
    MATCH (o:Issue)-[l]->(b)
    WHERE l.inwardLabel = 'is blocked by'
    RETURN l, o
    UNION
    MATCH (b)-[l]->(o:Issue)
    WHERE l.outwardLabel = 'depends on'
    RETURN l, o
}
MATCH (o)-[:IN_PROJECT]->(op:Project)
WHERE op.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND (o.resolutionName IS NULL OR o.resolutionName = '')
RETURN count(DISTINCT b) AS issues
