// Q08 answer: complete chains of outward "depends on" links between unresolved
// issues, at most five hops, one row per chain. Steps refer to the Q08 reading note.
// step 3: paths in the outward direction, one to five hops
MATCH p = (a:Issue)-[rs*1..5]->(b:Issue)
// step 2: every hop is an outward "depends on" link
WHERE all(r IN rs WHERE r.outwardLabel = 'depends on')
    // step 1: every issue on the path is unresolved and in the seven projects
    AND all(
        n IN nodes(p)
        WHERE coalesce(n.resolutionName, '') = ''
            AND EXISTS {
                (n)-[:IN_PROJECT]->(pr:Project)
                WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
            }
    )
    // simple paths: no issue repeats
    AND all(k IN range(0, size(rs)) WHERE NOT nodes(p)[k] IN nodes(p)[k + 1..])
    // step 4: complete chains only. No qualifying link leads into the first issue
    // from an issue off the path, and none leads out of the last issue to an issue
    // off the path. For a five hop path this second test is the hop past five.
    AND NOT EXISTS {
        MATCH (u:Issue)-[e]->(a)
        WHERE e.outwardLabel = 'depends on'
            AND NOT u IN nodes(p)
            AND coalesce(u.resolutionName, '') = ''
            AND EXISTS {
                (u)-[:IN_PROJECT]->(pr:Project)
                WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
            }
    }
    AND NOT EXISTS {
        MATCH (b)-[e]->(w:Issue)
        WHERE e.outwardLabel = 'depends on'
            AND NOT w IN nodes(p)
            AND coalesce(w.resolutionName, '') = ''
            AND EXISTS {
                (w)-[:IN_PROJECT]->(pr:Project)
                WHERE pr.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
            }
    }
// several links between the same two issues give several matched paths with the
// same issues; they are one chain, with every link ID in that hop
WITH [n IN nodes(p) | n.issueKey] AS path, rs
UNWIND range(0, size(rs) - 1) AS h
WITH path, h, collect(DISTINCT rs[h].linkId) AS links
WITH path, collect({h: h, links: links}) AS hops
RETURN
    path,
    [k IN range(0, size(path) - 2) | [x IN hops WHERE x.h = k][0].links] AS issue_link,
    // each hop's two issues, in path order
    [k IN range(0, size(path) - 2) | [path[k], path[k + 1]]] AS issue
ORDER BY path
