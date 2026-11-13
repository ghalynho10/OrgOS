// Q09 answer: complete chains of issue links followed in either direction between
// issues with different assignees, at most five hops, one row per chain and
// direction. Steps refer to the Q09 reading note.
// step 3: paths over any of the 17 link types, in either direction, one to five
// hops; step 2: each hop joins two qualifying issues with different assignees
MATCH p = (a:Issue)
    (
        (x:Issue)-[r:BLOCKER|BONFIRE_TESTING|CAUSE|CLONERS|DEPENDS|DERIVED|DETAIL|DUPLICATE|FOLLOWS|PART|REFERENCE|REGRESSION|RELATE|RELATED|RESOLVE|SPLIT|SUPERSESSION]-(y:Issue)
        WHERE
            // step 1: both ends in the seven projects, each with one assignee; the
            // flagged bot set is empty for this corpus (spec 0006 AC-17)
            EXISTS {
                (x)-[:IN_PROJECT]->(px:Project)
                WHERE px.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
            }
            AND EXISTS {
                (y)-[:IN_PROJECT]->(py:Project)
                WHERE py.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
            }
            AND COLLECT { MATCH (x)-[:ASSIGNED_TO]->(ax:Person) RETURN ax.personKey } <> []
            AND COLLECT { MATCH (y)-[:ASSIGNED_TO]->(ay:Person) RETURN ay.personKey } <> []
            AND COLLECT { MATCH (x)-[:ASSIGNED_TO]->(ax:Person) RETURN ax.personKey }
                <> COLLECT { MATCH (y)-[:ASSIGNED_TO]->(ay:Person) RETURN ay.personKey }
    ){1,5}
    (b:Issue)
// simple paths: no issue repeats
WHERE all(k IN range(0, size(r)) WHERE NOT nodes(p)[k] IN nodes(p)[k + 1..])
    // step 4: complete chains only. No qualifying link joins the first or the last
    // issue to a qualifying issue off the path with a different assignee. For a
    // five hop path the second test is the hop past five.
    AND NOT EXISTS {
        MATCH (a)-[e:BLOCKER|BONFIRE_TESTING|CAUSE|CLONERS|DEPENDS|DERIVED|DETAIL|DUPLICATE|FOLLOWS|PART|REFERENCE|REGRESSION|RELATE|RELATED|RESOLVE|SPLIT|SUPERSESSION]-(u:Issue)
        WHERE NOT u IN nodes(p)
            AND EXISTS {
                (u)-[:IN_PROJECT]->(pu:Project)
                WHERE pu.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
            }
            AND COLLECT { MATCH (u)-[:ASSIGNED_TO]->(au:Person) RETURN au.personKey } <> []
            AND COLLECT { MATCH (u)-[:ASSIGNED_TO]->(au:Person) RETURN au.personKey }
                <> COLLECT { MATCH (a)-[:ASSIGNED_TO]->(aa:Person) RETURN aa.personKey }
    }
    AND NOT EXISTS {
        MATCH (b)-[e:BLOCKER|BONFIRE_TESTING|CAUSE|CLONERS|DEPENDS|DERIVED|DETAIL|DUPLICATE|FOLLOWS|PART|REFERENCE|REGRESSION|RELATE|RELATED|RESOLVE|SPLIT|SUPERSESSION]-(w:Issue)
        WHERE NOT w IN nodes(p)
            AND EXISTS {
                (w)-[:IN_PROJECT]->(pw:Project)
                WHERE pw.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
            }
            AND COLLECT { MATCH (w)-[:ASSIGNED_TO]->(aw:Person) RETURN aw.personKey } <> []
            AND COLLECT { MATCH (w)-[:ASSIGNED_TO]->(aw:Person) RETURN aw.personKey }
                <> COLLECT { MATCH (b)-[:ASSIGNED_TO]->(ab:Person) RETURN ab.personKey }
    }
// several links between the same two issues, in either direction, give several
// matched paths with the same issues; they are one chain, with every link ID in
// that hop
WITH [n IN nodes(p) | n.issueKey] AS path, r
UNWIND range(0, size(r) - 1) AS h
WITH path, h, collect(DISTINCT r[h].linkId) AS links
WITH path, collect({h: h, links: links}) AS hops
RETURN
    path,
    [k IN range(0, size(path) - 2) | [x IN hops WHERE x.h = k][0].links] AS issue_link,
    // each hop's two issues, in path order
    [k IN range(0, size(path) - 2) | [path[k], path[k + 1]]] AS issue
ORDER BY path
