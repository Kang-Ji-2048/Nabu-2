"""Outcome metrics for an explicit walk-forward evaluation window.

Read persisted predictions; never regenerate them with hindsight. Warning
confirmation is a precision proxy, not classification accuracy or recall.
"""

import json
from datetime import datetime
from statistics import mean

from .agents.writer import percentile
from .store import ScopedStore, duration


def evaluate(conn, run_id, after):
    datetime.fromisoformat(after.replace("Z", "+00:00"))
    run = conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"No run {run_id!r}")
    scopes = json.loads(run["viewer_scopes"])
    rows = conn.execute("SELECT * FROM review WHERE run_id=? AND as_of>=? ORDER BY as_of",
                        (run_id, after)).fetchall()
    errors, baseline_errors, covered, widths = [], [], [], []
    eligible, scored, hits, completed = 0, 0, 0, 0
    for row in rows:
        # Only outcomes reached by this run's committed clock can be evaluated.
        outcome_store = ScopedStore(conn, run["tenant_id"], scopes, run["clock"])
        epic = outcome_store.get(row["epic_id"])
        if epic is None or epic["resolved_at"] is None:
            continue
        actual = duration(epic)
        if actual is None or actual <= 0:
            continue
        completed += 1
        warnings = conn.execute(
            "SELECT w.id, s.hit FROM warning w LEFT JOIN score s ON s.run_id=w.run_id AND s.warning_id=w.id "
            "WHERE w.run_id=? AND w.epic_id=? AND w.status='accepted'", (run_id, epic["id"])).fetchall()
        eligible += len(warnings)
        scored += sum(w["hit"] is not None for w in warnings)
        hits += sum(w["hit"] == 1 for w in warnings)
        forecast = json.loads(row["report"])["forecast"]
        if forecast["p50_days"] is None or forecast["p90_days"] is None:
            continue
        # The baseline sees precisely the same tenant, scopes and review cutoff.
        history = ScopedStore(conn, run["tenant_id"], scopes, row["as_of"])
        days = [duration(e) for e in history.completed("epic")
                if e["id"] != epic["id"] and duration(e) is not None and duration(e) > 0]
        if len(days) < 3:
            continue
        errors.append(abs(actual - forecast["p50_days"]))
        baseline_errors.append(abs(actual - percentile(days, 0.5)))
        covered.append(actual <= forecast["p90_days"])
        widths.append(forecast["p90_days"] - forecast["p50_days"])
    return {
        "run": run_id, "evaluation_after": after, "outcomes_as_of": run["clock"],
        "reviews": len(rows), "completed_epics": completed,
        "accepted_warnings": eligible, "scored_warnings": scored,
        "warning_confirmation_rate": hits / scored if scored else None,
        "forecast_pairs": len(errors),
        "p50_mae_days": mean(errors) if errors else None,
        "baseline_p50_mae_days": mean(baseline_errors) if baseline_errors else None,
        "p90_coverage": mean(covered) if covered else None,
        "mean_p50_p90_width_days": mean(widths) if widths else None,
        "limitations": [
            "Warning confirmation uses heuristic outcome checks; false negatives and recall are not measured.",
            "Choose the evaluation cutoff before tuning. This command cannot establish that a window was held out.",
            "A frozen timeline does not freeze imported source records; keep the input database immutable during a run.",
        ],
    }
