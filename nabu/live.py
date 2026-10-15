"""Review a new epic typed into the interface, against everything the store knows.

The epic and its tickets are added to the store as if just created in the tracker, the historian
mines the full history, and the review team runs with live Claude calls. Each agent log row is
committed as it happens, so the interface lights up step by step.
"""

import json
from datetime import datetime, timezone

from .agents.historian import Historian
from .llm import LLM, Log
from .review import ReviewTeam
from .store import ScopedStore, connect

RUN = "live"


class LiveLog(Log):
    def emit(self, *args, **kwargs):
        super().emit(*args, **kwargs)
        self.conn.commit()


def submit(db, plan: dict, offline=None) -> str:
    """plan = {"title", "description", "tickets": [{"title", "estimate", "component"}]}. Returns the epic id."""
    conn = connect(db)
    run = conn.execute("SELECT tenant_id, scopes FROM run WHERE id != ? ORDER BY rowid DESC LIMIT 1", (RUN,)).fetchone()
    tenant, scope = (run["tenant_id"], json.loads(run["scopes"])[0]) if run else ("demo", "payments")
    if not conn.execute("SELECT 1 FROM run WHERE id=?", (RUN,)).fetchone():
        conn.execute("INSERT INTO run VALUES (?,?,?,?,?,?,?,?)", (RUN, tenant, json.dumps([scope]), json.dumps([scope]),
                                                                0, None, "live", json.dumps({"reviews": 0, "hits": 0, "misses": 0, "accuracy": []})))
    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    n = conn.execute("SELECT COUNT(*) FROM work_item WHERE source='live' AND type='epic'").fetchone()[0] + 1
    key = f"NEW-{n}"
    epic_id = f"{tenant}:{key}"
    rows = [(epic_id, key, "epic", plan["title"], plan.get("description"), None, [], None)]
    for i, t in enumerate(plan.get("tickets", []), 1):
        comp = [t["component"]] if t.get("component") else []
        rows.append((f"{epic_id}-{i}", f"{key}-{i}", "story", t["title"], None, epic_id, comp, t.get("estimate")))
    for item_id, k, type_, title, desc, parent, comps, est in rows:
        conn.execute("INSERT OR REPLACE INTO work_item VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (item_id, tenant, scope, "live", k, type_, title, desc, parent, as_of if parent else None,
                      json.dumps(comps), "[]", est, "points" if est is not None else None, "todo", as_of,
                      None, None, None, None))
    conn.execute("UPDATE run SET clock=? WHERE id=?", (as_of, RUN))
    conn.commit()

    log = LiveLog(conn, RUN, quiet=True)
    log.sim_at = as_of
    llm = LLM(log, offline=offline)
    store = ScopedStore(conn, tenant, [scope], as_of)
    log.emit("replay", "start", f"new epic {key} '{plan['title']}' with {len(rows) - 1} tickets")
    Historian(llm, log).refresh(conn, RUN, store)
    ReviewTeam(llm, log).review(conn, RUN, store.get(epic_id), store, store)
    stats = json.loads(conn.execute("SELECT stats FROM run WHERE id=?", (RUN,)).fetchone()[0])
    stats["reviews"] += 1
    conn.execute("UPDATE run SET stats=? WHERE id=?", (json.dumps(stats), RUN))
    log.emit("replay", "finished", f"review of {key} published ({llm.calls} Claude calls)")
    conn.commit()
    return epic_id
