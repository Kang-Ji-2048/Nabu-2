# Interface

Nabu's frontend is a delivery-review workspace for a team lead: start with the work that needs attention, inspect the evidence behind a warning, and follow how the agents' predictions compare with outcomes. It uses the existing replay engine and read-only JSON API.

## Workspace views

- **Overview:** review and outcome totals, recent reviews, and an introduction to the agent workflow.
- **Reviews:** searchable and filterable epic reviews, accepted warnings, confidence, suggested work, and P50/P90 duration forecasts. Open cited tickets in an evidence dialog or download the selected review as JSON.
- **Agent activity:** filter the recorded actions from the Historian, Planner, specialists, Data quality, Critic, Writer, and Scorekeeper.
- **Outcomes:** confirmed and unconfirmed predictions, with the share of scored warnings that came true over replay time.
- **Evidence:** search the cited ticket history available in the selected run and inspect individual sources.
- **Workspace:** inspect the run, dataset, scope, and connection information.

The run selector changes the source run across the workspace. Forecasts are historical durations in days, not promised completion dates. Confidence describes evidence strength; an unscored warning is pending, not a failed prediction. Suggested tickets and downloaded JSON are review outputs; they do not create or change tracker tickets.

## Run locally

Prepare synthetic history and load the default database:

```bash
python -m nabu demo-data
python -m nabu load data/demo.jsonl
python -m nabu.ui --db data/nabu.sqlite --port 8000
```

In a second terminal, start a replay:

```bash
python -m nabu replay --run demo1 --tenant demo --scopes payments,platform --viewer-scopes payments --offline --pace 2
```

Open `http://127.0.0.1:8000/?run=demo1`. Use a new run ID for another replay, or `python -m nabu resume --run demo1 --offline --pace 2` to continue an unfinished run. `--pace 2` waits two seconds after each replay step so its progress can be watched.

The page polls `/api/state?run=<run>&after=<sequence>` every 1.5 seconds while a run is active, and obtains available runs from `/api/runs`. Agent actions become visible when the current replay step commits. The server opens SQLite read-only and can run alongside the replay process.

**Connection and inference are separate.** A connected API can serve either an active or completed replay. `--offline` runs the deterministic agent logic without model calls; omitting it permits model calls when credentials are configured. The database does not currently persist that inference setting, so a live connection or recorded export alone cannot establish whether a run used a model.

## Recorded runs and static hosting

Export a completed run for a static demonstration:

```bash
python -m nabu.ui --db data/nabu.sqlite --export demo1
```

This writes `frontend/runs/demo1.json` and updates `frontend/runs/index.json`. These exported runs are separate from the selected-review JSON download in the Reviews view: a run export contains the workspace data and agent activity needed by the hosted frontend.

`frontend/` contains plain HTML, CSS, and JavaScript with no build step. In Vercel, import this repository, set **Root Directory** to `frontend`, and set **Framework** to **Other**. The hosted frontend can show the exported files without running Python. When the API is unavailable, the interface explicitly identifies its fallback as a recorded demo; it does not present those saved results as live agent execution. The bundled demo uses synthetic history.

To connect the hosted frontend to a reachable Nabu API, open `https://<your-vercel-site>/?api=https://<your-api-host>&run=<run>`, or set `window.NABU_API` in `frontend/config.js`. The `?api=` URL option overrides the configuration file. With no configured API, the frontend first tries the origin that served the page, then the recorded runs. A temporary tunnel can expose a local demo API if needed:

```bash
python -m nabu.ui --db data/nabu.sqlite --port 8000
cloudflared tunnel --url http://localhost:8000
```

Use the resulting tunnel URL as the `api` value. Hosting the frontend does not host or start the Python agents.

## Deployment scope

This workspace is a review interface, not a production identity or administration system. The API has no authentication and its run selector is not an access-control boundary. Scope metadata and withheld-warning counts describe the replay's configured visibility. Use synthetic or otherwise approved demonstration data when sharing a hosted export or tunnel; company deployment needs authentication and server-enforced access to runs.

Tracker connections, ticket writeback, workspace membership management, and starting or stopping agents from the browser are not implemented. The supported real-data adapter is TAWOS; see [Agent infrastructure](agents.md) for importing history and configuring the replay.

## Frontend checks

Run `node tests/frontend.test.cjs` for polling and safe-rendering regressions, and `python -m unittest discover -s tests` for the backend and API tests. Use `?source=snapshot` to inspect a saved run even when the local API is available. Recorded playback reveals existing events; it does not execute agents.
