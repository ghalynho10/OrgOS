# Corpus fixtures

These are 270 real Jira issues from the 7 chosen corpus projects, written by `spikes/0001-time-data-coverage/select_fixtures.py` (spec 0004, AC-16 to AC-19). They keep the Mongo document shape, so load code can be tested on realistic documents without a database.

## Dataset and license

| | |
|---|---|
| Dataset | The Public Jira Dataset (Montgomery, Lüders, Maalej, MSR 2022) |
| Version | Zenodo record 15719919, published 2025-06-23 |
| Collection | `Jira` in database `JiraReposAnon` |
| License | CC BY 4.0. These fixtures are real data, so attribution is required. Cite the dataset as its authors ask (see the main README's data manifest). |

## Files

- One file per project: `BAM.json`, `CLOUD.json`, `CLOV.json`, `CRUC.json`, `CWD.json`, `FE.json`, `JRASERVER.json`. Each is a JSON array of MongoDB canonical extended JSON documents, one issue per line, ordered by numeric issue `id`. Read them with `bson.json_util.loads`.
- `manifest.json` holds the seed, the five groups with their quotas, drawn counts, candidate counts and keys, the 40 links, the shortfalls, the issue keys per file, the total size and, per issue, every concrete path that was replaced.

## The draw

The draw used seed 20261028, in the group order of AC-16. Total size: 9,695,035 bytes, under the 10,000,000 byte limit.

| Group | Quota | Drawn | Candidates |
|---|---|---|---|
| Timed: net non bot logged time and an original estimate | 100 | 100 | 2,525 |
| No time data | 60 | 60 | 83,411 |
| At least one entry by a flagged account | 30 | **0** | 0 |
| At least one negative entry | 30 | 30 | 49 |
| Links with both ends in the corpus | 40 | 40 | 21,721 |

The flagged entry group is empty because the Jira collection has no flagged (bot) account. The shortfall is recorded in `manifest.json` and not filled from another group. If you need to test the bot exclusion, you need synthetic data.

## What was replaced

Each of these values was replaced with the exact placeholder `[prose removed for fixtures]`. A null or absent value was left as it was. Every other field equals the Mongo document.

| Path | Replaced in these fixtures |
|---|---|
| `fields.description` | 211 |
| `fields.environment` | 17 |
| `fields.comments[].body` (AC-17 amendment: where the Jira collection keeps comments) | 1,393 |
| `changelog.histories[].items[].fromString` and `.toString`, where `field` is `description`, `comment` or `environment` | 105 and 114 |
| `changelog.histories[].items[].from` and `.to`, where `field` is `Comment`: the text of a removed comment (AC-17 amendment 2) | 22 and 0 |
| `fields.comment.comments[].body`, `renderedFields.description`, `renderedFields.environment`, `renderedFields.comment.comments[].body` | 0. These paths are absent from every corpus issue in this dump. |

## What was not replaced

Not all prose was removed. Free text in other fields is unchanged. These are the long or notable texts left in place:

- **Summaries.** `fields.summary` on every issue, and the summary history (changelog items whose `field` is `summary`, in 35 issues). One summary edit, on JRASERVER-22980, keeps a personal sentence ("Guys I'm sorry for being silly: ...").
- **Labels**, component and version names, and the summaries and statuses of linked issues, subtasks and parents embedded in `fields.issuelinks[]`, `fields.subtasks[]` and `fields.parent`.
- **Atlassian boilerplate banners** in custom fields: `customfield_20333` (270 issues), `customfield_10250` (254), `customfield_14830` (153) and `customfield_14930` (153).
- **Jira status and resolution descriptions**: `fields.status.description`, `fields.resolution.description`, and the status descriptions inside embedded linked issues, subtasks and parents.
- **Fix version descriptions**: `fields.fixVersions[].description` (5 issues).
- **Official "Atlassian Update" / Current Status notes** in `customfield_10681` (6 issues) and in its changelog items, whose `field` is `Current Status` (6 issues: JRASERVER-2740, 6798, 12891, 22699, 40049 and 42138).
- **Escaped error patterns** in `customfield_14130` ("Regular Expression") and its changelog items. It is set on 6 issues: 5 hold an escaped exception or error message pattern (CRUC-5898, FE-4340, BAM-16628, BAM-17032 and JRASERVER-22980), and CRUC-3944 holds a short string.
- **System data**: sprint strings in `customfield_11930` and time in status strings in `customfield_13231`.

Person and account ids are the dataset's anonymized ids (for example `<<|author_key|...|>>`).
