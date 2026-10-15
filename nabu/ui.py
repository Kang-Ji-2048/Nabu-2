"""Local web interface for a replay run.

    python -m nabu.ui --db data/nabu.sqlite --port 8000

Serves web/index.html and a read-only JSON API the page polls, so it can run
alongside `python -m nabu replay` and show the run live.
"""
import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB = Path(__file__).resolve().parent.parent / "web"


def _connect(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def runs(conn):
    return [dict(r) for r in conn.execute("SELECT id, status, clock FROM run ORDER BY rowid DESC")]


def state(conn, run_id, after):
    run = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        return None
    log = [dict(r) | {"data": json.loads(r["data"] or "{}")} for r in conn.execute(
        "SELECT seq, sim_at, agent, kind, message, data FROM agent_log WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT 1000",
        (run_id, after))]
    reviews = []
    cited = set()
    for r in conn.execute("SELECT epic_id, as_of, report FROM review WHERE run_id = ? ORDER BY as_of", (run_id,)):
        report = json.loads(r["report"])
        for w in report.get("warnings", []):
            cited.update(w.get("evidence", []))
        reviews.append({"epic_id": r["epic_id"], "as_of": r["as_of"], "report": report})
    scores = {r["warning_id"]: dict(r) for r in conn.execute(
        "SELECT warning_id, at, hit, detail FROM score WHERE run_id = ?", (run_id,))}
    items = {}
    if cited:
        marks = ",".join("?" * len(cited))
        for r in conn.execute(f"SELECT id, source_key, title, scope FROM work_item WHERE id IN ({marks})", tuple(cited)):
            items[r["id"]] = {"key": r["source_key"], "title": r["title"], "scope": r["scope"]}
    return {
        "run": {"id": run["id"], "status": run["status"], "clock": run["clock"], "cursor": run["cursor"],
                "tenant": run["tenant_id"], "scopes": json.loads(run["scopes"]),
                "viewer_scopes": json.loads(run["viewer_scopes"]), "stats": json.loads(run["stats"] or "{}")},
        "log": log, "reviews": reviews, "scores": scores, "items": items,
    }


def make_handler(db):
    conn = _connect(db)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/runs":
                return self._send(200, json.dumps(runs(conn)).encode(), "application/json")
            if url.path == "/api/state":
                run_id = q.get("run", [""])[0]
                after = int(q.get("after", ["0"])[0] or 0)
                body = state(conn, run_id, after)
                if body is None:
                    return self._send(404, b'{"error": "unknown run"}', "application/json")
                return self._send(200, json.dumps(body).encode(), "application/json")
            self._send(404, b"not found", "text/plain")

        def log_message(self, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Nabu replay interface")
    parser.add_argument("--db", default="data/nabu.sqlite")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.db))
    print(f"Nabu interface on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
