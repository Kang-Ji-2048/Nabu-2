import json
import io
import tempfile
import unittest
from http.client import HTTPResponse
from pathlib import Path
from unittest.mock import patch

from nabu import replay, ui
from nabu.store import connect


AT = "2026-05-01T00:00:00Z"


def add_item(conn, item_id, *, tenant="company", scope="payments", type_="task", created="2026-01-01T00:00:00Z",
             parent=None, parent_at=None, resolved=None):
    conn.execute(
        "INSERT INTO work_item (id, tenant_id, scope, source, source_key, type, title, status_category, created_at, "
        "parent_id, parent_at, resolved_at) VALUES (?, ?, ?, 'jira', ?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, tenant, scope, item_id.upper(), type_, f"Title for {item_id}", "done" if resolved else "todo",
         created, parent, parent_at, resolved))


def add_run(conn, run_id="r", *, scopes=None, viewer_scopes=None):
    conn.execute("INSERT INTO run VALUES (?, 'company', ?, ?, 1, ?, 'running', '{}')",
                 (run_id, json.dumps(scopes or ["payments", "platform"]),
                  json.dumps(viewer_scopes if viewer_scopes is not None else ["payments"]), AT))


def warning(warning_id, evidence):
    return {"id": warning_id, "agent": "estimator", "claim": f"Claim for {warning_id}", "evidence": evidence,
            "confidence": "high", "suggested_ticket": f"Follow up {warning_id}"}


def add_review(conn, epic_id, warnings=None, basis=None):
    report = {"epic": epic_id.upper(), "title": f"Title for {epic_id}", "as_of": AT,
              "summary": "Original summary with forecast 42 days", "warnings": warnings or [],
              "forecast": {"p50_days": 42, "p90_days": 60, "basis": basis or []},
              "missing_tickets": [w["suggested_ticket"] for w in warnings or []], "suppressed_by_ring_fence": 0}
    conn.execute("INSERT INTO review VALUES ('r', ?, ?, ?)", (epic_id, AT, json.dumps(report)))


class StateTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.addCleanup(self.conn.close)
        add_run(self.conn)
        add_item(self.conn, "epic", type_="epic")
        add_item(self.conn, "evidence")
        add_item(self.conn, "history", type_="epic")
        add_item(self.conn, "private", scope="platform", type_="epic")
        add_item(self.conn, "other-tenant", tenant="other", type_="epic")
        add_item(self.conn, "outside-run", scope="finance", type_="epic")
        add_item(self.conn, "future", created="2027-01-01T00:00:00Z")

    def test_metadata_and_citations_obey_tenant_scope_and_time(self):
        add_review(self.conn, "epic", [warning("public-warning", ["evidence"])], ["history"])
        result = ui.state(self.conn, "r", 0)
        self.assertEqual(result["dataset"], {"work_items": 3, "epics": 2, "scopes": ["payments"],
                                             "sources": ["jira"], "as_of": AT})
        self.assertEqual(set(result["items"]), {"epic", "evidence", "history"})
        self.assertEqual(result["items"]["epic"], {"key": "EPIC", "title": "Title for epic", "scope": "payments"})
        self.assertEqual(result["reviews"][0]["report"]["forecast"]["p50_days"], 42)
        self.assertIsNone(result["run"]["offline"])
        self.assertIsNone(ui.state(self.conn, "missing", 0))

    def test_hidden_reports_warnings_scores_and_forecasts_are_not_exposed(self):
        warnings = [warning("public-warning", ["evidence"]), warning("private-warning", ["private"]),
                    warning("foreign-warning", ["other-tenant"]), warning("future-warning", ["future"])]
        add_review(self.conn, "epic", warnings, ["history", "private"])
        add_review(self.conn, "private")
        add_review(self.conn, "other-tenant")
        self.conn.execute("INSERT INTO agent_log VALUES ('r', 1, ?, 'writer', 'review', ?, ?)",
                          (AT, "Original summary with forecast 42 days", json.dumps({"epic": "epic"})))
        for w in warnings:
            self.conn.execute("INSERT INTO score VALUES ('r', ?, ?, 1, ?)", (w["id"], AT, w["claim"]))
        self.conn.execute("UPDATE run SET stats = ? WHERE id = 'r'", (json.dumps({
            "reviews": 999, "hits": 888, "misses": 777, "accuracy": [[AT, 0.8]],
            "raw_inventory_ids": ["other-tenant"], "private_scope_counters": {"private-warning": 600},
        }),))
        result = ui.state(self.conn, "r", 0)
        self.assertEqual(len(result["reviews"]), 1)
        report = result["reviews"][0]["report"]
        self.assertEqual([w["id"] for w in report["warnings"]], ["public-warning"])
        self.assertEqual(report["suppressed_by_ring_fence"], 3)
        self.assertEqual(report["forecast"], {"p50_days": None, "p90_days": None, "basis": [],
                                               "withheld_by_viewer_scope": True})
        self.assertEqual(report["missing_tickets"], ["Follow up public-warning"])
        self.assertNotIn("42 days", report["summary"])
        self.assertEqual(set(result["scores"]), {"public-warning"})
        self.assertEqual(result["run"]["stats"], {"reviews": 1, "hits": 1, "misses": 0, "accuracy": [[AT, 1.0]]})
        encoded = json.dumps(result)
        for secret in ("private-warning", "foreign-warning", "future-warning", "Title for private", "other-tenant", "42 days"):
            self.assertNotIn(secret, encoded)

    def test_viewer_cannot_expand_beyond_the_run_scope(self):
        self.conn.execute("UPDATE run SET viewer_scopes = ?", (json.dumps(["payments", "finance"]),))
        add_review(self.conn, "outside-run")
        result = ui.state(self.conn, "r", 0)
        self.assertEqual(result["dataset"]["work_items"], 3)
        self.assertEqual(result["reviews"], [])
        self.conn.execute("UPDATE run SET viewer_scopes = '[]'")
        empty = ui.state(self.conn, "r", 0)
        self.assertEqual(empty["dataset"]["work_items"], 0)
        self.assertEqual(empty["items"], {})

    def test_unpublished_log_details_are_redacted_but_cursor_advances(self):
        self.conn.execute("INSERT INTO warning VALUES (?, 'r', 'epic', ?, 'estimator', NULL, ?, ?, NULL, '{}', 'high', 'suppressed', ?)",
                          ("hidden-warning", AT, "secret claim", json.dumps(["private"]), "secret reason"))
        entries = [
            ("estimator", "warning", "secret claim", {"evidence": ["private"]}),
            ("critic", "suppressed", "secret reason", {"warning": "hidden-warning"}),
            ("writer", "review", "Title for private", {"epic": "private"}),
            ("data_quality", "profile", "secret aggregate", {"finished_items": 999}),
            ("estimator", "warning", "Visible detail", {"evidence": ["evidence"]}),
            ("critic", "retry", "Provider exception with secret payload", {}),
        ]
        for seq, (agent, kind, message, data) in enumerate(entries, 1):
            self.conn.execute("INSERT INTO agent_log VALUES ('r', ?, ?, ?, ?, ?, ?)",
                              (seq, AT, agent, kind, message, json.dumps(data)))
        result = ui.state(self.conn, "r", 1)
        self.assertEqual([entry["seq"] for entry in result["log"]], [2, 3, 4, 5, 6])
        self.assertTrue(all(entry["data"].get("redacted") for entry in result["log"][:3]))
        self.assertEqual(result["log"][3]["message"], "Visible detail")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("hidden-warning", json.dumps(result))


class ProgressStateTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.addCleanup(self.conn.close)
        add_item(self.conn, "first-epic", type_="epic", resolved="2026-01-10T00:00:00Z")
        add_item(self.conn, "second-epic", type_="epic", created="2026-02-01T00:00:00Z",
                 resolved="2026-02-10T00:00:00Z")
        add_item(self.conn, "first-plan-ticket", parent="first-epic")
        add_item(self.conn, "second-plan-ticket", parent="second-epic", created="2026-02-01T00:00:00Z")
        add_item(self.conn, "late-ticket", parent="first-epic", created="2026-01-03T00:00:00Z")
        add_item(self.conn, "orphan-ticket")
        add_item(self.conn, "hidden-parent-ticket", parent="private-epic")
        add_item(self.conn, "private-epic", type_="epic", scope="platform")
        add_item(self.conn, "private-ticket", scope="platform", parent="private-epic")
        add_item(self.conn, "foreign-epic", type_="epic", tenant="another-company")
        add_item(self.conn, "outside-epic", type_="epic", scope="finance")

    def start(self):
        replay.start(self.conn, "progress-run", "company", ["payments", "platform"], ["payments"])

    def state(self):
        return ui.state(self.conn, "progress-run", 0)

    def test_initial_inventory_is_complete_but_replay_dataset_is_empty(self):
        self.start()
        result = self.state()
        inventory = result["inventory"]
        self.assertIsNone(result["run"]["clock"])
        self.assertEqual(result["dataset"]["work_items"], 0)
        self.assertEqual({key: inventory[key] for key in ("work_items", "epics", "tickets", "linked_tickets", "unlinked_tickets")},
                         {"work_items": 7, "epics": 2, "tickets": 5, "linked_tickets": 3, "unlinked_tickets": 2})
        self.assertEqual(inventory["source"], "run_start")
        self.assertTrue(inventory["captured_at"])
        self.assertEqual([row["scope"] for row in inventory["by_scope"]], ["payments"])
        for kind, total in (("epics", 2), ("tickets", 5), ("steps", 4)):
            self.assertEqual(result["progress"][kind], {"done": 0, "total": total, "percent": 0})
        self.assertEqual(result["checkpoints"], [])
        for secret in ("private-epic", "private-ticket", "foreign-epic", "outside-epic", "hidden-parent-ticket"):
            self.assertNotIn(secret, json.dumps({key: result[key] for key in ("inventory", "progress", "checkpoints")}))

    def test_progress_counts_only_committed_visible_review_plans(self):
        self.start()
        inventory = self.state()["inventory"]
        replay.run(self.conn, "progress-run", offline=True, stop_after=1, quiet=True)
        partial = self.state()
        self.assertEqual(partial["inventory"], inventory)
        self.assertEqual(partial["progress"]["epics"], {"done": 1, "total": 2, "percent": 50})
        self.assertEqual(partial["progress"]["tickets"], {"done": 1, "total": 5, "percent": 20})
        self.assertEqual(partial["progress"]["steps"], {"done": 1, "total": 4, "percent": 25})
        self.assertEqual(partial["checkpoints"][-1]["progress"], partial["progress"])
        self.assertEqual(partial["checkpoints"][-1]["at"], partial["run"]["clock"])
        self.assertEqual([row["scope"] for row in partial["progress"]["by_scope"]], ["payments"])
        replay.run(self.conn, "progress-run", offline=True, quiet=True)
        final = self.state()
        self.assertEqual(final["run"]["status"], "finished")
        self.assertEqual(final["inventory"], inventory)
        self.assertEqual(final["progress"]["epics"], {"done": 2, "total": 2, "percent": 100})
        self.assertEqual(final["progress"]["tickets"], {"done": 2, "total": 5, "percent": 40})
        self.assertEqual(final["progress"]["steps"], {"done": 4, "total": 4, "percent": 100})
        self.assertEqual(final["checkpoints"][0], partial["checkpoints"][0])
        self.assertEqual(final["checkpoints"][-1]["progress"], final["progress"])

    def test_inventory_and_step_plan_stay_frozen_after_new_data_is_loaded(self):
        self.start()
        inventory = self.state()["inventory"]
        add_item(self.conn, "new-epic", type_="epic", created="2026-03-01T00:00:00Z")
        add_item(self.conn, "new-ticket", parent="new-epic", created="2026-03-01T00:00:00Z")
        self.conn.commit()
        replay.run(self.conn, "progress-run", offline=True, quiet=True)
        result = self.state()
        self.assertEqual(result["inventory"], inventory)
        self.assertEqual(result["progress"]["steps"], {"done": 4, "total": 4, "percent": 100})
        self.assertNotIn("new-epic", [review["epic_id"] for review in result["reviews"]])

    def test_persisted_progress_respects_a_narrower_viewer_scope(self):
        replay.start(self.conn, "progress-run", "company", ["payments", "platform"], ["payments", "platform"])
        replay.run(self.conn, "progress-run", offline=True, quiet=True)
        self.conn.execute("UPDATE run SET viewer_scopes = '[\"payments\"]' WHERE id = 'progress-run'")
        result = self.state()
        self.assertEqual(result["inventory"]["epics"], 2)
        self.assertEqual(result["inventory"]["tickets"], 5)
        self.assertEqual(result["progress"]["epics"], {"done": 2, "total": 2, "percent": 100})
        self.assertEqual(result["progress"]["tickets"], {"done": 2, "total": 5, "percent": 40})
        self.assertEqual(result["progress"]["steps"], {"done": 4, "total": 4, "percent": 100})
        self.assertEqual(result["run"]["stats"]["reviews"], 2)
        for checkpoint in result["checkpoints"]:
            self.assertEqual([row["scope"] for row in checkpoint["progress"]["by_scope"]], ["payments"])
        self.assertEqual(len(result["checkpoints"]), 4)
        public = json.dumps({key: result[key] for key in ("inventory", "progress", "checkpoints")})
        for secret in ("platform", "private-epic", "private-ticket", "foreign-epic"):
            self.assertNotIn(secret, public)

    def test_legacy_inventory_is_labelled_reconstructed_and_does_not_write(self):
        add_run(self.conn, "progress-run")
        self.conn.execute("UPDATE run SET cursor = 0, clock = NULL WHERE id = 'progress-run'")
        self.conn.execute("DROP TABLE IF EXISTS replay_progress")
        self.conn.commit()
        result = self.state()
        self.assertEqual(result["inventory"]["source"], "legacy_reconstructed")
        self.assertIsNone(result["inventory"]["captured_at"])
        self.assertEqual(result["inventory"]["tickets"], 5)
        self.assertEqual(result["progress"]["tickets"], {"done": 0, "total": 5, "percent": 0})
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name = 'replay_progress'").fetchone())

    def test_legacy_cursor_cannot_advance_past_the_committed_clock(self):
        add_run(self.conn, "progress-run")
        # A legacy cursor may include steps outside today's viewer scopes.
        # It cannot prove that a future step in the visible timeline ran.
        self.conn.execute("UPDATE run SET clock = '2026-01-01T12:00:00Z' WHERE id = 'progress-run'")
        result = self.state()
        self.assertEqual(result["progress"]["epics"]["done"], 0)
        self.assertEqual(result["progress"]["tickets"]["done"], 0)
        self.assertEqual(result["progress"]["steps"]["done"], 0)
        self.assertEqual(result["checkpoints"], [])


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.web = root / "frontend"
        self.web.mkdir()
        self.db = root / "nabu.sqlite"
        conn = connect(self.db)
        add_run(conn)
        conn.commit()
        conn.close()
        for name, contents in {"index.html": "<h1>Nabu</h1>", "app.js": "const ok = true;", "config.js": 'window.NABU_API = "";',
                               "styles.css": "body { color: #333; }", "demo-state.json": '{"sample": true}'}.items():
            (self.web / name).write_text(contents)
        (self.web / "runs").mkdir()
        (self.web / "runs" / "index.json").write_text('[{"id": "r"}]')
        (self.web / "runs" / "r.json").write_text('{"run": {"id": "r"}}')
        (self.web / "secrets.txt").write_text("Do not serve")
        self.patch = patch.object(ui, "WEB", self.web)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.handler = ui.make_handler(self.db)

    def get(self, path):
        # Exercise real HTTP parsing and dispatch without binding a socket;
        # these tests also run in environments without network permission.
        class MemorySocket:
            def __init__(self, payload):
                self.input = io.BytesIO(payload)
                self.output = io.BytesIO()

            def makefile(self, *args):
                return self.input

            def sendall(self, data):
                self.output.write(data)

        request = MemorySocket(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
        self.handler(request, ("127.0.0.1", 0), object())
        response = HTTPResponse(MemorySocket(request.output.getvalue()))
        response.begin()
        return response.status, dict(response.getheaders()), response.read()

    def test_static_asset_allowlist_and_mime_types(self):
        for path, mime in (("/", "text/html"), ("/app.js", "text/javascript"), ("/config.js", "text/javascript"),
                           ("/styles.css", "text/css"), ("/demo-state.json", "application/json"),
                           ("/runs/index.json", "application/json"), ("/runs/r.json", "application/json")):
            with self.subTest(path=path):
                status, headers, body = self.get(path)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith(mime))
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertTrue(body)
        for path in ("/secrets.txt", "/../nabu.sqlite", "/%2e%2e/nabu.sqlite", "/frontend/app.js",
                     "/runs/../demo-state.json", "/runs/%2e%2e/demo-state.json", "/runs/r.sqlite"):
            self.assertEqual(self.get(path)[0], 404)
        (self.web / "app.js").unlink()
        (self.web / "app.js").symlink_to(self.db)
        self.assertEqual(self.get("/app.js")[0], 404)
        (self.web / "styles.css").unlink()
        self.assertEqual(self.get("/styles.css")[0], 404)
        (self.web / "runs" / "r.json").unlink()
        (self.web / "runs" / "r.json").symlink_to(self.db)
        self.assertEqual(self.get("/runs/r.json")[0], 404)

    def test_api_rejects_invalid_cursor_and_retains_unknown_run_404(self):
        for after in ("invalid", "-1", "1.5", "9223372036854775808"):
            with self.subTest(after=after):
                status, headers, body = self.get(f"/api/state?run=r&after={after}")
                self.assertEqual(status, 400)
                self.assertIn("after", json.loads(body)["error"])
                self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(self.get("/api/state?run=missing")[0], 404)
        self.assertEqual(self.get("/api/state?run=r&after=0")[0], 200)
        self.assertEqual(self.get("/api/state?run=r&after=")[0], 200)
        status, _, body = self.get("/api/runs")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)[0]["id"], "r")
        self.assertEqual(self.get("/api/runs")[1]["Access-Control-Allow-Origin"], "*")

    def test_export_retains_all_pages_and_scoped_metadata(self):
        conn = connect(self.db)
        add_item(conn, "epic", type_="epic")
        add_item(conn, "private", scope="platform")
        add_review(conn, "epic")
        conn.executemany("INSERT INTO agent_log VALUES ('r', ?, ?, 'estimator', 'warning', 'private detail', ?)",
                         [(seq, AT, json.dumps({"evidence": ["private"]})) for seq in range(1, 1003)])
        conn.commit()
        conn.close()
        result = ui.export(self.db, "r", self.web)
        self.assertEqual(result["log_rows"], 1002)
        snapshot = json.loads((self.web / "runs" / "r.json").read_text())
        self.assertEqual(snapshot["log"][-1]["seq"], 1002)
        self.assertEqual(snapshot["dataset"]["work_items"], 1)
        self.assertNotIn("private detail", json.dumps(snapshot))
        self.assertEqual(json.loads((self.web / "runs" / "index.json").read_text()),
                         [{"id": "r", "status": "recorded", "clock": AT}])
        for unsafe in ("../escape", "index", "/absolute", "r/child"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                ui.export(self.db, unsafe, self.web)


if __name__ == "__main__":
    unittest.main()
