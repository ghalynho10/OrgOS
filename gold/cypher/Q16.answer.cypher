// Q16 answer: the open Highest priority bugs in the seven projects that are blocked
// by, or depend on, at least one unresolved corpus issue. Steps refer to the Q16
// reading note.
// step 1: corpus bugs of priority Highest whose resolution is missing, null or ''
MATCH (b:Issue)-[:IN_PROJECT]->(bp:Project)
WHERE bp.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND b.issueTypeName = 'Bug'
    AND b.priorityName = 'Highest'
    AND (b.resolutionName IS NULL OR b.resolutionName = '')
// step 2: a link runs from its outward side to its inward side
CALL (b) {
    // "is blocked by" is the inward label, so the bug is the end node and the
    // issue blocking it the start node
    MATCH (o:Issue)-[l]->(b)
    WHERE l.inwardLabel = 'is blocked by'
    RETURN l, o
    UNION
    // "depends on" is the outward label, so the bug is the start node and the
    // issue it depends on the end node
    MATCH (b)-[l]->(o:Issue)
    WHERE l.outwardLabel = 'depends on'
    RETURN l, o
}
// step 3: the other end is a corpus issue whose resolution is missing, null or ''
MATCH (o)-[:IN_PROJECT]->(op:Project)
WHERE op.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND (o.resolutionName IS NULL OR o.resolutionName = '')
// step 4: count never sum, so no match gives 0; DISTINCT counts each bug once
// however many qualifying links it has
RETURN count(DISTINCT b) AS issues
