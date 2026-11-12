// Q04 sources, step 1 of the known answer: every issue of the population, and
// every positive entry on it, one row per entry. Steps 1 and 2 of the reading
// note. An issue with no positive entry keeps one row whose time_entry cell is
// null, which is no ID (spec 0011 AC-12, amended 2026-11-12); the issue column
// repeats per entry and is read as a set.
MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND (
        i.timeOriginalEstimateS IS NOT NULL
        OR EXISTS { (:Person)-[e:LOGGED_TIME]->(i) WHERE e.seconds > 0 }
    )
// an optional match, so an issue with no positive entry stays in the issue column
OPTIONAL MATCH (:Person)-[t:LOGGED_TIME]->(i)
WHERE t.seconds > 0
RETURN DISTINCT i.issueKey AS issue, t.historyId AS time_entry
ORDER BY issue, time_entry
