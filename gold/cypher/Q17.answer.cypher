// Q17 answer: per person, the positive seconds they logged on the issues of their
// qualifying pairs (two directly linked corpus issues in different projects, with a
// positive entry by that person on each). Steps refer to the Q17 reading note.
// step 1: directly linked corpus issues in different projects; any link type, so any
// relationship carrying a linkId; direction ignored, so each link is read once from
// its start node to its end node with its two ends
MATCH (a:Issue)-[l]->(b:Issue)
WHERE l.linkId IS NOT NULL
MATCH (a)-[:IN_PROJECT]->(pa:Project), (b)-[:IN_PROJECT]->(pb:Project)
WHERE pa.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pb.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pa.projectKey <> pb.projectKey
// step 2: the same author has a positive entry on both ends; the flagged bot set is
// empty for this corpus (spec 0006 AC-17)
MATCH (p:Person)-[ta:LOGGED_TIME]->(a), (p)-[tb:LOGGED_TIME]->(b)
WHERE ta.seconds > 0 AND tb.seconds > 0
WITH DISTINCT p, a, b
// step 3: the distinct issues in at least one qualifying pair, per author
UNWIND [a, b] AS i
WITH DISTINCT p, i
// step 4: the author's positive entries on those issues, each entry once
MATCH (p)-[t:LOGGED_TIME]->(i)
WHERE t.seconds > 0
RETURN p.personKey AS person_key, sum(t.seconds) AS seconds
ORDER BY person_key
