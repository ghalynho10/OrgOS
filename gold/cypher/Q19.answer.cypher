// Q19 answer: the projects with the most distinct issues directly linked to an issue
// in another corpus project, top five and every project tied with the fifth. Steps
// refer to the Q19 reading note.
// step 2: direct links of any type, so any relationship carrying a linkId; each link
// is one relationship, read once from its start node to its end node
MATCH (a:Issue)-[l]->(b:Issue)
WHERE l.linkId IS NOT NULL
// step 1: both ends are corpus issues, with their current project; step 2: in
// different projects
MATCH (a)-[:IN_PROJECT]->(pa:Project), (b)-[:IN_PROJECT]->(pb:Project)
WHERE pa.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pb.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pa.projectKey <> pb.projectKey
// direction is ignored: each end of each link, in its own project
UNWIND [[pa, a], [pb, b]] AS side
WITH side[0] AS pr, side[1] AS i
// step 3 and step 4: distinct qualifying issues per project, each issue once in its
// own project however many links or partner projects it has
WITH pr, count(DISTINCT i) AS issues
// step 4: rank highest first
ORDER BY issues DESC
WITH collect({project_key: pr.projectKey, issues: issues}) AS ranked
// step 5: the fifth count (index 4 of the sorted list), or the lowest when fewer than
// five qualify; keeping every count at or above it keeps the top five and every tie
WITH ranked, coalesce(ranked[4].issues, ranked[-1].issues) AS cutoff
UNWIND ranked AS r
WITH r, cutoff
WHERE r.issues >= cutoff
RETURN r.project_key AS project_key, r.issues AS issues
ORDER BY issues DESC, project_key
