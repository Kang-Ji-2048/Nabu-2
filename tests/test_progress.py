import json
import unittest

from nabu import progress, replay
from nabu.store import connect


def add_item(conn, key, kind="story", scope="payments", created="2024-01-01T00:00:00Z",
             resolved=None, parent=None, parent_at=None, tenant="demo"):
    conn.execute("INSERT INTO work_item (id,tenant_id,scope,source,source_key,type,title,parent_id,parent_at,"
                 "status_category,created_at,resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (key, tenant, scope, "synthetic", key, kind, key, parent, parent_at,
                  "done" if resolved else "todo", created, resolved))


def populated():
    conn = connect(":memory:")
    add_item(conn, "epic-1", "epic", resolved="2024-01-05T00:00:00Z")
    add_item(conn, "epic-2", "epic", created="2024-01-10T00:00:00Z", resolved="2024-01-15T00:00:00Z")
    add_item(conn, "day-one-1", parent="epic-1")
    add_item(conn, "day-one-2", parent="epic-2", created="2024-01-10T00:00:00Z")
    add_item(conn, "late-created", parent="epic-1", created="2024-01-03T00:00:00Z")
    add_item(conn, "late-joined", parent="epic-1", parent_at="2024-01-04T00:00:00Z")
    add_item(conn, "orphan")
    add_item(conn, "hidden-parent", parent="private-epic")
    add_item(conn, "private-epic", "epic", scope="platform")
    add_item(conn, "private-ticket", scope="platform", parent="private-epic")
    add_item(conn, "other-tenant", tenant="another")
    conn.commit()
    return conn


def public(conn, run_id="run"):
    row = conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    return progress.public_state(conn, row)


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.conn = populated()
        self.addCleanup(self.conn.close)

    def start(self, **kwargs):
        replay.start(self.conn, "run", "demo", ["payments", "platform"], kwargs.get("viewers", ["payments"]))

    def test_start_captures_full_visible_inventory_before_any_step(self):
        self.start()
        state = public(self.conn)
        inventory = state["inventory"]
        self.assertEqual((inventory["work_items"], inventory["epics"], inventory["tickets"]), (8, 2, 6))
        self.assertEqual((inventory["linked_tickets"], inventory["unlinked_tickets"]), (4, 2))
        self.assertEqual(inventory["source"], "run_start")
        self.assertEqual([scope["scope"] for scope in inventory["by_scope"]], ["payments"])
        self.assertEqual(state["progress"]["steps"], {"done": 0, "total": 4, "percent": 0.0})
        self.assertEqual(state["checkpoints"], [])
        self.assertNotIn("private", json.dumps(state))

    def test_coverage_uses_unique_actual_day_one_plan_and_scores_without_warnings(self):
        self.start()
        replay.run(self.conn, "run", offline=True, stop_after=1, quiet=True)
        partial = public(self.conn)
        self.assertEqual(partial["progress"]["epics"]["done"], 1)
        self.assertEqual(partial["progress"]["tickets"]["done"], 1)
        self.assertEqual(partial["progress"]["steps"]["done"], 1)
        self.assertEqual(self.conn.execute("SELECT status FROM run").fetchone()[0], "paused")
        replay.run(self.conn, "run", offline=True, quiet=True)
        final = public(self.conn)
        self.assertEqual(final["progress"]["epics"], {"done": 2, "total": 2, "percent": 100.0})
        self.assertEqual(final["progress"]["tickets"], {"done": 2, "total": 6, "percent": 33.3})
        self.assertEqual(final["progress"]["steps"], {"done": 4, "total": 4, "percent": 100.0})
        self.assertEqual([point["progress"]["steps"]["done"] for point in final["checkpoints"]], [1, 2, 3, 4])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM warning").fetchone()[0], 0)

    def test_inventory_and_schedule_stay_frozen_when_dataset_changes_on_resume(self):
        self.start()
        before = public(self.conn)["inventory"]
        replay.run(self.conn, "run", offline=True, stop_after=1, quiet=True)
        add_item(self.conn, "inserted-earlier", "epic", created="2023-01-01T00:00:00Z")
        add_item(self.conn, "new-ticket", parent="epic-2", created="2024-01-10T00:00:00Z")
        self.conn.execute("UPDATE work_item SET resolved_at='2025-01-01T00:00:00Z' WHERE id='epic-2'")
        self.conn.commit()
        replay.run(self.conn, "run", offline=True, quiet=True)
        result = public(self.conn)
        self.assertEqual(result["inventory"], before)
        self.assertEqual(result["progress"]["steps"]["done"], 4)
        self.assertEqual(result["progress"]["tickets"]["done"], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM review").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT clock FROM run").fetchone()[0], "2024-01-15T00:00:00Z")

    def test_empty_intersection_finishes_without_division_by_zero(self):
        replay.start(self.conn, "run", "demo", ["payments"], ["platform"])
        replay.run(self.conn, "run", offline=True, quiet=True)
        result = public(self.conn)
        self.assertEqual(result["inventory"]["work_items"], 0)
        self.assertEqual(result["progress"]["steps"], {"done": 0, "total": 0, "percent": 0.0})
        self.assertEqual(self.conn.execute("SELECT status FROM run").fetchone()[0], "finished")

    def test_explicit_empty_viewer_scope_does_not_fall_back_to_agent_scopes(self):
        self.start(viewers=[])
        self.assertEqual(public(self.conn)["inventory"]["work_items"], 0)

    def test_scope_narrowing_filters_inventory_progress_and_hidden_checkpoints(self):
        self.start(viewers=["payments", "platform"])
        replay.run(self.conn, "run", offline=True, quiet=True)
        self.conn.execute("UPDATE run SET viewer_scopes='[\"payments\"]' WHERE id='run'")
        self.conn.commit()
        result = public(self.conn)
        self.assertNotIn("platform", json.dumps(result))
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(result["progress"]["steps"], {"done": 4, "total": 4, "percent": 100.0})
        self.assertEqual(result["progress"]["tickets"]["done"], 2)
        self.assertEqual(len(result["checkpoints"]), 4)

    def test_legacy_run_reconstructs_read_only_then_freezes_on_resume(self):
        self.start()
        replay.run(self.conn, "run", offline=True, stop_after=1, quiet=True)
        self.conn.execute("DELETE FROM replay_progress")
        self.conn.commit()
        legacy = public(self.conn)
        self.assertEqual(legacy["inventory"]["source"], "legacy_reconstructed")
        self.assertIsNone(legacy["inventory"]["captured_at"])
        self.assertEqual(legacy["progress"]["tickets"]["done"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM replay_progress").fetchone()[0], 0)
        replay.run(self.conn, "run", offline=True, quiet=True)
        final = public(self.conn)
        self.assertEqual(final["inventory"]["source"], "legacy_reconstructed")
        self.assertEqual(final["progress"]["steps"]["done"], 4)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM replay_progress").fetchone()[0], 1)

    def test_legacy_database_without_progress_table_is_readable(self):
        self.start()
        self.conn.execute("DROP TABLE replay_progress")
        self.conn.commit()
        self.assertEqual(public(self.conn)["inventory"]["source"], "legacy_reconstructed")

    def test_removed_scheduled_epic_does_not_crash_or_claim_review_coverage(self):
        self.start()
        self.conn.execute("DELETE FROM work_item WHERE id='epic-1'")
        self.conn.commit()
        replay.run(self.conn, "run", offline=True, quiet=True)
        result = public(self.conn)
        self.assertEqual(result["progress"]["epics"]["done"], 1)
        self.assertEqual(result["progress"]["steps"]["done"], 4)

    def test_incomplete_fraction_does_not_round_up_to_complete(self):
        self.assertEqual(progress._measure(9999, 10000)["percent"], 99.9)

    def test_legacy_cursor_cannot_advance_past_committed_clock(self):
        self.start()
        self.conn.execute("DELETE FROM replay_progress")
        self.conn.execute("UPDATE run SET cursor=1, clock='2024-01-01T12:00:00Z'")
        self.conn.commit()
        result = public(self.conn)
        self.assertEqual(result["progress"]["steps"]["done"], 0)
        self.assertEqual(result["checkpoints"], [])


if __name__ == "__main__":
    unittest.main()
