# Agent infrastructure

This is the code behind the architecture diagram. Python 3.11+, standard library only; `anthropic` is needed for live Claude calls.

## Try it

```bash
python -m nabu demo-data                       # synthetic history -> data/demo.jsonl (not real data)
python -m nabu load data/demo.jsonl
python -m nabu replay --run demo1 --tenant demo --scopes payments,platform --viewer-scopes payments --offline
python -m nabu show --run demo1 --epic demo:PAY-111
python -m unittest discover -s tests
```

- Drop `--offline` and put `ANTHROPIC_API_KEY=...` in a `.env` file in the repo root (copy `.env.example`; git ignores `.env`) to have Claude phrase, judge and summarise. Install the SDK with `pip install anthropic`. `NABU_MODEL` (default `claude-opus-5-5`) and `NABU_EFFORT` (default `low`) control the model.
- `--stop-after 5` pauses the run at a checkpoint; `python -m nabu resume --run demo1` carries on from there.
- `--viewer-scopes payments` makes the reader payments-only while the agents read both scopes, so warnings citing platform tickets are suppressed by the ring-fence.

## Real data: TAWOS

Download and unzip TAWOS v1.1 (the inner `TAWOS.sql` is about 4.3 GB). The dump reader parses it directly, so no MySQL server is needed. Each project becomes one scope.

```bash
python -m nabu profile-dump --input data/source/TAWOS.sql            # pick a project with epics and estimates
python -m nabu extract-dump --input data/source/TAWOS.sql --project XD --output data/raw/xd.jsonl
python -m nabu tawos --input data/raw/xd.jsonl --output data/xd-canonical.jsonl
python -m nabu load data/xd-canonical.jsonl
python -m nabu replay --run xd1 --tenant tawos --scopes XD --offline
```

`dump.py`, `extract.py` and `mappings.json` are copied unchanged from Nabu 1. `tawos.py` maps them into the new schema:
- **Epic membership:** TAWOS has no epic column. Membership comes from `Epic Link` and `Epic Child` change events, and `parent_at` records when a ticket joined its epic. If a ticket was put in an epic when it was created, no event may have been logged, so check the `with_epic` count in the report.
- **Open epics:** many epics were never closed in Jira. An open epic whose tickets are all resolved counts as done on the date of its last ticket, so the Scorekeeper can grade it (`epics_closed_from_children` in the report). Epics with any open ticket stay open and are reviewed but never scored.
- **Status:** a "done" ticket whose resolution is "Won't Fix", a duplicate or similar is counted as `cancelled`.
- **Links:** a link's time comes from the `Link` change event when there is one. Otherwise the link counts as known once both tickets exist.
- **Ring-fence demo on one project:** `tawos --scope-by component` makes one scope per component (`XD/Runtime`, `XD/Shell`, ...). A ticket uses its first component, else its epic's; an epic without one takes its tickets' most common component. The report lists the scopes with ticket counts, and `cross_scope_links` says how many links cross scopes, since only those can trigger a suppression. Then replay with `--scopes all --viewer-scopes XD/<component>`. Patterns are mined per scope, so smaller scopes give fewer warnings.

TAWOS's authors state research-only use alongside the Apache 2.0 licence.

## Modules

| Diagram box | Code |
| --- | --- |
| Canonical store, ring-fenced by tenant + scope | `nabu/store.py`: the schema and `ScopedStore`, the single query layer. Every read filters on tenant and scope and is cut off at `as_of`. Later items are invisible, and later resolutions and re-estimates are masked. |
| Adapters | `nabu/adapters/`: TAWOS, ported from Nabu 1 (see below). Any new adapter only has to write canonical JSONL (`{"table": "work_item"|"link"|"event", "row": {...}}`), which `load_jsonl` reads. |
| Replay clock | `nabu/replay.py`: builds the timeline (review each epic one day after it appears; score it when it finishes) and checkpoints after every step. |
| Historian | `nabu/agents/historian.py`: mines estimate, dependency and gap patterns per scope, with cited ticket ids. Claude names them. |
| Planner, Estimator, Dependency hunter, Gap finder | `nabu/agents/specialists.py` |
| Data quality, Critic | `nabu/agents/critic.py` |
| Writer (P50/P90), Scorekeeper | `nabu/agents/writer.py` |
| One epic's review loop | `nabu/review.py` |

## Design rules

- **Claude never supplies evidence.** Specialists build each candidate warning in Python, with its cited tickets and a checkable prediction. Claude rewrites or drops candidates and can't add citations. This is what makes the Scorekeeper's grading meaningful.
- **Critic rules are code.** A warning needs at least 2 cited tickets known at review time. If any cited ticket is outside the reader's scope, the warning is `suppressed`. Claude's judgement only applies to warnings that pass these rules, and a `revise` goes back to the specialist once.
- **Learning loop.** Each scored hit adds 0.2 to the weight of the warning's pattern and each miss takes off 0.3. Specialists ignore patterns whose weight falls below 0.5.
- **Error recovery.** A malformed Claude reply or an API error is retried once. If it fails again, the agent's deterministic result is used and logged as `recovered`. Each step commits atomically with the cursor.
- **Live log.** Every agent action is a row in `agent_log` (run, seq, sim time, agent, kind, message), which the interface can tail. A step's rows appear when that step commits.

## Known gaps

- Similarity is token overlap, not embeddings.
- The store is SQLite. plan.md says Supabase/Postgres, and the schema ports directly.
- Only TAWOS has an adapter. The GitHub Issues adapter in plan.md is still to do.
