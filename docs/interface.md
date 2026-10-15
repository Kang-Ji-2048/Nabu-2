# Interface

A single page that shows a saved project snapshot immediately: agent logs, cited risk reviews and outcomes known at the snapshot's cutoff. There is no timed playback. With a live API connected, the page polls for new reviews and log entries.

```bash
python -m nabu replay --run demo1 --tenant demo --scopes payments,platform --viewer-scopes payments --offline
python -m nabu.ui --db data/nabu.sqlite --port 8000   # then open http://127.0.0.1:8000/?run=demo1
```

- **Review a new epic** at the top takes an epic title and its tickets (`title | points | component`, one per line), adds them to the store as if just created, and runs the historian and review team on them against the whole history, with live Claude calls when `.env` has a key. The page switches to run `live` and the agent boxes light up as each step happens.
- The **How it works** page contains the interactive agent workflow diagram. Select a node to inspect its role; zoom controls make it readable on smaller screens.
- Start `nabu.ui` before or during a replay: the page polls every 1.5 seconds and appends new `agent_log` rows, so it doubles as the live log for the demo.
- The server opens SQLite read-only and only reads `run`, `agent_log`, `review`, `warning`, `score` and the titles of cited `work_item`s, so it can't interfere with a running replay.
- No build step or dependencies: `frontend/index.html` is plain HTML and JavaScript, light and dark mode, and works at phone width.

## Hosting the dashboard (Vercel)

`frontend/` is a static site with no build step, and `vercel.json` points Vercel at it, so importing the repo in Vercel and pressing Deploy is enough.

With no API, the hosted page displays the selected project's saved snapshot in one render. Spring XD, Hyperledger Sawtooth and Apache Mesos use their own cutoff (`pause_at` in `runs/index.json`, retained for file compatibility). Reviews, outcomes and dated log entries after that cutoff are excluded. A recording without a cutoff shows its complete saved state immediately. The new-epic form is hidden there, because it needs the live API. Backend CLI replay remains available to generate the historical data; it does not cause dashboard playback.

The agents still run on a laptop, and the hosted page reads them live:

```bash
python -m nabu.ui --db data/nabu.sqlite --port 8000
cloudflared tunnel --url http://localhost:8000     # prints https://<name>.trycloudflare.com
```

Open `https://<your-vercel-site>/?api=https://<name>.trycloudflare.com&run=<run>`, or put the link in `frontend/config.js`. If the API can't be reached, the page displays the snapshots exported with `python -m nabu.ui --db <db> --export <run>` (saved in `frontend/runs/`) instead.
