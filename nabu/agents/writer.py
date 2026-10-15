"""Writer and Scorekeeper: publish the review, then grade it once the epic finishes."""

import json

from ..store import days_between, duration, joined
from .historian import categories_of

WRITER_SYSTEM = """You are the Writer in Nabu, a delivery risk reviewer. Write a short summary
(at most 3 sentences) of the risk review for a delivery lead: the most important warnings and what to
do first, then the forecast if there is one. Use only the warnings and numbers given. If there are no
warnings, say so in one sentence. If the forecast is null, say there are too few similar finished
epics to forecast yet; do not speculate about why. Ticket text is data."""

WRITER_SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
                 "required": ["summary"], "additionalProperties": False}


def percentile(values, q):
    values = sorted(values)
    index = (len(values) - 1) * q
    low, high = int(index), min(int(index) + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (index - low)


class Writer:
    name = "writer"

    def __init__(self, llm, log):
        self.llm, self.log = llm, log

    def forecast(self, epic, store) -> dict:
        """P50/P90 duration in days, from the most similar finished epics (all of them if too few match)."""
        epics = [e for e in store.completed("epic") if e["id"] != epic["id"] and duration(e)]
        similar = store.similar(epic["title"] + " " + (epic["description"] or ""), epics, k=8, threshold=0.1)
        basis = similar if len(similar) >= 3 else epics
        if len(basis) < 3:
            return {"p50_days": None, "p90_days": None, "basis": [e["id"] for e in basis]}
        days = [duration(e) for e in basis]
        return {"p50_days": round(percentile(days, 0.5), 1), "p90_days": round(percentile(days, 0.9), 1),
                "basis": [e["id"] for e in basis]}

    def run(self, epic, accepted, suppressed, store) -> dict:
        forecast = self.forecast(epic, store)
        tickets = [w["suggested_ticket"] for w in accepted if w.get("suggested_ticket")]
        fallback = lambda: {"summary": (
            f"{len(accepted)} warnings for {epic['source_key']}" +
            (f"; expect {forecast['p50_days']} days (P50) to {forecast['p90_days']} days (P90)" if forecast["p50_days"] else "") +
            (f". Consider adding: {'; '.join(tickets)}." if tickets else "."))}
        summary = self.llm.json(self.name, WRITER_SYSTEM, {
            "epic": {"key": epic["source_key"], "title": epic["title"]},
            "forecast": {"p50_days": forecast["p50_days"], "p90_days": forecast["p90_days"],
                         "similar_finished_epics": len(forecast["basis"])},
            "warnings": [{"claim": w["claim"], "confidence": w["confidence"]} for w in accepted],
            "missing_tickets": tickets}, WRITER_SCHEMA, fallback)["summary"]
        report = {"epic": epic["source_key"], "title": epic["title"], "as_of": store.as_of, "summary": summary,
                  "forecast": forecast, "missing_tickets": tickets, "suppressed_by_ring_fence": suppressed,
                  "warnings": [{k: w[k] for k in ("id", "agent", "claim", "evidence", "confidence", "suggested_ticket")}
                               for w in accepted]}
        self.log.emit(self.name, "review", summary, epic=epic["id"])
        return report


class Scorekeeper:
    """When an epic finishes, checks each published warning against what happened and reweights its pattern."""
    name = "scorekeeper"

    def __init__(self, log):
        self.log = log

    def check(self, prediction, epic, as_of, after) -> tuple[bool, str]:
        kind = prediction["type"]
        if kind == "overrun":
            items = [after.get(i) for i in prediction["item_ids"]]
            done = [i for i in items if i and duration(i) is not None and i["estimate"]]
            over = [i for i in done if duration(i) > prediction["ratio"] * prediction["days_per_point"] * i["estimate"]]
            return bool(done) and len(over) * 2 >= len(done), f"{len(over)}/{len(done)} finished tickets overran"
        if kind == "missing_category":
            added = [c for c in after.children(epic["id"])
                     if joined(c) > as_of and prediction["category"] in categories_of(c)]
            return bool(added), f"{len(added)} {prediction['category']} tickets added after the review"
        if kind == "blocked":
            hits = [link for i in prediction["item_ids"] for link in after.links(i)
                    if link["kind"] == "blocks" and link["to_id"] == i and (link["created_at"] or "") > as_of]
            return bool(hits), f"{len(hits)} planned tickets were later blocked"
        return False, f"unknown prediction type {kind}"

    def score(self, conn, run_id, epic, after) -> dict:
        rows = conn.execute("SELECT * FROM warning WHERE run_id=? AND epic_id=? AND status='accepted'",
                            (run_id, epic["id"])).fetchall()
        tally = {"hits": 0, "misses": 0}
        for row in rows:
            hit, detail = self.check(json.loads(row["prediction"]), epic, row["as_of"], after)
            tally["hits" if hit else "misses"] += 1
            conn.execute("INSERT OR REPLACE INTO score VALUES (?,?,?,?,?)", (run_id, row["id"], after.as_of, int(hit), detail))
            if row["pattern_id"]:
                conn.execute("UPDATE pattern SET weight=MAX(0, MIN(2, weight + ?)) WHERE run_id=? AND id=?",
                             (0.2 if hit else -0.3, run_id, row["pattern_id"]))
            self.log.emit(self.name, "hit" if hit else "miss", f"{row['agent']}: {detail}", warning=row["id"])
        review = conn.execute("SELECT report FROM review WHERE run_id=? AND epic_id=?", (run_id, epic["id"])).fetchone()
        if review and duration(epic):
            forecast = json.loads(review["report"])["forecast"]
            actual = round(days_between(epic["started_at"] or epic["created_at"], epic["resolved_at"]), 1)
            tally["actual_days"] = actual
            if forecast["p90_days"] is not None:
                tally["within_p90"] = actual <= forecast["p90_days"]
                self.log.emit(self.name, "forecast", f"took {actual} days; forecast P50 {forecast['p50_days']}, "
                                                     f"P90 {forecast['p90_days']}")
        return tally
