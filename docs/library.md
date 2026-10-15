# Nabu for Python

Nabu reviews an epic against a team's ticket history and returns cited delivery
risks, suggested work and historical duration forecasts. It is an MIT-licensed,
experimental Python library with no mandatory runtime dependencies. Python 3.11+
is required. The distribution is named **nabu-delivery**; imports use **nabu**.

This is a focused delivery-review library, not a general-purpose agent framework.
The public API is versioned as `0.2.0a1` and may change before 1.0.

## Install

From a checkout containing this release:

```bash
python -m pip install .
# Optional live provider:
python -m pip install ".[live]"
```

Or install its built wheel with `python -m pip install path/to/nabu_delivery-0.2.0a1-py3-none-any.whl`.
The project has not yet been published to PyPI; a registry name is not reserved.
The dashboard is a separate repository application and is not included in the wheel.

## Import tickets and review a plan

```python
from nabu import Reviewer, Viewer, WorkItem

viewer = Viewer(tenant="acme", scopes=("payments",))

with Reviewer("history.sqlite", provider="offline") as reviewer:
    reviewer.load([
        WorkItem(
            id="acme:PAY-142", tenant_id="acme", scope="payments",
            source_key="PAY-142", type="epic", title="Billing migration",
            created_at="2026-10-01T09:00:00Z",
        ),
        WorkItem(
            id="acme:PAY-143", tenant_id="acme", scope="payments",
            source_key="PAY-143", title="Backfill invoices", parent_id="acme:PAY-142",
            components=("database",), estimate=5, estimate_unit="points",
            created_at="2026-10-01T10:00:00Z",
        ),
    ])
    report = reviewer.review("acme:PAY-142", viewer=viewer, as_of="2026-10-02T09:00:00Z")
    print(report.to_markdown())
    report_json = report.to_json()
    report_dict = report.to_dict()
```

This minimal example has no completed history, so no grounded warning or forecast
can be produced yet. Import completed tickets and their events before expecting
useful patterns. `examples/library_quickstart.py` runs a synthetic history with
cited warnings, a paused replay and outcome evaluation.

`Reviewer.load` accepts `WorkItem` values and canonical records of the form
`{"table": "work_item" | "link" | "event", "row": {...}}`. `load_jsonl(path)`
reads the same canonical format. The batch commits atomically. Required references
must exist by the end of the batch. IDs must be globally unique within the database;
namespace them by tenant. Existing IDs cannot be reassigned to another tenant.
Events append; importing identical event records repeatedly duplicates them.

All imported timestamps must have a timezone. The library normalizes them to UTC
with microsecond precision, so offsets and subsecond review cutoffs are consistent.
Import into a new database when migrating legacy, inconsistently formatted records.

## Pause and resume a historical run

```python
from nabu import Reviewer, Viewer

viewer = Viewer("acme", ("payments",))
with Reviewer("history.sqlite") as reviewer:
    run_id = reviewer.start_run(viewer=viewer, run_id="backtest-1")
    partial = reviewer.resume(run_id, viewer=viewer, stop_after=5, max_calls=100)

# A new process can reopen the same SQLite file.
with Reviewer("history.sqlite") as reviewer:
    finished = reviewer.resume(run_id, viewer=viewer, max_calls=100)
    reports = reviewer.reports(run_id, viewer=viewer)
    state = reviewer.run_state(run_id, viewer=viewer)
    metrics = reviewer.evaluate(run_id, viewer=viewer, after="2026-10-01T00:00:00Z")
```

The schedule and committed cursor persist. Exceptions roll back the in-progress
step; a new call resumes from the preceding checkpoint. Imports are blocked while
a replay is active. Treat source rows as immutable for the duration of a backtest.
One-off `review()` invocations are saved under independent run IDs and cannot be
resumed as replays. Pattern weights adapt through scored replay outcomes.

## Live and custom model providers

Offline execution is the default, even when API credentials exist in the process.
To enable Claude explicitly:

```python
from nabu import AnthropicProvider, Reviewer

provider = AnthropicProvider(model="claude-opus-5-5", timeout=60)
with Reviewer("history.sqlite", provider=provider) as reviewer:
    # Import and review using the same API shown above.
    pass
```

Set `ANTHROPIC_API_KEY` outside the source code. The library does not implicitly
load `.env` files. `Reviewer(provider="anthropic", model=..., timeout=...)` is a
convenience form. Live calls were not part of the offline test suite.

Any provider may implement this protocol:

```python
class MyProvider:
    def generate(self, *, agent, system, payload, schema):
        # Call your model, enforcing its own timeout. Return parsed JSON.
        return call_my_model(system=system, input=payload, schema=schema)
```

Pass `provider=MyProvider()` to `Reviewer`. Nabu validates outputs recursively,
makes at most two attempts and falls back to deterministic agent results. Provider
implementations own their network timeout and hidden retries. `max_calls` counts
provider invocations, including retries. Across resumes it counts committed steps;
calls paid for in a crashed step can be repeated. It is not a strict spend limit.

## Public values and errors

- `Viewer`, `WorkItem`: explicit access context and canonical input.
- `ReviewReport`, `Warning`, `Forecast`: typed output and JSON/Markdown export.
- `RunResult`: saved run status, review counts and warning-confirmation rate.
- `JsonProvider`, `AnthropicProvider`: provider contract and optional live provider.
- `ValidationError`, `ScopeError`, `NotFoundError`, `ClosedReviewerError`: exceptions
  derived from `NabuError`.

Use the context manager or call `close()`. One Reviewer owns one SQLite connection;
do not share it across threads or run concurrent workers against the same run ID.
You own the lifetime of any injected provider client.

## Scope and prediction limits

`Viewer` is a declaration from the application, not an authentication service.
Authenticate callers before constructing it. By default, agents and readers use
the same scopes. Explicit `agent_scopes` may broaden agent evidence; warnings with
inaccessible citations are suppressed and forecasts use the viewer's view.
Saved run access requires the correct tenant and all of its recorded viewer scopes.
Raw operator logs and database access are not exposed through this public API.

Historical fields are not fully versioned. Forecasts are empirical percentiles,
confidence is heuristic, and warning confirmation does not measure recall or
classification accuracy. Complete snapshots and independent real-world validation
are still required before making stronger privacy or predictive guarantees.

## Command line and development

Installation adds `nabu`; `python -m nabu` remains supported. Run `nabu --help`.
The tracker adapters retain the canonical JSONL interchange format. Build wheels
and source archives with `python -m build`; see `CONTRIBUTING.md` for validation.

The MIT software licence does not relicense imported datasets or third-party
materials. See `THIRD_PARTY_NOTICES.md`.
