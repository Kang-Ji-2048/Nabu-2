# Interface

A single page that shows a replay as it runs: the agent log, each epic's risk review with cited tickets, what the Scorekeeper found when the epic finished, warnings withheld by the ring-fence, and the share of warnings that came true over time.

```bash
python -m nabu replay --run demo1 --tenant demo --scopes payments,platform --viewer-scopes payments --offline
python -m nabu.ui --db data/nabu.sqlite --port 8000   # then open http://127.0.0.1:8000/?run=demo1
```

- Start `nabu.ui` before or during a replay: the page polls every 1.5 seconds and appends new `agent_log` rows, so it doubles as the live log for the demo.
- The server opens SQLite read-only and only reads `run`, `agent_log`, `review`, `warning`, `score` and the titles of cited `work_item`s, so it can't interfere with a running replay.
- No build step or dependencies: `web/index.html` is plain HTML and JavaScript, light and dark mode, and works at phone width.
