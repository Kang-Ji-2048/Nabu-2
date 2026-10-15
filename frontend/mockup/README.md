# Nabu dashboard mockup

Open `index.html` in a browser. This is a standalone dashboard with no install,
build, network requests, credentials, or backend required.

The interface includes overview, risk reviews, agent team, run history and data
source views. Click a risk to open its evidence drawer; filter priority, search
with Ctrl/Cmd K, export a review, pause the example replay or run a simulated
review. Project and date selectors are illustrative and disclose this when used.

All workspace data, source connections, chart points and risk evidence are
illustrative. The mockup does not connect to SQLite, external trackers or models.
No tickets are created. Exported JSON includes a `mockup: true` marker.

Design: warm neutral surfaces, restrained olive actions, a flat metrics strip,
findings before charts, plain task labels and an evidence-focused review table.
Layouts adapt to desktop and mobile widths; dialogs support Escape, focus trapping
and focus restoration, and risks can be opened by keyboard.

JavaScript syntax and HTML structure were checked locally. Visual browser QA was
unavailable because the browser policy rejected the preview action. Cloud Sites
publishing tools were unavailable in this session, so this result is local only.

To serve it locally, use Python 3:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory .
```

Then open http://127.0.0.1:8765 in your browser.
