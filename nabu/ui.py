"""Local, read-only web interface for a replay run.

    python -m nabu.ui --db data/nabu.sqlite --port 8000

Serves frontend/index.html, recorded snapshots, and a read-only JSON API. The
API respects each run's recorded viewer scopes. This loopback-only helper is
not a company authentication or authorization service.
"""
import argparse
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .progress import public_state as progress_state

WEB = Path(__file__).resolve().parent.parent / "frontend"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/config.js": ("config.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/demo-state.json": ("demo-state.json", "application/json; charset=utf-8"),
}
SNAPSHOT_PATH = re.compile(r"/runs/[A-Za-z0-9][A-Za-z0-9._-]*\.json")


def _connect(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def runs(conn):
    return [dict(r) for r in conn.execute("SELECT id, status, clock FROM run ORDER BY rowid DESC")]


def _public_stats(reviews, scores):
    """Derive display statistics from the same viewer-filtered records returned.

    Internal run.stats can contain additional execution state or counters from a
    broader agent view. It must not become an unfiltered API extension point.
    """
    ordered = sorted(scores.values(), key=lambda score: score["at"])
    hits, accuracy = 0, []
    for index, score in enumerate(ordered, 1):
        hits += int(bool(score["hit"]))
        point = [score["at"], round(hits / index, 3)]
        if accuracy and accuracy[-1][0] == score["at"]:
            accuracy[-1] = point
        else:
            accuracy.append(point)
    return {"reviews": len(reviews), "hits": hits, "misses": len(ordered) - hits, "accuracy": accuracy}


def state(conn, run_id, after):
    run = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        return None
    scopes = json.loads(run["scopes"])
    viewer_scopes = json.loads(run["viewer_scopes"])
    visible_scopes = sorted(set(scopes) & set(viewer_scopes))
    scope_marks = ",".join("?" * len(visible_scopes)) or "NULL"
    where = f"tenant_id = ? AND scope IN ({scope_marks})"
    params = [run["tenant_id"], *visible_scopes]
    cache = {}

    def visible_item(item_id, at=None):
        # Cache metadata, never unscoped titles or source keys. A report's
        # evidence must also have existed at that report's simulated time.
        if item_id not in cache:
            cache[item_id] = conn.execute(
                f"SELECT id, source_key, title, scope, created_at FROM work_item WHERE id = ? AND {where}",
                [item_id, *params]).fetchone()
        item = cache[item_id]
        cutoff = min(at, run["clock"]) if at and run["clock"] else run["clock"]
        return item if item and cutoff and item["created_at"] <= cutoff else None

    def visible_evidence(evidence, at=None):
        return all(visible_item(item_id, at) is not None for item_id in evidence)

    reviews, cited, published_warnings, filtered_epics = [], set(), set(), set()
    for r in conn.execute("SELECT epic_id, as_of, report FROM review WHERE run_id = ? ORDER BY as_of", (run_id,)):
        if not run["clock"] or r["as_of"] > run["clock"] or not visible_item(r["epic_id"], r["as_of"]):
            continue
        report = json.loads(r["report"])
        warnings = report.get("warnings", [])
        allowed = [w for w in warnings if visible_evidence(w.get("evidence", []), r["as_of"])]
        removed = len(warnings) - len(allowed)
        report["warnings"] = allowed
        report["suppressed_by_ring_fence"] = report.get("suppressed_by_ring_fence", 0) + removed
        forecast = report.get("forecast") or {}
        hidden_forecast = not visible_evidence(forecast.get("basis", []), r["as_of"])
        if hidden_forecast:
            # A range and summary derived from hidden epics are also hidden;
            # merely removing their IDs would still disclose their outcomes.
            report["forecast"] = {"p50_days": None, "p90_days": None, "basis": [],
                                  "withheld_by_viewer_scope": True}
        if removed or hidden_forecast:
            report["summary"] = f"{len(allowed)} warnings available in your viewer scope."
            report["missing_tickets"] = [w["suggested_ticket"] for w in allowed if w.get("suggested_ticket")]
            filtered_epics.add(r["epic_id"])
        cited.add(r["epic_id"])
        cited.update(report.get("forecast", {}).get("basis", []))
        for w in allowed:
            cited.update(w.get("evidence", []))
            published_warnings.add(w["id"])
        reviews.append({"epic_id": r["epic_id"], "as_of": r["as_of"], "report": report})
    scores = {r["warning_id"]: dict(r) for r in conn.execute(
        "SELECT warning_id, at, hit, detail FROM score WHERE run_id = ?", (run_id,))
        if r["warning_id"] in published_warnings}
    items = {item_id: {"key": item["source_key"], "title": item["title"], "scope": item["scope"]}
             for item_id in sorted(cited) if (item := visible_item(item_id)) is not None}

    # Logs are produced before the critic's ring fence. A suppressed warning's
    # claim, IDs and critic reason must not reappear in the activity stream.
    warning_visibility = {}
    restricted = set(scopes) != set(visible_scopes)
    log = []
    for row in conn.execute(
        "SELECT seq, sim_at, agent, kind, message, data FROM agent_log WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT 1000",
        (run_id, after)):
        entry = dict(row)
        data = json.loads(row["data"] or "{}")
        at = row["sim_at"] or run["clock"]
        hidden = bool(data.get("evidence")) and not visible_evidence(data["evidence"], at)
        if data.get("epic"):
            hidden = hidden or not visible_item(data["epic"], at) or data["epic"] in filtered_epics
        if data.get("warning"):
            warning_id = data["warning"]
            if warning_id not in warning_visibility:
                warning = conn.execute(
                    "SELECT epic_id, as_of, evidence FROM warning WHERE run_id = ? AND id = ?",
                    (run_id, warning_id)).fetchone()
                warning_visibility[warning_id] = bool(warning and visible_item(warning["epic_id"], warning["as_of"])
                    and visible_evidence(json.loads(warning["evidence"]), warning["as_of"]))
            hidden = hidden or not warning_visibility[warning_id]
        has_reference = any(data.get(key) for key in ("evidence", "epic", "warning"))
        if hidden:
            entry["message"], data = "Detail withheld by viewer scope.", {"redacted": True}
        elif row["kind"] in ("retry", "recovered"):
            # Provider exception strings may contain request material.
            entry["message"] = ("Model request failed; retrying." if row["kind"] == "retry"
                                else "Using deterministic result after a model request failed.")
            data = {}
        elif restricted and not has_reference:
            # Unattributed diagnostics may aggregate the agents' wider view.
            entry["message"] = f"{row['agent'].replace('_', ' ').capitalize()}: {row['kind'].replace('_', ' ')}."
            data = {"redacted": True}
        entry["data"] = data
        log.append(entry)

    dataset_rows = list(conn.execute(
        f"SELECT scope, source, COUNT(*) AS items, SUM(type = 'epic') AS epics FROM work_item "
        f"WHERE {where} AND created_at <= ? GROUP BY scope, source", [*params, run["clock"] or ""]))
    dataset = {"work_items": sum(r["items"] for r in dataset_rows),
               "epics": sum(r["epics"] for r in dataset_rows),
               "scopes": sorted({r["scope"] for r in dataset_rows}),
               "sources": sorted({r["source"] for r in dataset_rows}), "as_of": run["clock"]}
    return {
        "run": {"id": run["id"], "status": run["status"], "clock": run["clock"], "cursor": run["cursor"],
                "tenant": run["tenant_id"], "scopes": scopes, "viewer_scopes": viewer_scopes,
                "stats": _public_stats(reviews, scores),
                # Replay does not currently persist its offline/live setting.
                "offline": None},
        "log": log, "reviews": reviews, "scores": scores, "items": items, "dataset": dataset,
        **progress_state(conn, run),
    }


def export(db, run_id, out_dir):
    """Write a finished run to <out_dir>/runs/<run>.json (and update runs/index.json), so the page can
    replay it with no server: a static host such as Vercel just serves the frontend/ folder."""
    if run_id == "index" or not SNAPSHOT_PATH.fullmatch(f"/runs/{run_id}.json"):
        raise ValueError("snapshot run ID must start with a letter or digit, use only letters, digits, '.', '_' or '-', and not be 'index'")
    conn = _connect(db)
    try:
        conn.execute("BEGIN")
        body = state(conn, run_id, 0)
        if body is None:
            raise SystemExit(f"unknown run {run_id!r}")
        while True:  # state() pages the log
            more = state(conn, run_id, body["log"][-1]["seq"] if body["log"] else 0)["log"]
            if not more or (body["log"] and more[0]["seq"] <= body["log"][-1]["seq"]):
                break
            body["log"] += more
    finally:
        conn.close()
    folder = Path(out_dir) / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{run_id}.json").write_text(json.dumps(body), encoding="utf-8")
    index_path = folder / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    index = [r for r in index if r["id"] != run_id] + [{"id": run_id, "status": "recorded", "clock": body["run"]["clock"]}]
    index_path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return {"run": run_id, "log_rows": len(body["log"]), "reviews": len(body["reviews"]), "out": str(folder / f"{run_id}.json")}


def make_handler(db):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")  # lets the hosted frontend read this API
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            static = STATIC_FILES.get(url.path)
            if SNAPSHOT_PATH.fullmatch(url.path):
                static = (url.path.lstrip("/"), "application/json; charset=utf-8")
            if static:
                filename, ctype = static
                asset = WEB.resolve() / filename
                # Exact assets or one JSON filename inside runs/, with no
                # symlinks or directory traversal into other local files.
                if not asset.is_file() or asset.resolve() != asset:
                    return self._send(404, b"not found", "text/plain; charset=utf-8")
                return self._send(200, asset.read_bytes(), ctype)
            if url.path == "/api/runs":
                conn = _connect(db)
                try:
                    body = runs(conn)
                finally:
                    conn.close()
                return self._send(200, json.dumps(body).encode(), "application/json")
            if url.path == "/api/state":
                run_id = q.get("run", [""])[0]
                try:
                    after = int(q.get("after", ["0"])[0] or 0)
                    if after < 0 or after > 9223372036854775807:
                        raise ValueError
                except ValueError:
                    return self._send(400, b'{"error": "after must be a non-negative integer within SQLite range"}',
                                      "application/json")
                conn = _connect(db)
                try:
                    # A replay can commit while the browser polls; keep all
                    # state queries on the same SQLite read snapshot.
                    conn.execute("BEGIN")
                    body = state(conn, run_id, after)
                finally:
                    conn.close()
                if body is None:
                    return self._send(404, b'{"error": "unknown run"}', "application/json")
                return self._send(200, json.dumps(body).encode(), "application/json")
            self._send(404, b"not found", "text/plain; charset=utf-8")

        def log_message(self, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Nabu replay interface")
    parser.add_argument("--db", default="data/nabu.sqlite")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--export", metavar="RUN", help="write RUN to frontend/runs/ for static hosting, then exit")
    args = parser.parse_args()
    if args.export:
        print(json.dumps(export(args.db, args.export, WEB)))
        return
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.db))
    print(f"Nabu interface on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
