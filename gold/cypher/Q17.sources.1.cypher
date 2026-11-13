// Q17 sources, step 1 of the known answer: the issue link hop, every issue and link of
// a qualifying pair of a returned person. The same conditions as the answer file,
// steps 1 and 2 of the Q17 reading note.
MATCH (a:Issue)-[l]->(b:Issue)
WHERE l.linkId IS NOT NULL
MATCH (a)-[:IN_PROJECT]->(pa:Project), (b)-[:IN_PROJECT]->(pb:Project)
WHERE pa.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pb.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pa.projectKey <> pb.projectKey
MATCH (p:Person)-[ta:LOGGED_TIME]->(a), (p)-[tb:LOGGED_TIME]->(b)
WHERE ta.seconds > 0 AND tb.seconds > 0
WITH DISTINCT l, a, b
// both ends of each qualifying pair, each with its supporting link
UNWIND [a, b] AS i
RETURN DISTINCT i.issueKey AS issue, l.linkId AS issue_link
ORDER BY issue, issue_link
