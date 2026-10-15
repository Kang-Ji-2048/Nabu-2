# Handover: picking up Nabu on a new machine

State as of 15 Oct 2026, 11:20. Everything below is on `main`; PRs #1 to #10 are all merged and nothing is open. Run `git pull` first on any existing checkout.

**Today's deadlines:** judges shortlist at about 12:05, submissions close at 15:00, top-8 pitches at 16:00.

## 1. Set up (Windows, PowerShell)

Needs Python 3.11+ and git.

```
git clone https://github.com/Kang-Ji-2048/Nabu-2.git
cd Nabu-2
pip install anthropic
copy .env.example .env
notepad .env
```

In `.env`, paste your Anthropic key after `ANTHROPIC_API_KEY=`. `.env` is git-ignored, so the key never goes in the repo. There's no other install step; everything runs as `python -m nabu ...` from the repo root.

Check it works: `python -m unittest discover -s tests`

## 2. Get the data (not in the repo)

`data/` is git-ignored. Pick the fastest option:

- **Best: copy from the old machine.** Copy `C:\Users\Admin\Nabu-2\data\raw\xd.jsonl` (the extracted Spring XD project) into `data\raw\` on the new machine. Then skip to step 3.
- **Otherwise: from the TAWOS dump.** Download TAWOS v1.1 (linked from the TAWOS GitHub repo, `SOLAR-group/TAWOS`) and unzip it; the inner `TAWOS.sql` is about 4.3 GB. Put it at `data\source\TAWOS.sql`, then:
  ```
  python -m nabu extract-dump --input data/source/TAWOS.sql --project XD --output data/raw/xd.jsonl
  ```
  This reads the whole file, so it takes a few minutes. No MySQL needed.

## 3. Run the XD backtest

Treat XD as one scope (no `--scope-by`): splitting it by component makes each scope too small to learn from. Run one at a time:

```
python -m nabu tawos --input data/raw/xd.jsonl --output data/xd-project.jsonl
python -m nabu --db data/xd-project.sqlite load data/xd-project.jsonl
python -m nabu --db data/xd-project.sqlite replay --run xd1 --tenant tawos --scopes XD --offline --quiet
```

**Headline result:** 109 epics reviewed, and the overrun warnings came true in 31 of 45 cases (69%). This is after PR #9, which stopped the gap finder warning about work past epics never actually added late.

**Ring-fence:** show it on the demo data (two teams), not on XD:
```
python -m nabu demo-data
python -m nabu --db data/live-test.sqlite load data/demo.jsonl
python -m nabu --db data/live-test.sqlite replay --run live2 --tenant demo --scopes payments,platform --viewer-scopes payments
```
The reader sees only payments, so warnings citing platform tickets are suppressed.

Useful variants:
- Drop `--offline` to have Claude phrase, judge and summarise (uses the API key, costs credits).
- `--stop-after N` pauses at a checkpoint; `resume --run <name>` carries on.
- `--pace 2` waits 2 seconds per step so an audience can follow.
- `python -m nabu --db data/xd-project.sqlite show --run xd1 --epic <epic id>` prints one stored review.
- Each `--run` name can only be started once; use a new name for a fresh run.

## 4. The live demo

**Terminal 1:** fast-forward offline to just before the demo epics.
```
python -m nabu --db data/xd-project.sqlite replay --run xd-live --tenant tawos --scopes XD --offline --stop-after 9 --quiet
```
**Terminal 2:** start the dashboard, then open `http://127.0.0.1:8000/?run=xd-live`.
```
python -m nabu.ui --db data/xd-project.sqlite --port 8000
```
**Terminal 1:** run the next 32 steps with live Claude calls.
```
python -m nabu --db data/xd-project.sqlite resume --run xd-live --stop-after 32
```
This reviews XD-237 (Batch jobs on the runtime) and XD-468 (Hadoop distributions), then grades both when they finish; all their warnings came true in the offline run. Screen-record it as the backup. The top strip shows the agent team lighting up as each acts; below are the log, reviews and accuracy curve. To repeat it, use a new run name in place of `xd-live`.

**Public link for judges (live from the laptop):**
```
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8000
```
It prints a `https://....trycloudflare.com` link that works while the laptop and the dashboard are running. The dashboard is read-only.

**Hosted page on Vercel (not deployed yet):** import Nabu-2 in Vercel, set Root Directory to `frontend` and Framework to Other, and deploy (no build step). Then open `https://<vercel-site>/?api=<tunnel link>&run=xd-live`. If the tunnel is down, the page replays a recorded run from `frontend/runs/` so it never shows blank. `python -m nabu.ui --db data/xd-project.sqlite --export xd-live` records a run there.

## 5. Where things are

- `README.md`: one-page summary. `docs/plan.md`: build plan. `docs/agents.md`: how the agents and the TAWOS adapter work. `docs/interface.md`: the dashboard. `docs/demo-projects.md`: why Spring XD, and the best demo epics (the live demo now uses XD-237 and XD-468).
- Code: `nabu/store.py` (ring-fenced store), `nabu/replay.py` (simulated clock, checkpoints), `nabu/agents/` (historian, specialists, critic, writer, scorekeeper), `nabu/adapters/` (TAWOS), `nabu/ui.py` and `frontend/` (dashboard).

## 6. Open work, in priority order

1. Run the live demo above on your machine and screen-record it as the backup (by 12:00).
2. Rehearse a 2-minute walkthrough: the problem, the agent team, the 69% backtest, the ring-fence on demo data.
3. Deploy `frontend/` on Vercel.
4. Three pitch slides: the problem, the agent-team diagram (`docs/architecture.png`), the backtest result.
5. Dropped for today: GitHub Issues adapter, embeddings, Supabase.
