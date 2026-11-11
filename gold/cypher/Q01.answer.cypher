// Q01 answer: per project, the issues created in Q1 2013 (UTC) that are
// still unresolved and have no assignee. Steps refer to the Q01 reading note.
// step 1: the seven project population
MATCH (p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
// the zeros: OPTIONAL MATCH keeps every project, its WHERE filters only the issues
OPTIONAL MATCH (i:Issue)-[:IN_PROJECT]->(p)
WHERE
    // step 2: half open UTC interval, written in the Z form
    i.createdAt >= datetime('2013-01-01T00:00:00Z')
    AND i.createdAt < datetime('2013-04-01T00:00:00Z')
    // step 3: unresolved means missing, null or exactly ''
    AND (i.resolutionName IS NULL OR i.resolutionName = '')
    // step 4: no assignee means no ASSIGNED_TO relationship
    AND NOT EXISTS { (i)-[:ASSIGNED_TO]->(:Person) }
// step 5: count never sum, so no match gives 0; DISTINCT counts issues, not matched rows
RETURN p.projectKey AS project_key, count(DISTINCT i) AS issues
ORDER BY project_key
