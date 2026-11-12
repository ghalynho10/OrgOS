// Q05 sources, step 1 of the known answer: every contributing time entry with its
// issue, one row per entry. The same conditions as the answer file, steps 1 to 3
// of the reading note; the issue column repeats per entry and is read as a set.
MATCH (author:Person)-[t:LOGGED_TIME]->(i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND t.seconds > 0
MATCH (i)-[:ASSIGNED_TO]->(assignee:Person)
WHERE assignee.personKey <> author.personKey
RETURN DISTINCT i.issueKey AS issue, t.historyId AS time_entry
ORDER BY issue, time_entry
