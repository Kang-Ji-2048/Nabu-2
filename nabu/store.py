"""Canonical store and the one scoped query layer every agent reads through.

Ring-fencing and backtest safety live here, not in prompts:
- every read filters on tenant_id and the caller's scopes;
- every read is cut off at `as_of`: later items are invisible and later
  outcomes (resolution, re-estimates, links) are masked.
"""

import json
import re
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS work_item (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, scope TEXT NOT NULL,
    source TEXT NOT NULL, source_key TEXT NOT NULL,
    type TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
    parent_id TEXT, parent_at TEXT, components TEXT NOT NULL DEFAULT '[]', labels TEXT NOT NULL DEFAULT '[]',
    estimate REAL, estimate_unit TEXT, status_category TEXT NOT NULL,
    created_at TEXT NOT NULL, started_at TEXT, resolved_at TEXT, due_at TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS work_item_scope ON work_item(tenant_id, scope, created_at);
CREATE INDEX IF NOT EXISTS work_item_parent ON work_item(parent_id);
CREATE TABLE IF NOT EXISTS link (
    from_id TEXT NOT NULL, to_id TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT,
    tenant_id TEXT NOT NULL, scope TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, kind)
);
CREATE TABLE IF NOT EXISTS event (
    item_id TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL,
    from_value TEXT, to_value TEXT, tenant_id TEXT NOT NULL, scope TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS event_item ON event(item_id, at);

-- Agent state. Everything below is written by the replay and keyed by run.
CREATE TABLE IF NOT EXISTS run (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, scopes TEXT NOT NULL,
    viewer_scopes TEXT NOT NULL, cursor INTEGER NOT NULL, clock TEXT,
    status TEXT NOT NULL, stats TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS pattern (
    id TEXT NOT NULL, run_id TEXT NOT NULL, scope TEXT NOT NULL, kind TEXT NOT NULL,
    key TEXT NOT NULL, name TEXT NOT NULL, description TEXT, evidence TEXT NOT NULL,
    stat REAL, weight REAL NOT NULL DEFAULT 1.0, updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS warning (
    id TEXT NOT NULL, run_id TEXT NOT NULL, epic_id TEXT NOT NULL, as_of TEXT NOT NULL,
    agent TEXT NOT NULL, pattern_id TEXT, claim TEXT NOT NULL, evidence TEXT NOT NULL,
    suggested_ticket TEXT, prediction TEXT NOT NULL, confidence TEXT,
    status TEXT NOT NULL, reason TEXT,
    PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS review (
    run_id TEXT NOT NULL, epic_id TEXT NOT NULL, as_of TEXT NOT NULL, report TEXT NOT NULL,
    PRIMARY KEY (run_id, epic_id)
);
CREATE TABLE IF NOT EXISTS score (
    run_id TEXT NOT NULL, warning_id TEXT NOT NULL, at TEXT NOT NULL,
    hit INTEGER NOT NULL, detail TEXT,
    PRIMARY KEY (run_id, warning_id)
);
CREATE TABLE IF NOT EXISTS agent_log (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, sim_at TEXT, agent TEXT NOT NULL,
    kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT,
    PRIMARY KEY (run_id, seq)
);
"""

ITEM_FIELDS = ("id", "tenant_id", "scope", "source", "source_key", "type", "title", "description",
               "parent_id", "parent_at", "components", "labels", "estimate", "estimate_unit", "status_category",
               "created_at", "started_at", "resolved_at", "due_at", "raw")

STOPWORDS = {"the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with", "add", "new",
             "update", "support", "make", "use", "from", "into", "by", "is", "be", "as", "at"}


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def load_jsonl(conn, path) -> dict:
    """Load canonical records: one {"table": "work_item"|"link"|"event", "row": {...}} per line.

    Whichever ETL feeds Nabu (ported or rewritten) only has to emit this format.
    """
    counts = {"work_item": 0, "link": 0, "event": 0}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            table, row = record["table"], dict(record["row"])
            if table == "work_item":
                for key in ("components", "labels"):
                    row[key] = json.dumps(row.get(key) or [])
                if isinstance(row.get("raw"), (dict, list)):
                    row["raw"] = json.dumps(row["raw"])
                values = [row.get(field) for field in ITEM_FIELDS]
                conn.execute(f"INSERT OR REPLACE INTO work_item VALUES ({','.join('?' * len(values))})", values)
            elif table == "link":
                conn.execute("INSERT OR REPLACE INTO link VALUES (?,?,?,?,?,?)",
                             (row["from_id"], row["to_id"], row["kind"], row.get("created_at"), row["tenant_id"], row["scope"]))
            elif table == "event":
                conn.execute("INSERT INTO event VALUES (?,?,?,?,?,?,?)",
                             (row["item_id"], row["at"], row["kind"], row.get("from_value"), row.get("to_value"), row["tenant_id"], row["scope"]))
            else:
                raise ValueError(f"Unknown table {table!r}")
            counts[table] += 1
    conn.commit()
    return counts


def tokens(text) -> set:
    return {word for word in re.findall(r"[a-z][a-z0-9]+", (text or "").lower()) if word not in STOPWORDS}


def similarity(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def days_between(start, end) -> float:
    from datetime import datetime
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parse(end) - parse(start)).total_seconds() / 86400


class ScopedStore:
    """Read-only view of one tenant, limited to `scopes`, as known at `as_of`."""

    def __init__(self, conn, tenant_id: str, scopes, as_of: str):
        self.conn, self.tenant_id, self.scopes, self.as_of = conn, tenant_id, list(scopes), as_of

    def at(self, as_of: str) -> "ScopedStore":
        return ScopedStore(self.conn, self.tenant_id, self.scopes, as_of)

    def _where(self, alias=""):
        prefix = alias + "." if alias else ""
        marks = ",".join("?" * len(self.scopes))
        return f"{prefix}tenant_id=? AND {prefix}scope IN ({marks})", [self.tenant_id, *self.scopes]

    def _mask(self, row) -> dict:
        item = dict(row)
        item["components"] = json.loads(item["components"])
        item["labels"] = json.loads(item["labels"])
        item.pop("raw", None)
        if item["started_at"] and item["started_at"] > self.as_of:
            item["started_at"] = None
        if item["resolved_at"] and item["resolved_at"] > self.as_of:
            item["resolved_at"] = None
            item["status_category"] = "in_progress" if item["started_at"] else "todo"
        # The estimate as it stood at as_of: undo any later re-estimates.
        later = self.conn.execute(
            "SELECT from_value FROM event WHERE item_id=? AND kind='estimate_changed' AND at>? ORDER BY at LIMIT 1",
            (item["id"], self.as_of)).fetchone()
        if later is not None:
            item["estimate"] = float(later[0]) if later[0] not in (None, "") else None
        return item

    def items(self, **filters) -> list:
        where, params = self._where()
        sql = f"SELECT * FROM work_item WHERE {where} AND created_at<=?"
        params.append(self.as_of)
        for key, value in filters.items():
            sql += f" AND {key}=?"
            params.append(value)
        return [self._mask(row) for row in self.conn.execute(sql + " ORDER BY created_at", params)]

    def get(self, item_id):
        where, params = self._where()
        row = self.conn.execute(f"SELECT * FROM work_item WHERE id=? AND {where} AND created_at<=?",
                                [item_id, *params, self.as_of]).fetchone()
        return self._mask(row) if row else None

    def can_see(self, item_id) -> bool:
        return self.get(item_id) is not None

    def children(self, epic_id) -> list:
        """Items in the epic at as_of. parent_at is when the item joined the epic (default: created)."""
        return [i for i in self.items(parent_id=epic_id) if joined(i) <= self.as_of]

    def completed(self, type_=None) -> list:
        return [item for item in self.items(**({"type": type_} if type_ else {}))
                if item["resolved_at"] and item["status_category"] == "done"]

    def links(self, item_id) -> list:
        where, params = self._where()
        rows = self.conn.execute(
            f"SELECT * FROM link WHERE (from_id=? OR to_id=?) AND {where} AND (created_at IS NULL OR created_at<=?)",
            [item_id, item_id, *params, self.as_of])
        return [dict(row) for row in rows if self.can_see(row["from_id"]) and self.can_see(row["to_id"])]

    def similar(self, text, candidates, k=5, threshold=0.15) -> list:
        query = tokens(text)
        scored = [(similarity(query, tokens(c["title"] + " " + (c["description"] or ""))), c) for c in candidates]
        return [c for score, c in sorted(scored, key=lambda pair: -pair[0]) if score >= threshold][:k]


def joined(item) -> str:
    return item.get("parent_at") or item["created_at"]


def duration(item) -> float | None:
    if not item.get("resolved_at"):
        return None
    return days_between(item["started_at"] or item["created_at"], item["resolved_at"])
