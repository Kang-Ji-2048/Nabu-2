# Demo projects

**Pick: Spring XD (TAWOS key `XD`).** It's small enough to replay in under an hour (3,707 tickets, 2013-2015), every ticket has story points, it has 109 epics with sprint and "Epic Link" history, and it's already loaded in Nabu 1's `data/nabu.sqlite`. 49% of its non-epic tickets (1,761 of 3,598) have an "Epic Link" event, so epic membership is reasonably complete; run `profile-dump` to compare `with_epic` for the backups before switching.

How this was chosen: ranked all TAWOS projects by epics × share of tickets estimated (from `data/reports/projects.json`), then scored XD's 80 epics with 5+ child tickets on scope added after the first week, re-estimates, reopens and "blocks" links. Script: `scripts/rank_epics.py` (reads a Nabu 1 SQLite database); scores are in `docs/xd-epic-scores.json`.

## Demo epics (Spring XD)

| Epic | Title | Week 1 scope | Final scope | Span | Signals | Why it demos well |
| --- | --- | --- | --- | --- | --- | --- |
| XD-3477 | Support to compose batch jobs | 2 tickets | 30 tickets, 92 pts | 58 days | 12 blocks links, 5 re-estimates | **Best live demo.** Short, so the replay is quick, and scope grew 15x with dependencies |
| XD-2356 | Kafka support | 5 tickets | 46 tickets, 181 pts | 367 days | 12 re-estimates | Relatable feature; a "small integration" that took a year |
| XD-1694 | Provide security integration | 1 ticket | 27 tickets, 119 pts | 461 days | 7 re-estimates, 6 blocks links | Security work lands well with enterprise and bank judges |
| XD-232 | HATEOAS REST API | 6 tickets | 54 tickets, 162 pts | Epic marked done after 4 months; child work ran to Nov 2015 | 44 blocks links, 18 re-estimates | "Declared done but wasn't": strong dependency story |

Span = epic created to its last child ticket resolved. Many XD epics were never closed in Jira, so the last child resolution is the meaningful end date.

**Avoid as demos:** bucket epics such as XD-387 "Technical Debt", XD-2399 "CI issues" and XD-1605 "test matrix". They grow by design, so a warning about them is trivially true.

## Backup projects

### Additional dashboard projects

The dashboard also includes recorded, offline replays for **Hyperledger Sawtooth
(`STL`)** and **Apache Mesos (`MESOS`)**. Choose their names in the Project menu.
Each replay is isolated to that project's scope and pauses at its own recorded
cutoff. The synthetic demo remains separately labelled.

These exports are generated from the local TAWOS SQL dump, including issue change
history, components and dependency links. Their `*-provenance.json` files alongside
the run JSON record source counts, conversion counts, review count and cutoff.
They use deterministic mode with zero model requests; do not describe them as
live Claude runs or independently validated predictions.

To reproduce from a fresh work directory, run from the repository root:

```bash
python scripts/build_project_replays.py --source /path/to/TAWOS.sql --projects STL MESOS --work-dir ../work/additional-projects
```

Raw extracts and SQLite files stay in the work directory. Only the compact
dashboard recordings and provenance belong in `frontend/runs/`. Dataset source
and usage terms: [SOLAR-group/TAWOS](https://github.com/SOLAR-group/TAWOS).

| Key | Project | Tickets | Epics | Estimated | Note |
| --- | --- | --- | --- | --- | --- |
| STL | Hyperledger Sawtooth | 1,663 | 84 | 58% | Small; good second replay if XD misbehaves |
| MESOS | Apache Mesos | 10,157 | 282 | 32% | Nabu 1 already has reference data for it |
| DM | LSST Data Management | 26,506 | 2,407 | 78% | Richest data, but too big to replay live; use a time slice |

## Caveats

- TAWOS's authors state research-only use. That's fine for the demo; commercial use of the dataset would need clarifying (from Nabu 1's `docs/etl.md`).
- Scope counts use "Epic Link" add/remove events only. A ticket that was planned but never linked won't appear.
