"""The long run: replay a tenant's history on a simulated clock, with checkpoint and resume.

The timeline has two kinds of step, in time order:
- review: an epic appeared; the historian refreshes, then the team reviews it
  using only what was known one day after it was created;
- score: an epic finished; the scorekeeper grades the published warnings.

Each step's writes and the cursor commit in one transaction, so a crash or
Ctrl-C loses at most the step in progress and `resume` carries on from there.
"""

import json
import time
from datetime import datetime, timedelta

from .agents.historian import Historian
from .agents.writer import Scorekeeper
from .llm import LLM, Log
from .review import ReviewTeam
from .store import ScopedStore

END_OF_TIME = "9999-12-31T00:00:00Z"


def plus_days(timestamp, days) -> str:
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00")) + timedelta(days=days)
    return moment.isoformat().replace("+00:00", "Z")


def timeline(conn, tenant_id, scopes, review_after_days=1.0) -> list:
    """(sim time, kind, epic id) for every epic in scope. Built from the full record; agents never see it."""
    everything = ScopedStore(conn, tenant_id, scopes, END_OF_TIME)
    steps = []
    for epic in everything.items(type="epic"):
        steps.append((plus_days(epic["created_at"], review_after_days), "review", epic["id"]))
        if epic["resolved_at"]:
            steps.append((epic["resolved_at"], "score", epic["id"]))
    return sorted(steps)


def start(conn, run_id, tenant_id, scopes, viewer_scopes=None):
    if conn.execute("SELECT 1 FROM run WHERE id=?", (run_id,)).fetchone():
        raise ValueError(f"Run {run_id!r} already exists; use resume")
    conn.execute("INSERT INTO run VALUES (?,?,?,?,?,?,?,?)",
                 (run_id, tenant_id, json.dumps(scopes), json.dumps(viewer_scopes or scopes), 0, None, "running",
                  json.dumps({"reviews": 0, "hits": 0, "misses": 0, "accuracy": []})))
    conn.commit()


def run(conn, run_id, *, offline=None, stop_after=None, quiet=False, pace=0.0) -> dict:
    row = conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"No run {run_id!r}")
    tenant_id, scopes, viewer_scopes = row["tenant_id"], json.loads(row["scopes"]), json.loads(row["viewer_scopes"])
    stats = json.loads(row["stats"])
    log = Log(conn, run_id, quiet=quiet)
    llm = LLM(log, offline=offline)
    historian, team, scorekeeper = Historian(llm, log), ReviewTeam(llm, log), Scorekeeper(log)
    steps = timeline(conn, tenant_id, viewer_scopes)  # only epics the reader can see get reviewed
    cursor = row["cursor"]
    if cursor:
        log.emit("replay", "resume", f"resuming at step {cursor + 1} of {len(steps)}")
    else:
        log.emit("replay", "start", f"{len(steps)} steps for tenant {tenant_id}, scopes {scopes}")
    done_now = 0
    while cursor < len(steps):
        if stop_after is not None and done_now >= stop_after:
            log.emit("replay", "paused", f"stopped after {done_now} steps (checkpoint at step {cursor + 1})")
            conn.commit()
            return stats
        at, kind, epic_id = steps[cursor]
        log.sim_at = at
        store = ScopedStore(conn, tenant_id, scopes, at)
        epic = store.get(epic_id)
        if kind == "review":
            historian.refresh(conn, run_id, store)
            team.review(conn, run_id, epic, store, ScopedStore(conn, tenant_id, viewer_scopes, at))
            stats["reviews"] += 1
        else:
            tally = scorekeeper.score(conn, run_id, epic, store)
            stats["hits"] += tally["hits"]
            stats["misses"] += tally["misses"]
            graded = stats["hits"] + stats["misses"]
            if graded:
                stats["accuracy"].append([at, round(stats["hits"] / graded, 3)])
                log.emit("replay", "accuracy", f"{stats['hits']}/{graded} warnings confirmed so far "
                                               f"({stats['hits'] / graded:.0%})")
        cursor += 1
        done_now += 1
        conn.execute("UPDATE run SET cursor=?, clock=?, stats=?, status=? WHERE id=?",
                     (cursor, at, json.dumps(stats), "running" if cursor < len(steps) else "finished", run_id))
        conn.commit()  # checkpoint: this step's warnings, scores, patterns and log rows land together
        time.sleep(pace)  # slows an offline replay down so the interface can be watched
    log.emit("replay", "finished", f"{stats['reviews']} epics reviewed, {stats['hits']} hits, {stats['misses']} misses, "
                                   f"{llm.calls} Claude calls")
    conn.commit()
    return stats
