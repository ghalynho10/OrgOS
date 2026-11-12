// Q05 answer: per time entry author, the positive seconds they logged on issues
// assigned to someone else. Steps refer to the Q05 reading note.
// step 1: entries on issues in the seven project population
MATCH (author:Person)-[t:LOGGED_TIME]->(i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    // step 3: positive entries only (spec 0006 AC-15)
    AND t.seconds > 0
// step 2: an issue with no ASSIGNED_TO relationship is unassigned and does not
// match; the flagged bot set is empty for this corpus (spec 0006 AC-17)
MATCH (i)-[:ASSIGNED_TO]->(assignee:Person)
// step 3: the entry's author is not the issue's current assignee
WHERE assignee.personKey <> author.personKey
// step 4: the retained amounts summed per author, in seconds
RETURN author.personKey AS person_key, sum(t.seconds) AS seconds
ORDER BY person_key
