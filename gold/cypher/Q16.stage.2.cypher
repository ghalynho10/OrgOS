// Q16 stage 2: the store's own count of links with the specified label and direction
// on those bugs (step 2), each link once.
// Cumulative: the conditions of steps 1 to 2 of the Q16 reading note, nothing else.
MATCH (b:Issue)-[:IN_PROJECT]->(bp:Project)
WHERE bp.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND b.issueTypeName = 'Bug'
    AND b.priorityName = 'Highest'
    AND (b.resolutionName IS NULL OR b.resolutionName = '')
CALL (b) {
    MATCH (o:Issue)-[l]->(b)
    WHERE l.inwardLabel = 'is blocked by'
    RETURN l
    UNION
    MATCH (b)-[l]->(o:Issue)
    WHERE l.outwardLabel = 'depends on'
    RETURN l
}
RETURN count(DISTINCT l.linkId) AS links
