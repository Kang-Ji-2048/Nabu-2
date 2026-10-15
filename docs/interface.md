# Interface

A single page that shows a replay as it runs: the agent log, each epic's risk review with cited tickets, what the Scorekeeper found when the epic finished, warnings withheld by the ring-fence, and the share of warnings that came true over time.

```bash
python -m nabu replay --run demo1 --tenant demo --scopes payments,platform --viewer-scopes payments --offline
python -m nabu.ui --db data/nabu.sqlite --port 8000   # then open http://127.0.0.1:8000/?run=demo1
```

- **Review a new epic** at the top takes an epic title and its tickets (`title | points | component`, one per line), adds them to the store as if just created, and runs the historian and review team on them against the whole history, with live Claude calls when `.env` has a key. The page switches to run `live` and the agent boxes light up as each step happens.
- The **Agent team** strip at the top is the architecture diagram: each agent's box lights up as it acts and shows its latest action (green for a hit, red for a miss, amber for a suppression, revise or recovery). An offline replay is very fast, so add `--pace 2` to wait 2 seconds after each step.
- Start `nabu.ui` before or during a replay: the page polls every 1.5 seconds and appends new `agent_log` rows, so it doubles as the live log for the demo.
- The server opens SQLite read-only and only reads `run`, `agent_log`, `review`, `warning`, `score` and the titles of cited `work_item`s, so it can't interfere with a running replay.
- No build step or dependencies: `frontend/index.html` is plain HTML and JavaScript, light and dark mode, and works at phone width.

## Hosting the dashboard (Vercel)

`frontend/` is a static site with no build step, and `vercel.json` points Vercel at it, so importing the repo in Vercel and pressing Deploy is enough.

With no API, the hosted page plays the recorded Spring XD backtest (`frontend/runs/xd-demo.json`, built offline from the one-scope XD store). The export is trimmed to the April 2014 cutoff (`pause_at` in `runs/index.json`), and the page treats that as today: it holds only the data known by then, so it shows the open risks plus the earlier warnings already checked against their outcome, and nothing from later. The new-epic form is hidden there, because it needs the live API.

The agents still run on a laptop, and the hosted page reads them live:

```bash
python -m nabu.ui --db data/nabu.sqlite --port 8000
cloudflared tunnel --url http://localhost:8000     # prints https://<name>.trycloudflare.com
```

Open `https://<your-vercel-site>/?api=https://<name>.trycloudflare.com&run=<run>`, or put the link in `frontend/config.js`. If the API can't be reached, the page replays the runs exported with `python -m nabu.ui --db <db> --export <run>` (saved in `frontend/runs/`) instead.
