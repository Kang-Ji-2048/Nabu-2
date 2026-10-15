"""Frozen replay inventory and committed coverage, separate from agent inputs.

The census deliberately sees the whole imported dataset. It is presentation
metadata only: agents continue to read through their time-fenced ScopedStore.
Raw census identifiers and the frozen schedule never leave public_state().
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .store import ScopedStore


def visible_scopes(scopes, viewer_scopes):
    return sorted(set(scopes) & set(viewer_scopes))


def capture(conn, tenant_id, scopes, viewer_scopes, source="run_start"):
    allowed = visible_scopes(scopes, viewer_scopes)
    marks = ",".join("?" * len(allowed))
    rows = conn.execute(
        "SELECT id, scope, source, type, parent_id, parent_at, created_at, resolved_at "
        f"FROM work_item WHERE tenant_id=? AND scope IN ({marks}) ORDER BY id",
        (tenant_id, *allowed))
    items = {row["id"]: dict(row) for row in rows}
    steps = []
    for item in items.values():
        if item["type"] != "epic":
            continue
        review_at = (datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                     + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        steps.append({"at": review_at, "kind": "review", "epic_id": item["id"], "scope": item["scope"]})
        if item["resolved_at"]:
            steps.append({"at": item["resolved_at"], "kind": "score", "epic_id": item["id"],
                          "scope": item["scope"]})
    steps.sort(key=lambda step: (step["at"], step["kind"], step["epic_id"]))
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") if source == "run_start" else None
    return {"version": 1, "captured_at": captured_at,
            "source": source, "scopes": allowed, "items": items, "steps": steps,
            "done": {"epics": [], "tickets": [], "steps": 0}, "checkpoints": []}


def save(conn, run_id, state):
    conn.execute("INSERT OR REPLACE INTO replay_progress (run_id, state) VALUES (?,?)",
                 (run_id, json.dumps(state)))


def _read(conn, run_id):
    try:
        row = conn.execute("SELECT state FROM replay_progress WHERE run_id=?", (run_id,)).fetchone()
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error):
            raise
        return None
    return json.loads(row["state"]) if row else None


def advance(state, step, plan=(), reviewed=False):
    """Record one successful step, including score steps with zero warnings.

    Coverage counts only baseline non-epic tickets in the actual review plan.
    A ticket that appeared after the census cannot inflate a fixed denominator.
    """
    tickets = set()
    if reviewed and step["epic_id"] in state["items"]:
        state["done"]["epics"] = sorted(set(state["done"]["epics"]) | {step["epic_id"]})
        tickets = {item["id"] for item in plan
                   if item["id"] in state["items"] and state["items"][item["id"]]["type"] != "epic"}
        state["done"]["tickets"] = sorted(set(state["done"]["tickets"]) | tickets)
    state["done"]["steps"] += 1
    # Keep review provenance: a visible ticket covered only by a now-hidden
    # epic must not disclose that hidden review through aggregate counters.
    state["checkpoints"].append({"at": step["at"], "scope": step["scope"],
                                 "reviewed_epic": step["epic_id"] if reviewed else None,
                                 "tickets": sorted(tickets)})


def reconstruct(conn, run):
    """Best-effort census for a legacy run, explicitly labelled as reconstructed.

    Legacy rows did not preserve a start-time inventory or historical plans.
    Use the current imported records, committed cursor and stored review times;
    created_at/parent_at still prevent counting later tickets as day-one work.
    """
    run = dict(run)
    scopes, viewer_scopes = json.loads(run["scopes"]), json.loads(run["viewer_scopes"])
    state = capture(conn, run["tenant_id"], scopes, viewer_scopes, "legacy_reconstructed")
    reviews = {row["epic_id"]: row["as_of"] for row in conn.execute(
        "SELECT epic_id, as_of FROM review WHERE run_id=?", (run["id"],))}
    for step in state["steps"][:min(run["cursor"], len(state["steps"]))]:
        if not run["clock"] or step["at"] > run["clock"]:
            break
        as_of = reviews.get(step["epic_id"]) if step["kind"] == "review" else None
        if as_of and as_of > run["clock"]:
            as_of = None
        plan = ScopedStore(conn, run["tenant_id"], state["scopes"], as_of).children(step["epic_id"]) if as_of else []
        advance(state, step, plan, reviewed=as_of is not None)
    return state


def ensure(conn, run):
    """Load the frozen state, or freeze a reconstruction before legacy resume."""
    state = _read(conn, run["id"])
    if state is None:
        state = reconstruct(conn, run)
        save(conn, run["id"], state)
    return state


def _inventory(state, allowed):
    items = {key: value for key, value in state["items"].items() if value["scope"] in allowed}
    epics = {key for key, value in items.items() if value["type"] == "epic"}
    by_scope = []
    for scope in sorted(allowed):
        scoped = [item for item in items.values() if item["scope"] == scope]
        tickets = [item for item in scoped if item["type"] != "epic"]
        linked = sum(item["parent_id"] in epics for item in tickets)
        by_scope.append({"scope": scope, "work_items": len(scoped), "epics": len(scoped) - len(tickets),
                         "tickets": len(tickets), "linked_tickets": linked, "unlinked_tickets": len(tickets) - linked})
    totals = {key: sum(scope[key] for scope in by_scope)
              for key in ("work_items", "epics", "tickets", "linked_tickets", "unlinked_tickets")}
    return {**totals, "by_scope": by_scope, "captured_at": state["captured_at"], "source": state["source"],
            "sources": sorted({item["source"] for item in items.values()})}


def _measure(done, total):
    percent = round(100 * done / total, 1) if total else 0.0
    if done < total:
        percent = min(percent, 99.9)
    return {"done": done, "total": total, "percent": percent}


def _public_progress(state, inventory, counts, step_totals):
    by_scope = []
    for scope in inventory["by_scope"]:
        name = scope["scope"]
        done = counts.get(name, {})
        steps = step_totals.get(name, 0)
        by_scope.append({"scope": name, "epics": _measure(done.get("epics_done", 0), scope["epics"]),
                         "tickets": _measure(done.get("tickets_done", 0), scope["tickets"]),
                         "steps": _measure(done.get("steps_done", 0), steps)})
    totals = {key: _measure(sum(row[key]["done"] for row in by_scope),
                            sum(row[key]["total"] for row in by_scope)) for key in ("epics", "tickets", "steps")}
    return {**totals, "by_scope": by_scope}


def public_state(conn, run):
    """Read-only, identifier-free census, progress and committed checkpoints.

    Filter again against the run's current permitted scopes before serialization,
    even when a persisted census was originally captured with broader access.
    """
    run = dict(run)
    state = _read(conn, run["id"]) or reconstruct(conn, run)
    allowed = set(visible_scopes(json.loads(run["scopes"]), json.loads(run["viewer_scopes"]))) & set(state["scopes"])
    inventory = _inventory(state, allowed)
    step_totals = {scope: 0 for scope in allowed}
    for step in state["steps"]:
        if step["scope"] in allowed:
            step_totals[step["scope"]] += 1
    counts = {scope: {"epics_done": 0, "tickets_done": 0, "steps_done": 0} for scope in allowed}
    seen_epics, seen_tickets, checkpoints = set(), set(), []
    for checkpoint in state["checkpoints"]:
        scope = checkpoint["scope"]
        if scope not in allowed:
            continue
        counts[scope]["steps_done"] += 1
        epic_id = checkpoint["reviewed_epic"]
        if epic_id and epic_id not in seen_epics:
            seen_epics.add(epic_id)
            counts[scope]["epics_done"] += 1
        for item_id in checkpoint["tickets"]:
            item_scope = state["items"][item_id]["scope"]
            if item_scope in allowed and item_id not in seen_tickets:
                seen_tickets.add(item_id)
                counts[item_scope]["tickets_done"] += 1
        checkpoints.append({"at": checkpoint["at"],
                            "progress": _public_progress(state, inventory, counts, step_totals)})
    return {"inventory": inventory, "progress": _public_progress(state, inventory, counts, step_totals),
            "checkpoints": checkpoints}
