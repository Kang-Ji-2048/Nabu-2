"""Typed values for the public API. No optional dependencies are imported here."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any

from .errors import ValidationError


def timestamp(value: str | datetime) -> str:
    """Normalize an aware ISO-8601 timestamp to UTC, preserving microseconds."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("timezone required")
        return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValidationError("Expected an ISO-8601 timestamp with a timezone") from exc


def scopes_tuple(scopes) -> tuple[str, ...]:
    if isinstance(scopes, (str, bytes)):
        raise ValidationError("Scopes must be a sequence, not a comma-separated string")
    try:
        result = tuple(dict.fromkeys(scopes))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Scopes must be a sequence of strings") from exc
    if any(not isinstance(scope, str) or not scope.strip() for scope in result):
        raise ValidationError("Each scope must be a non-empty string")
    return result


@dataclass(frozen=True)
class Viewer:
    """Caller-declared tenant and scopes; authenticate the caller in your application."""
    tenant: str
    scopes: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.tenant, str) or not self.tenant.strip():
            raise ValidationError("Viewer tenant must be a non-empty string")
        object.__setattr__(self, "scopes", scopes_tuple(self.scopes))


@dataclass(frozen=True)
class WorkItem:
    """A canonical ticket or epic. IDs must be globally unique in a database."""
    id: str
    tenant_id: str
    scope: str
    title: str
    created_at: str | datetime
    type: str = "task"
    source: str = "custom"
    source_key: str | None = None
    description: str | None = None
    parent_id: str | None = None
    parent_at: str | datetime | None = None
    components: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    estimate: float | None = None
    estimate_unit: str | None = None
    status_category: str = "todo"
    started_at: str | datetime | None = None
    resolved_at: str | datetime | None = None
    due_at: str | datetime | None = None
    raw: Any = None

    def to_record(self) -> dict:
        row = asdict(self)
        row["source_key"] = self.source_key or self.id
        return {"table": "work_item", "row": row}


@dataclass(frozen=True)
class Forecast:
    p50_days: float | None
    p90_days: float | None
    basis: tuple[str, ...] = ()


@dataclass(frozen=True)
class Warning:
    id: str
    agent: str
    claim: str
    evidence: tuple[str, ...]
    confidence: str
    suggested_ticket: str | None = None


@dataclass(frozen=True)
class ReviewReport:
    run_id: str
    epic: str
    title: str
    as_of: str
    summary: str
    forecast: Forecast
    warnings: tuple[Warning, ...]
    missing_tickets: tuple[str, ...] = ()
    suppressed_by_ring_fence: int = 0

    @classmethod
    def from_dict(cls, run_id: str, data: dict) -> "ReviewReport":
        forecast = data["forecast"]
        return cls(run_id, data["epic"], data["title"], data["as_of"], data["summary"],
                   Forecast(forecast["p50_days"], forecast["p90_days"], tuple(forecast["basis"])),
                   tuple(Warning(**{**w, "evidence": tuple(w["evidence"])}) for w in data["warnings"]),
                   tuple(data["missing_tickets"]), data["suppressed_by_ring_fence"])

    def to_dict(self) -> dict:
        """Return JSON-compatible values, including lists rather than tuples."""
        return json.loads(self.to_json())

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(asdict(self), indent=indent, ensure_ascii=False, allow_nan=False)

    def to_markdown(self) -> str:
        lines = [f"# {self.epic}: {self.title}", "", f"As of {self.as_of}", "", self.summary, ""]
        for warning in self.warnings:
            lines += [f"- **{warning.agent} ({warning.confidence}):** {warning.claim}",
                      f"  Evidence: {', '.join(warning.evidence)}"]
        if self.missing_tickets:
            lines += ["", "Suggested work:", "", *[f"- {ticket}" for ticket in self.missing_tickets]]
        if self.forecast.p50_days is not None:
            lines += ["", f"Historical duration: P50 {self.forecast.p50_days} days; "
                      f"P90 {self.forecast.p90_days} days. These are not calibrated delivery guarantees."]
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    cursor: int
    clock: str | None
    reviews: int
    hits: int
    misses: int
    model_calls: int = 0

    @property
    def warning_confirmation_rate(self) -> float | None:
        count = self.hits + self.misses
        return self.hits / count if count else None
