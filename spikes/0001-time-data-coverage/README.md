# Spike 0001: time data coverage and the corpus

This spike picks the Jira projects that the rest of phase 1 is built on. The rules are in `rules.py`, committed in `b6638b8` before any number was measured (spec 0004, AC-1). Every script refuses to run when `rules.py` differs from that commit. The spike read Mongo only through the read only login and wrote nothing to it.

**Result: 7 projects from the Jira collection, picked at ladder step 1.** They are CRUC, JRASERVER, BAM, FE, CLOUD, CWD and CLOV, in pick order. The details are in `results/corpus.json`.

## How the spike ran

| Step | Script | What it did | Time |
|---|---|---|---|
| Stage 1 | `stage1_counts.py` | Exact issue counts per (collection, project key) for all 16 collections, reconciled to the pinned counts | 38.0 s |
| Stage 2 | `stage2_entries.py` | Rebuilt every time entry from the changelog, flagged bots, applied the fills rule, chose the person id, checked entries against `timespent` | 64.3 s |
| Choose | `choose.py` | Linked projects by shared people, ranked all 16 collections, walked the ladder, picked | under 1 s |
| Fixtures | `select_fixtures.py` | Drew 270 issues and replaced their prose (`tests/fixtures/corpus/`, 9,695,035 bytes) | 14 s |

You can rerun each one with `uv run --env-file .env python spikes/0001-time-data-coverage/<script>.py`. The exact commands, versions and both `orgos mongo verify` outputs are in `results/run_record.json`.

## The four questions

### 1. Which projects fill both time fields?

A project "fills both" when an issue has logged time above 0 and an original estimate (`timeoriginalestimate`) above 0. This table comes from Stage 1 (`results/project_counts.csv`). "Survivors" have at least 30 such issues (N).

| Collection | Project keys | Keys with any such issue | Survivors (at least 30) | Issues filling both |
|---|---|---|---|---|
| Apache | 646 | 172 | 15 | 1,781 |
| Jira | 30 | 13 | 9 | 2,926 |
| RedHat | 241 | 46 | 9 | 976 |
| Spring | 80 | 19 | 4 | 1,050 |
| JiraEcosystem | 101 | 18 | 4 | 304 |
| Sonatype | 5 | 2 | 1 | 687 |
| SecondLife | 2 | 2 | 1 | 173 |
| Qt | 21 | 5 | 1 | 106 |
| Hyperledger | 32 | 7 | 1 | 78 |
| Sakai | 53 | 3 | 1 | 73 |
| IntelDAOS, JFrog, MariaDB, Mindville, Mojang, MongoDB | 2 to 27 each | 0 | 0 | 0 |

Six collections have no issue at all with both fields filled.

Stage 2 then applied the full fills rule, using logged time rebuilt from the changelog and leaving out bot time (`results/project_measures.csv`). With the rules as written (step 0), **only one project passes in the whole dump: Apache STDCXX** (239 qualifying issues, 22.50 percent of its 1,062 issues, 5 people). Every other survivor fails the 20 percent floor (X). With X dropped (step 1), 40 projects pass: Apache 10, Jira 9, RedHat 9, JiraEcosystem 4, Spring 3, and one each in Sonatype, SecondLife, Qt, Sakai and Hyperledger.

### 2. Which accounts are bots, and how much time do they hold?

An account is flagged as a bot when it has at least 20 positive entries and at least 90 percent of them share one amount (`results/flagged_accounts.csv`). The bot share is the positive seconds logged by flagged accounts divided by the positive seconds logged by all accounts.

| Collection | Flagged accounts | Bot share |
|---|---|---|
| Apache | 2 | 76.35 percent (402,022,800 of 526,560,492 s) |
| Every other collection with time entries (Hyperledger, Jira, JiraEcosystem, Qt, RedHat, Sakai, SecondLife, Sonatype, Spring) | 0 | 0 percent |

Both Apache accounts log 600 s every time. One has 665,729 entries (665,692 of them 600 s), the other 4,326, all 600 s. Neither amount is a whole number of hours, so no flag rests on the round hour concern. The diagnostic column `result_if_round_hour_flags_lifted` in `project_measures.csv` matches `result` on every row, and it was never used for selection.

**Per chosen project, the bot share is 0 percent for all 7.** Jira has no flagged account.

### 3. Do single entries exist, or only per issue totals?

Single entries exist. Each one is a changelog history entry with a `timespent` item: the amount is the new value minus the old one, and it carries an author, a time and a worklog id. The spike rebuilt 693,478 entries across the 10 collections that have any.

To check that they add up, the spike compared each issue's entry sum with its final `timespent`. Issues with a flagged entry or with no time at all were left out (`results/entry_match.csv`, mismatches in `results/entry_mismatches.csv`).

| Collection | Matched | Mismatched | Rate |
|---|---|---|---|
| Apache | 1,791 | 0 | 1.0000 |
| Hyperledger | 152 | 0 | 1.0000 |
| Jira | 5,037 | 33 | 0.9935 |
| JiraEcosystem | 559 | 1 | 0.9982 |
| Qt | 159 | 1 | 0.9938 |
| RedHat | 1,848 | 0 | 1.0000 |
| Sakai | 576 | 2 | 0.9965 |
| SecondLife | 523 | 1 | 0.9981 |
| Sonatype | 1,660 | 0 | 1.0000 |
| Spring | 1,860 | 0 | 1.0000 |

The person id is `key` in eight collections and `accountId` in JiraEcosystem and Sakai. In every collection, 100 percent of entries carry the chosen field. No history entry holds two `timespent` items. Changelogs are cut short (fewer histories stored than `changelog.total`) on 6 JiraEcosystem issues and 3 Sakai issues. Neither collection is in the corpus.

### 4. Which projects share contributors?

Two projects are linked when at least K = 2 non bot people logged positive time in both. Linked projects form groups, and each collection is scored by its largest group (`results/collection_ranking.csv`, both ladder steps). At step 1 the largest groups are Jira 7, JiraEcosystem 4, Apache 3, RedHat 2, then 1 everywhere else.

The links inside the chosen corpus (shared people):

| | CRUC | JRASERVER | BAM | FE | CLOUD | CWD | CLOV |
|---|---|---|---|---|---|---|---|
| CRUC | | 5 | 2 | 36 | | | 3 |
| JRASERVER | 5 | | | 2 | 2 | 2 | |
| BAM | 2 | | | 2 | | 4 | |
| FE | 36 | 2 | 2 | | | | 3 |
| CLOUD | | 2 | | | | 2 | |
| CWD | | 2 | 4 | | 2 | | |
| CLOV | 3 | | | 3 | | | |

A blank cell means fewer than 2 shared people.

## The chosen corpus

Step 0 (the rules as written) found a best group of 1 (Apache STDCXX), so the ladder went to step 1: drop X, keep N = 30, P = 3, K = 2. Jira's group of 7 met the brief's 5 to 10, so the ladder stopped there. Steps 2 and 3 were never scored. The pick took the project with the most qualifying issues, then each time the linked project with the most.

| # | Project | Issues | With logged time | Qualifying | Share | People | Bot share |
|---|---|---|---|---|---|---|---|
| 1 | CRUC | 6,765 | 1,391 | 801 | 11.84% | 46 | 0 |
| 2 | JRASERVER | 47,225 | 1,090 | 627 | 1.33% | 53 | 0 |
| 3 | BAM | 14,625 | 551 | 415 | 2.84% | 23 | 0 |
| 4 | FE | 5,561 | 646 | 263 | 4.73% | 41 | 0 |
| 5 | CLOUD | 7,755 | 391 | 182 | 2.35% | 14 | 0 |
| 6 | CWD | 4,407 | 229 | 172 | 3.90% | 17 | 0 |
| 7 | CLOV | 1,501 | 113 | 65 | 4.33% | 4 | 0 |
| | **Total** | **87,839** | **4,411** | **2,525** | | | |

"With logged time" means issues with `timespent` above 0, from Stage 1. "Qualifying" means issues with net non bot logged time and an original estimate, from Stage 2.

## Limitations

- **This corpus exists only because X was dropped (ladder step 1).** With the 20 percent floor, only Apache STDCXX passes in the whole dump, and a group of one can't answer questions about shared people.
- **Few issues per project qualify.** Each chosen project has 1.33 to 11.84 percent of its issues qualifying. Most issues in the corpus have no time data at all.
- **Most links are thin.** Most links sit at exactly K = 2. Only CRUC and FE share many people (36). Questions that follow people across projects will often rest on two or three people.
- **There is no bot time in the corpus.** Jira has no flagged account, so the bot exclusion is never exercised on corpus data. The only bots in the dump are the two Apache accounts.
- **The flagged entry fixture group is empty.** For the same reason, its quota of 30 has 0 candidates, and the shortfall is recorded in `tests/fixtures/corpus/manifest.json`. Tests of the bot exclusion need synthetic data.
- **Eligibility needed an original estimate.** Eligibility required an original estimate on each qualifying issue (decision 2, a phase 2 need: budget = `timeoriginalestimate` × rate), so projects with logged time but no estimates could not be chosen. Phase 1's hours questions need only logged time, and the loads take whole projects, so every logged entry of the chosen projects is still used: 4,411 issues with logged time, not only the 2,525 qualifying ones. The estimate also makes estimate versus actual questions possible. The rule was fixed before any number was seen and is not changed after the fact.
- **Two passing Jira projects are left out.** JSWCLOUD (245 qualifying, 5 people) and JSWSERVER (130 qualifying, 7 people) pass step 1 but don't share 2 people with anyone in the group, so they link only to each other, forming a separate group of two.
- **An edited worklog is credited to the editor.** The changelog doesn't keep the original logger.
- **The entry match is not perfect in Jira.** 33 Jira issues (0.65 percent of those checked) have entries that don't add up to `timespent`. They are listed in `results/entry_mismatches.csv`. Hours come from the entries, so those issues' hours will differ from Jira's own total.
- **Fixtures keep real text.** Summaries, labels, Atlassian banners and status descriptions are real data under CC BY 4.0. See `tests/fixtures/corpus/README.md` for exactly what was replaced and what was not.
- **There is one extra results file.** `results/project_people.csv` (the people of each project that passes N and P) is not in the spec's file list. `choose.py` needs it to link projects without reading Mongo again.

## Files

`rules.py` holds every threshold and the pure rule functions. `common.py` holds the shared shell (the rules guard, the read only client, the results folder). The scripts write only to `results/` and `tests/fixtures/corpus/`. Unit tests: `tests/test_spike_rules.py`, `tests/test_spike_fixtures.py`. Integration test of Stage 1: `tests/integration/test_spike_stage1.py`.

Data: The Public Jira Dataset (Montgomery, Lüders, Maalej, MSR 2022), Zenodo record 15719919, CC BY 4.0.
