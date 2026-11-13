// Q17 sources, step 2 of the known answer: the shared person hop, every returned
// person and each of their contributing entries on their qualifying issues. The same
// conditions as the answer file, steps 1 to 4 of the Q17 reading note.
MATCH (a:Issue)-[l]->(b:Issue)
WHERE l.linkId IS NOT NULL
MATCH (a)-[:IN_PROJECT]->(pa:Project), (b)-[:IN_PROJECT]->(pb:Project)
WHERE pa.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pb.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pa.projectKey <> pb.projectKey
MATCH (p:Person)-[ta:LOGGED_TIME]->(a), (p)-[tb:LOGGED_TIME]->(b)
WHERE ta.seconds > 0 AND tb.seconds > 0
WITH DISTINCT p, a, b
UNWIND [a, b] AS i
WITH DISTINCT p, i
MATCH (p)-[t:LOGGED_TIME]->(i)
WHERE t.seconds > 0
RETURN DISTINCT p.personKey AS person, t.historyId AS time_entry
ORDER BY person, time_entry
