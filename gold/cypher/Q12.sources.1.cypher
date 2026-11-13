// Q12 sources, step 1 of the known answer: each qualifying parent, and every
// contributing sub-task with its parent hop and assignee. Steps refer to the Q12
// reading note. A parent's own row has null parent and person cells, which are no
// ID (spec 0011 AC-12, amended 2026-11-12).
// step 2: direct sub-tasks, one HAS_PARENT hop from child to parent
MATCH (c:Issue)-[:HAS_PARENT]->(p:Issue)
// step 1: the parent in CLOUD or CLOV
MATCH (p)-[:IN_PROJECT]->(pp:Project)
WHERE pp.projectKey IN ['CLOUD', 'CLOV']
// step 3: the sub-task in CLOUD or CLOV with an assignee
MATCH (c)-[:IN_PROJECT]->(pc:Project), (c)-[:ASSIGNED_TO]->(a:Person)
WHERE pc.projectKey IN ['CLOUD', 'CLOV']
// step 4: distinct assignee keys per parent, with every contributing sub-task
WITH
    p,
    collect(DISTINCT a.personKey) AS people,
    collect(DISTINCT {child: c.issueKey, person: a.personKey}) AS children
// step 5: parents with at least two
WHERE size(people) >= 2
// the parent's own issue record, then each contributing sub-task: its issue
// record, its parent hop (the child key, ruling 5) and its assignee
UNWIND
    [{issue: p.issueKey, parent: null, person: null}]
    + [k IN children | {issue: k.child, parent: k.child, person: k.person}] AS row
RETURN DISTINCT row.issue AS issue, row.parent AS parent, row.person AS person
ORDER BY issue, parent, person
