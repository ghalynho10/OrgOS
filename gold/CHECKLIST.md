# Gold hand review checklist

Read every gold query against each item before `orgos eval gold accept --reviewed`
(spec 0011, Feature design). The literal check catches long numbers and known IDs;
everything else here is read by you.

1. Each clause is explained in `Qnn.explain.md` in plain words against a numbered step of the reading note.
2. Date bounds are half open `[start, end)` in UTC: an offset in SQL, the `Z` form in Cypher, never a named zone.
3. Counts use `count`, never `sum`, so nothing found gives 0 and not NULL.
4. Every group the question lists, zeros included, comes from the query itself (an outer join in SQL, an `OPTIONAL MATCH` with its `WHERE` inside it in Cypher), never from the adapter.
5. `DISTINCT` is present wherever a join can repeat a record.
6. Missing, null and an empty string mean the same thing in both stores as the reading note says.
7. Hours use `seconds > 0` (spec 0006 AC-15).
8. Link direction and label families follow the reading note: `from_issue_key` holds the outward wording in SQL, a link runs from the outward side in the graph, and `relates to` covers Reference, Relate and Related.
9. A path query keeps `max_hops`, never repeats an entity, and detects a chain that continues past hop 5 (spec 0008 Follow-up).
10. No literal answer value or ID list, and no `LIMIT` or number that happens to equal a known answer (the literal check misses small numbers).
11. Empty source lists are kept as empty lists, as the known answer's step list requires.
12. Both stores' queries express the same reading, and any difference between them is explained.
13. For a zero guarded question, the stage queries follow the numbered source steps in order, with none added, dropped or merged.
