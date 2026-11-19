# Graph or SQL, and when? Conclusion of Phase 1

## 1. What the frozen rule said

The rule, fixed before the full runs, tested each question type for a real gap between the stores. All three tests read "did not detect a gap":

| Type            | Questions | Untied | Graph ahead | SQL ahead | p   |
| --------------- | --------- | ------ | ----------- | --------- | --- |
| aggregate       | 7         | 1      | 1           | 0         | 1   |
| path_connection | 7         | 4      | 2           | 2         | 7/8 |
| mixed           | 6         | 0      | 0           | 0         | 1   |

For none of the three question types could the test tell whether graph or SQL did better. It doesn't show they're the same either. Too few questions disagreed for the test to reach a conclusion.

## 2. What I expected, and what happened

Runs correct with the reading note (runs with the right answer without it):

- aggregate: SQL 18/21 (18/21), graph 20/21 (18/21)
- path_connection: SQL 16/21 (11/21), graph 14/21 (10/21)
- mixed: SQL 18/18 (18/18), graph 18/18 (16/18)

Aggregates: I expected the stores to be fairly close, with SQL slightly ahead. The graph came out ahead by 1 question.

Paths: I expected the graph to be much better than SQL. SQL came out ahead, mostly because of one question (Q09).

Mixed: I expected a split. Both stores got 18 of 18.

In both stores, most failures were citation failures: the answer was right but the evidence wasn't (SQL 7 of 8 failed runs, graph 5 of 8).

## 3. The questions where the stores differed

Graph minus SQL, correct runs out of 3: Q04 +2, Q08 +1, Q09 −3, Q13 +1, Q14 −1. All other questions tied.

On Q09, SQL is 3/3 against the graph's 0/3 with the note's either direction rule; without it, both are 0/3. Each graph run hit several errors and a query timeout before giving a wrong answer. The gold query for Q09 ran successfully.

## 4. What the plain runs add

Without the reading note, right answers fell from 59 to 47 of 60 (SQL) and from 57 to 44 of 60 (graph), mostly on path questions (SQL 20 to 11, graph 18 to 10, of 21).

That tells me the stores are much more accurate with a note. Whether this can be a viable product without the note is a separate question.

## 5. How far this reaches

Limitations that matter most:

1. The graph was built from tables designed in SQL, so it may be shaped like a database rather than a natural graph, which could put the graph at a disadvantage.
2. Every scored run got the reading note. Real users don't write one, which is why I did the plain runs. Those still told the agent the answer's shape, unit and hop limit, which the scoring needed. A real person would give even less.
3. The model ran on default settings with low thinking effort. More effort might change the results.

What a real deployment would need:

1. This project loaded the data once; a real one would need constant updates.
2. The same person appears under different names and emails in each system.
3. Data sensitivity: a company could use its own model. Nothing in the project requires Claude, though the numbers would need re-measuring on that model.
4. Not everyone may see every ticket, and the agent would have to respect that.
5. Real company data isn't only in Jira: there's chat, docs, code and HR systems.

## 6. Graph or SQL, and when?

This conclusion is about this project only. The counts were close, and where one store led, it was by a single question. In the counts, the note made a bigger difference than the store did. Because too few questions disagreed, the test between the stores is inconclusive and could not pick a winner.
