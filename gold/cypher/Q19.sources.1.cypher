// Q19 sources, step 1 of the known answer: for every returned project, the project,
// every counted issue, every qualifying link supporting it and the issue at the other
// end. The same conditions and cut as the answer file, steps 1 to 5 of the Q19
// reading note. The columns repeat per row and are read as sets.
// First the returned projects, exactly as the answer file finds them.
CALL () {
    MATCH (a:Issue)-[l]->(b:Issue)
    WHERE l.linkId IS NOT NULL
    MATCH (a)-[:IN_PROJECT]->(pa:Project), (b)-[:IN_PROJECT]->(pb:Project)
    WHERE pa.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
        AND pb.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
        AND pa.projectKey <> pb.projectKey
    UNWIND [[pa, a], [pb, b]] AS side
    WITH side[0] AS pr, side[1] AS i
    WITH pr, count(DISTINCT i) AS issues
    ORDER BY issues DESC
    WITH collect({project_key: pr.projectKey, issues: issues}) AS ranked
    WITH ranked, coalesce(ranked[4].issues, ranked[-1].issues) AS cutoff
    RETURN [r IN ranked WHERE r.issues >= cutoff | r.project_key] AS returned
}
// Then every qualifying link again, kept only where an end sits in a returned project.
MATCH (a:Issue)-[l]->(b:Issue)
WHERE l.linkId IS NOT NULL
MATCH (a)-[:IN_PROJECT]->(pa:Project), (b)-[:IN_PROJECT]->(pb:Project)
WHERE pa.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pb.projectKey IN ['BAM', 'CLOUD', 'CLOV', 'CRUC', 'CWD', 'FE', 'JRASERVER']
    AND pa.projectKey <> pb.projectKey
// each end of each link, in its own project, with the other end
UNWIND [[pa, a, b], [pb, b, a]] AS side
WITH returned, side[0] AS pr, side[1] AS i, side[2] AS other, l
WHERE pr.projectKey IN returned
// the counted issue and the issue at the other end, which may sit in a project that
// was not returned; that project is not listed (the accepted plan)
UNWIND [i, other] AS listed
RETURN DISTINCT pr.projectKey AS project, listed.issueKey AS issue, l.linkId AS issue_link
ORDER BY project, issue, issue_link
