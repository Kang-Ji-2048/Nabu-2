"""Supported application API over Nabu's existing review and replay engine."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from . import replay
from .agents.historian import Historian
from .errors import ClosedReviewerError, NotFoundError, ScopeError, ValidationError
from .evaluate import evaluate as evaluate_run
from .ingest import load_records
from .llm import Log
from .models import ReviewReport, RunResult, Viewer, WorkItem, scopes_tuple, timestamp
from .providers import AnthropicProvider, JsonProvider, ProviderRunner
from .review import ReviewTeam
from .store import ScopedStore, connect


def _limit(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"{name} must be a non-negative integer")
    return value


class Reviewer:
    """Owns one SQLite connection; use one instance per thread or request worker.

    Offline by default. Viewer contexts are application-supplied access claims,
    not authentication. The public API is experimental until version 1.0.
    """
    def __init__(self, database: str | Path = ":memory:", *,
                 provider: str | JsonProvider = "offline", model: str | None = None,
                 timeout: float = 60.0):
        self._owns_provider = isinstance(provider, str) and provider == "anthropic"
        if isinstance(provider, str):
            if provider == "offline":
                self._provider = None
            elif provider == "anthropic":
                self._provider = AnthropicProvider(model=model, timeout=timeout)
            else:
                raise ValidationError("provider must be 'offline', 'anthropic', or implement JsonProvider")
        elif isinstance(provider, JsonProvider) and callable(getattr(provider, "generate", None)):
            self._provider = provider
        else:
            raise ValidationError("Custom providers must implement generate()")
        try:
            self._conn = connect(database)
        except Exception:
            if self._owns_provider:
                self._provider.close()
            raise
        self._closed = False

    def _open(self):
        if self._closed:
            raise ClosedReviewerError("Reviewer is closed")

    def __enter__(self):
        self._open()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._conn.close()
            self._closed = True
            if self._owns_provider:
                self._provider.close()

    @staticmethod
    def _scopes(viewer, agent_scopes):
        if not isinstance(viewer, Viewer):
            raise ValidationError("viewer must be a Viewer")
        result = viewer.scopes if agent_scopes is None else scopes_tuple(agent_scopes)
        if not set(viewer.scopes) <= set(result):
            raise ScopeError("Viewer scopes must be a subset of agent scopes")
        return result

    def load(self, records: Iterable[WorkItem | Mapping]) -> dict[str, int]:
        """Atomically load Python work items or canonical work_item/link/event records.

        Do not change source data during an active replay. Import events are
        append-only; importing the same events twice duplicates those rows.
        """
        self._open()
        if self._conn.execute("SELECT 1 FROM run WHERE status='running' LIMIT 1").fetchone():
            raise ValidationError("Finish active replay runs before importing more source records")
        return load_records(self._conn, records)

    def load_jsonl(self, path: str | Path) -> dict[str, int]:
        """Atomically load UTF-8 canonical JSONL; malformed input rolls back the batch."""
        def records():
            with open(path, encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ValidationError(f"Invalid JSON on line {line_number}") from exc
        return self.load(records())

    def review(self, epic_id: str, *, viewer: Viewer, as_of: str | datetime | None = None,
               agent_scopes=None, run_id: str | None = None, max_calls: int = 1000) -> ReviewReport:
        """Review an imported epic and save an immutable report value.

        Each invocation uses an independent run ID. Use replay runs to learn
        from observed outcomes over time. Source history itself is not versioned.
        """
        self._open()
        scopes = self._scopes(viewer, agent_scopes)
        max_calls = _limit(max_calls, "max_calls")
        at = timestamp(as_of if as_of is not None else datetime.now(timezone.utc))
        visible = ScopedStore(self._conn, viewer.tenant, viewer.scopes, at)
        epic = visible.get(epic_id)
        if epic is None:
            raise NotFoundError("Epic is unavailable in this viewer context at the review date")
        if epic["type"] != "epic":
            raise ValidationError("review() requires an epic work item")
        key = self._new_id(run_id, "review")
        with self._conn:
            self._conn.execute("INSERT INTO run VALUES (?,?,?,?,?,?,?,?)",
                               (key, viewer.tenant, json.dumps(scopes), json.dumps(viewer.scopes),
                                0, at, "reviewing", "{}"))
            log = Log(self._conn, key, quiet=True)
            log.sim_at = at
            llm = ProviderRunner(log, self._provider, max_calls)
            store = ScopedStore(self._conn, viewer.tenant, scopes, at)
            Historian(llm, log).refresh(self._conn, key, store)
            report = ReviewTeam(llm, log).review(self._conn, key, epic, store, visible)
            stats = {"reviews": 1, "hits": 0, "misses": 0, "model_calls": llm.calls, "accuracy": []}
            self._conn.execute("UPDATE run SET status='reviewed', stats=? WHERE id=?", (json.dumps(stats), key))
        return ReviewReport.from_dict(key, report)

    def _new_id(self, run_id, prefix):
        key = run_id if run_id is not None else f"{prefix}-{uuid4().hex}"
        if not isinstance(key, str) or not key.strip():
            raise ValidationError("run_id must be a non-empty string")
        if self._conn.execute("SELECT 1 FROM run WHERE id=?", (key,)).fetchone():
            raise ValidationError("Run ID already exists")
        return key

    def start_run(self, *, viewer: Viewer, agent_scopes=None, run_id: str | None = None) -> str:
        """Freeze a replay schedule. Call resume() to execute its steps."""
        self._open()
        scopes = self._scopes(viewer, agent_scopes)
        key = self._new_id(run_id, "replay")
        replay.start(self._conn, key, viewer.tenant, scopes, viewer.scopes)
        return key

    def _run(self, run_id, viewer):
        self._open()
        if not isinstance(viewer, Viewer):
            raise ValidationError("viewer must be a Viewer")
        row = self._conn.execute("SELECT * FROM run WHERE id=? AND tenant_id=?", (run_id, viewer.tenant)).fetchone()
        if row is None:
            raise NotFoundError("Run unavailable in this viewer context")
        if not set(json.loads(row["viewer_scopes"])) <= set(viewer.scopes):
            raise ScopeError("Viewer cannot access all scopes of this saved run")
        return row

    def resume(self, run_id: str, *, viewer: Viewer, stop_after: int | None = None,
               max_calls: int = 1000) -> RunResult:
        """Run from the last checkpoint. max_calls is cumulative over committed steps."""
        row = self._run(run_id, viewer)
        if row["status"] not in {"running", "finished"}:
            raise ValidationError("Only replay runs can be resumed")
        _limit(max_calls, "max_calls")
        if stop_after is not None:
            _limit(stop_after, "stop_after")
        if row["status"] != "finished":
            replay.run(self._conn, run_id, quiet=True, stop_after=stop_after, max_calls=max_calls,
                       llm_factory=lambda log, limit, calls: ProviderRunner(log, self._provider, limit, calls))
        return self.run_state(run_id, viewer=viewer)

    def run_state(self, run_id: str, *, viewer: Viewer) -> RunResult:
        row = self._run(run_id, viewer)
        stats = json.loads(row["stats"])
        return RunResult(run_id, row["status"], row["cursor"], row["clock"],
                         stats.get("reviews", 0), stats.get("hits", 0), stats.get("misses", 0),
                         stats.get("model_calls", 0))

    def reports(self, run_id: str, *, viewer: Viewer) -> tuple[ReviewReport, ...]:
        self._run(run_id, viewer)
        rows = self._conn.execute("SELECT report FROM review WHERE run_id=? ORDER BY as_of, epic_id", (run_id,))
        return tuple(ReviewReport.from_dict(run_id, json.loads(row[0])) for row in rows)

    def evaluate(self, run_id: str, *, viewer: Viewer, after: str | datetime) -> dict:
        row = self._run(run_id, viewer)
        if row["status"] not in {"running", "finished"}:
            raise ValidationError("Evaluate replay runs, not one-off reviews")
        return evaluate_run(self._conn, run_id, timestamp(after))
