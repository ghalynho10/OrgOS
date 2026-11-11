// Q01 sources, step 1 of the known answer: every qualifying issue, one row each.
// The same four conditions as the answer file, steps 1 to 4 of the reading note.
MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND i.createdAt >= datetime('2013-01-01T00:00:00Z')
    AND i.createdAt < datetime('2013-04-01T00:00:00Z')
    AND (i.resolutionName IS NULL OR i.resolutionName = '')
    AND NOT EXISTS { (i)-[:ASSIGNED_TO]->(:Person) }
RETURN DISTINCT i.issueKey AS issue
ORDER BY issue
