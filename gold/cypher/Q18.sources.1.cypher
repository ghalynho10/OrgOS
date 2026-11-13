// Q18 sources, step 1 of the known answer: for every returned person, each issue
// where they hold a qualifying role, the person, and the project of that issue. The
// same conditions as the answer file, steps 1 to 4 of the Q18 reading note. The
// columns repeat per row and are read as sets.
MATCH (i:Issue)-[:IN_PROJECT]->(pr:Project)
WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
MATCH (i)-[:REPORTED_BY|ASSIGNED_TO]->(x:Person)
WITH x, count(DISTINCT pr) AS projects
WHERE projects >= 2
// only the returned people's role issues, so a reporter or assignee who is not
// returned is never listed as a person (ruling 3)
MATCH (i:Issue)-[:REPORTED_BY|ASSIGNED_TO]->(x), (i)-[:IN_PROJECT]->(pr:Project)
WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
RETURN DISTINCT i.issueKey AS issue, x.personKey AS person, pr.projectKey AS project
ORDER BY issue, person, project
