// Q02 answer: the CWD issues resolved as Fixed that have at least one
// unreleased fix version. Steps refer to the Q02 reading note.
// step 1: the CWD population, no date bounds
MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey = 'CWD'
    // step 2: marked Fixed means the resolution name is exactly 'Fixed'
    AND i.resolutionName = 'Fixed'
    // step 3: at least one fix version whose released flag is false; EXISTS
    // tests the issue once, however many unreleased versions it has
    AND EXISTS { (i)-[:FIXED_IN]->(v:FixVersion) WHERE v.isReleased = false }
// step 4: count never sum, so no match gives 0; DISTINCT counts each issue once
RETURN count(DISTINCT i) AS issues
