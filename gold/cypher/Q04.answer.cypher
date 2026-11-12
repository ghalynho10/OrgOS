// Q04 answer: per project, total positive seconds logged minus total original
// estimated seconds. Steps refer to the Q04 reading note.
// every project of the seven is a row, whatever its issues hold
MATCH (p:Project)
WHERE p.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
CALL (p) {
    // step 2: positive entries on the project's issues
    OPTIONAL MATCH (:Person)-[t:LOGGED_TIME]->(:Issue)-[:IN_PROJECT]->(p)
    WHERE t.seconds > 0
    // step 3: summed per project; sum of no rows is 0
    RETURN sum(t.seconds) AS logged
}
CALL (p) {
    // step 1: the population, issues with a present estimate or a positive entry
    OPTIONAL MATCH (i:Issue)-[:IN_PROJECT]->(p)
    WHERE i.timeOriginalEstimateS IS NOT NULL
        OR EXISTS { (:Person)-[e:LOGGED_TIME]->(i) WHERE e.seconds > 0 }
    // step 4: original estimates summed per project, a missing estimate as 0
    RETURN sum(coalesce(i.timeOriginalEstimateS, 0)) AS estimated
}
// step 5: logged minus estimated; a project with neither gives 0 - 0
RETURN p.projectKey AS project_key, logged - estimated AS difference
ORDER BY project_key
