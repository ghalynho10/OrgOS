// Q18 answer: the people who are reporter or assignee in at least two of the seven
// projects, each with their count of distinct projects. Steps refer to the Q18
// reading note.
// step 1: issues in the seven project population
MATCH (i:Issue)-[:IN_PROJECT]->(pr:Project)
WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
// step 2: the current reporter or assignee; a missing or "" key has no edge, and the
// flagged bot set is empty for this corpus (spec 0006 AC-17)
MATCH (i)-[:REPORTED_BY|ASSIGNED_TO]->(x:Person)
// step 3: distinct projects per person across both roles, so many issues or both
// roles in one project count one project
WITH x, count(DISTINCT pr) AS projects
// step 4: at least two distinct projects
WHERE projects >= 2
// step 5: each person with their distinct project count
RETURN x.personKey AS person_key, projects
ORDER BY person_key
