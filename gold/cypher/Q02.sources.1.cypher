// Q02 sources, step 1 of the known answer: every qualifying issue, one row each.
// The same three conditions as the answer file, steps 1 to 3 of the reading note.
MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey = 'CWD'
    AND i.resolutionName = 'Fixed'
    AND EXISTS { (i)-[:FIXED_IN]->(v:FixVersion) WHERE v.isReleased = false }
RETURN DISTINCT i.issueKey AS issue
ORDER BY issue
