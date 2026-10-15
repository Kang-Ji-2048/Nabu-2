# Handover: picking up Nabu on a new machine

State as of 15 Oct 2026, 11:15. Everything below is on `main`; PRs #1 to #8 are all merged and nothing is open.

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

## 3. Run the XD backtest (the commands that worked at 11:09)

Run one at a time:

```
python -m nabu tawos --input data/raw/xd.jsonl --output data/xd-canonical.jsonl --scope-by component
python -m nabu --db data/xd.sqlite load data/xd-canonical.jsonl
python -m nabu --db data/xd.sqlite replay --run xd1 --tenant tawos --scopes all --offline --quiet
```

Expected output:
- `tawos`: 3,707 work items, 1,536 with an epic, 714 links (399 cross-scope), 19 scopes (`XD`, `XD/Runtime`, `XD/Stream Module`, `XD/Batch`, ...).
- `load`: `{"work_item": 3707, "link": 714, "event": 43227}`
- `replay`: `{"reviews": 109, "hits": 9, "misses": 21}`

That means 109 epics reviewed and 30 warnings graded, of which 9 came true. That hit rate is weak for the pitch; the build thread was looking into which warning type is missing.

Useful variants:
- Drop `--offline` to have Claude phrase, judge and summarise (uses the API key, costs credits).
- `--stop-after 5` pauses at a checkpoint; `python -m nabu --db data/xd.sqlite resume --run xd1` carries on.
- `--viewer-scopes XD/Runtime` (with `--scopes all`) shows the ring-fence: warnings citing tickets outside the viewer's scope are suppressed.
- `--pace 2` waits 2 seconds per step so an audience can follow.
- `python -m nabu --db data/xd.sqlite show --run xd1 --epic <epic id>` prints one stored review.
- Each `--run` name can only be started once; use a new name (`xd2`, ...) for a fresh run.

**Safety-net demo** (synthetic data, works with no download):
```
python -m nabu demo-data
python -m nabu --db data/live-test.sqlite load data/demo.jsonl
python -m nabu --db data/live-test.sqlite replay --run live2 --tenant demo --scopes payments,platform --viewer-scopes payments
```

## 4. Run the dashboard

Two terminals in the repo root:

```
python -m nabu.ui --db data/xd.sqlite --port 8000
```
```
python -m nabu --db data/xd.sqlite replay --run xd2 --tenant tawos --scopes all --pace 2
```

Open `http://127.0.0.1:8000/?run=xd2`. The top strip shows the agent team lighting up as each acts; below are the log, reviews and accuracy curve.

**Public link for judges (live from the laptop):**
```
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8000
```
It prints a `https://....trycloudflare.com` link that works while the laptop and both commands are running. The dashboard is read-only.

**Hosted page on Vercel (not deployed yet):** import Nabu-2 in Vercel, set Root Directory to `frontend` and Framework to Other, and deploy (no build step). Then open `https://<vercel-site>/?api=<tunnel link>&run=xd2`. If the tunnel is down, the page replays a recorded run from `frontend/runs/` so it never shows blank. `python -m nabu.ui --db data/xd.sqlite --export xd1` records a run there.

## 5. Where things are

- `README.md`: one-page summary. `docs/plan.md`: build plan. `docs/agents.md`: how the agents and the TAWOS adapter work. `docs/interface.md`: the dashboard. `docs/demo-projects.md`: why Spring XD, and the best demo epics (XD-3477 first, then XD-2356, XD-1694, XD-232).
- Code: `nabu/store.py` (ring-fenced store), `nabu/replay.py` (simulated clock, checkpoints), `nabu/agents/` (historian, specialists, critic, writer, scorekeeper), `nabu/adapters/` (TAWOS), `nabu/ui.py` and `frontend/` (dashboard).

## 6. Open work, in priority order

1. Improve the XD hit rate (9 of 30) before the shortlist, or pitch around it (the accuracy curve rising over the run is the story).
2. A live (non-offline) run on 3 to 5 XD epics to check the Claude wording on real tickets.
3. Pick the viewer scope for the ring-fence demo (a component scope with plenty of cross-scope links).
4. Screen-record one full run in the dashboard as a backup, and rehearse a 2-minute walkthrough.
5. Deploy `frontend/` on Vercel.
6. Three pitch slides: the problem, the agent-team diagram (`docs/architecture.png`), the backtest result.
7. Dropped for today: GitHub Issues adapter, embeddings, Supabase.
