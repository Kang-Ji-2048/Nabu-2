"""Atomic ingestion for the public library boundary."""

from dataclasses import fields
import json
import math
from collections.abc import Mapping

from .errors import ScopeError, ValidationError
from .models import WorkItem, scopes_tuple, timestamp
from .store import ITEM_FIELDS


def _text(row, key):
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} must be a non-empty string")
    return value


def load_records(conn, records) -> dict[str, int]:
    counts = {"work_item": 0, "link": 0, "event": 0}
    references = []
    item_fields = {f.name for f in fields(WorkItem)}
    with conn:
        for record in records:
            if isinstance(record, WorkItem):
                record = record.to_record()
            if not isinstance(record, Mapping) or set(record) != {"table", "row"} or not isinstance(record["row"], Mapping):
                raise ValidationError('Each record must be a WorkItem or {"table": ..., "row": {...}}')
            table, row = record["table"], dict(record["row"])
            if not isinstance(table, str) or table not in counts:
                raise ValidationError("Unknown canonical table")
            for key in ("tenant_id", "scope"):
                _text(row, key)
            if table == "work_item":
                if set(row) - item_fields:
                    raise ValidationError(f"Unknown work item fields: {sorted(set(row) - item_fields)}")
                try:
                    row = WorkItem(**row).to_record()["row"]
                except TypeError as exc:
                    raise ValidationError("Work item is missing required fields") from exc
                for key in ("id", "title", "type", "source", "source_key"):
                    _text(row, key)
                if row["status_category"] not in {"todo", "in_progress", "done", "cancelled"}:
                    raise ValidationError("Invalid status_category")
                for key in ("description", "parent_id", "estimate_unit"):
                    if row[key] is not None and not isinstance(row[key], str):
                        raise ValidationError(f"{key} must be a string or None")
                for key in ("created_at", "parent_at", "started_at", "resolved_at", "due_at"):
                    if row[key] is not None:
                        row[key] = timestamp(row[key])
                if row["created_at"] is None:
                    raise ValidationError("created_at is required")
                for key in ("started_at", "resolved_at"):
                    if row[key] and row[key] < row["created_at"]:
                        raise ValidationError(f"{key} precedes created_at")
                if row["resolved_at"] and row["started_at"] and row["resolved_at"] < row["started_at"]:
                    raise ValidationError("resolved_at precedes started_at")
                estimate = row["estimate"]
                if estimate is not None and (isinstance(estimate, bool) or not isinstance(estimate, (int, float))
                                             or not math.isfinite(estimate) or estimate < 0):
                    raise ValidationError("estimate must be a finite non-negative number or None")
                for key in ("components", "labels"):
                    row[key] = json.dumps(scopes_tuple(row[key]))
                try:
                    row["raw"] = json.dumps(row["raw"], allow_nan=False) if row["raw"] is not None else None
                except (TypeError, ValueError) as exc:
                    raise ValidationError("raw must be JSON serializable") from exc
                existing = conn.execute("SELECT tenant_id FROM work_item WHERE id=?", (row["id"],)).fetchone()
                if existing and existing[0] != row["tenant_id"]:
                    raise ScopeError("Work item ID already belongs to another tenant; namespace IDs")
                conn.execute(f"INSERT OR REPLACE INTO work_item VALUES ({','.join('?' for _ in ITEM_FIELDS)})",
                             [row.get(key) for key in ITEM_FIELDS])
                if row["parent_id"]:
                    references.append((row["parent_id"], row["tenant_id"], None, False))
            elif table == "link":
                allowed = {"from_id", "to_id", "kind", "created_at", "tenant_id", "scope"}
                if set(row) - allowed:
                    raise ValidationError("Unknown link fields")
                for key in ("from_id", "to_id", "kind"):
                    _text(row, key)
                at = timestamp(row["created_at"]) if row.get("created_at") is not None else None
                conn.execute("INSERT OR REPLACE INTO link VALUES (?,?,?,?,?,?)",
                             (row["from_id"], row["to_id"], row["kind"], at, row["tenant_id"], row["scope"]))
                references.extend((row[key], row["tenant_id"], None, True) for key in ("from_id", "to_id"))
            else:
                allowed = {"item_id", "at", "kind", "from_value", "to_value", "tenant_id", "scope"}
                if set(row) - allowed:
                    raise ValidationError("Unknown event fields")
                for key in ("item_id", "kind"):
                    _text(row, key)
                for key in ("from_value", "to_value"):
                    if row.get(key) is not None and not isinstance(row[key], (str, int, float)):
                        raise ValidationError("Event values must be strings, numbers or None")
                if row["kind"] == "estimate_changed":
                    for key in ("from_value", "to_value"):
                        if row.get(key) not in (None, ""):
                            try:
                                value = float(row[key])
                                if not math.isfinite(value) or value < 0:
                                    raise ValueError()
                            except (TypeError, ValueError) as exc:
                                raise ValidationError("Estimate-change values must be finite non-negative numbers") from exc
                conn.execute("INSERT INTO event VALUES (?,?,?,?,?,?,?)",
                             (row["item_id"], timestamp(row.get("at")), row["kind"], row.get("from_value"),
                              row.get("to_value"), row["tenant_id"], row["scope"]))
                references.append((row["item_id"], row["tenant_id"], row["scope"], True))
            counts[table] += 1
        for item_id, tenant, scope, required in references:
            item = conn.execute("SELECT tenant_id, scope FROM work_item WHERE id=?", (item_id,)).fetchone()
            if item is None:
                if required:
                    raise ValidationError(f"Referenced work item {item_id!r} was not imported")
            elif item[0] != tenant or (scope is not None and item[1] != scope):
                raise ScopeError("A reference crosses a tenant or event scope boundary")
    return counts
