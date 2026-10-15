"""Synthetic tracker history for testing and rehearsing the replay. Not real data.

Two scopes in one tenant, with patterns planted so every agent has something to find:
- 'database' work takes about 2.5x the usual time per point (estimator);
- payments 'api' work is often blocked by platform 'auth' work, across the scope
  wall (dependency hunter, and ring-fence suppression for a payments-only reader);
- testing and documentation tickets are usually added after the epic starts (gap finder).
"""

import json
import random
from datetime import datetime, timedelta

FEATURES = {
    "payments": ["refunds", "payouts", "invoices", "disputes", "subscriptions", "multi currency",
                 "chargebacks", "receipts", "settlements", "fraud rules"],
    "platform": ["login", "tenancy", "rate limits", "feature flags", "audit trail", "sso",
                 "api keys", "billing sync", "search", "notifications"],
}
TEMPLATES = {
    "database": ["Ledger table for {f}", "Index {f} by merchant", "Store {f} history"],
    "api": ["Endpoint for {f}", "Webhook on {f} status", "Client library calls for {f}"],
    "frontend": ["Page to manage {f}", "Form to create {f}", "Show {f} in account view"],
    "auth": ["Token scopes for {f}", "Role checks for {f}"],
}


def generate(tenant="demo", seed=7, epics_per_scope=10, start="2023-01-02"):
    rng = random.Random(seed)
    records, counter = [], [0]
    begin = datetime.fromisoformat(start)
    iso = lambda moment: moment.isoformat() + "Z"

    def item(scope, kind, title, created, days, estimate=None, parent=None, component=None):
        counter[0] += 1
        key = f"{scope[:3].upper()}-{counter[0]}"
        started = created + timedelta(days=rng.uniform(0, 2))
        row = {"id": f"{tenant}:{key}", "tenant_id": tenant, "scope": scope, "source": "synthetic",
               "source_key": key, "type": kind, "title": title, "description": None, "parent_id": parent,
               "components": [component] if component else [], "labels": [], "estimate": estimate,
               "estimate_unit": "points" if estimate else None, "status_category": "done",
               "created_at": iso(created), "started_at": iso(started), "resolved_at": iso(started + timedelta(days=days))}
        records.append({"table": "work_item", "row": row})
        return row

    blockers = []
    for scope in ("platform", "payments"):  # platform first so payments work can be blocked by it
        for n, feature in enumerate(FEATURES[scope][:epics_per_scope]):
            created = begin + timedelta(days=30 * n + rng.uniform(0, 5))
            epic = item(scope, "epic", f"{feature.capitalize()} {'v2' if n % 3 == 2 else ''}".strip(), created, 0)
            children = []
            components = ["database", "api", "frontend"] + (["auth"] if scope == "platform" else [])
            for component in rng.sample(components, k=min(len(components), rng.randint(3, 4))):
                estimate = rng.choice([1, 2, 3, 5, 8])
                pace = 2.5 if component == "database" else rng.uniform(0.7, 1.2)
                title = rng.choice(TEMPLATES[component]).format(f=feature)
                child = item(scope, "story", title, created, estimate * pace, estimate, epic["id"], component)
                children.append(child)
                if component == "auth":
                    blockers.append(child)
                if scope == "payments" and component == "api" and blockers and rng.random() < 0.8:
                    blocker = rng.choice(blockers[-4:])
                    records.append({"table": "link", "row": {
                        "from_id": blocker["id"], "to_id": child["id"], "kind": "blocks", "tenant_id": tenant,
                        "scope": scope, "created_at": iso(created + timedelta(days=3))}})
            # Work the plan forgot, discovered after the epic started.
            for category, title, chance in (("testing", "Regression tests for {f}", 0.8),
                                            ("documentation", "Write runbook for {f}", 0.6)):
                if rng.random() < chance:
                    late = created + timedelta(days=rng.uniform(6, 12))
                    children.append(item(scope, "task", title.format(f=feature), late, rng.uniform(2, 4), 2, epic["id"]))
            epic["started_at"] = epic["created_at"]
            epic["resolved_at"] = max(c["resolved_at"] for c in children)
    return records


def write(path, **kwargs) -> int:
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    records = generate(**kwargs)
    with open(path, "w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")
    return len(records)
