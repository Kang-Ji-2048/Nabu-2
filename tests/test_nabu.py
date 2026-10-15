import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nabu import demo_data, replay
from nabu.llm import LLM, Log
from nabu.store import ScopedStore, connect, load_jsonl


def loaded_db(tmp):
    path = Path(tmp) / "demo.jsonl"
    demo_data.write(path)
    conn = connect(Path(tmp) / "nabu.sqlite")
    load_jsonl(conn, path)
    return conn


class ScopedStoreTest(unittest.TestCase):
    def test_hides_other_scopes_and_the_future(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = loaded_db(tmp)
            epic = ScopedStore(conn, "demo", ["payments"], replay.END_OF_TIME).items(type="epic")[0]
            day_one = ScopedStore(conn, "demo", ["payments"], replay.plus_days(epic["created_at"], 1))
            self.assertTrue(all(i["scope"] == "payments" for i in day_one.items()))
            self.assertIsNone(day_one.get(epic["id"])["resolved_at"])  # outcome masked
            later = [c for c in ScopedStore(conn, "demo", ["payments"], replay.END_OF_TIME).children(epic["id"])
                     if c["created_at"] > day_one.as_of]
            self.assertTrue(later)
            self.assertFalse(any(day_one.can_see(c["id"]) for c in later))  # added later: invisible
            self.assertEqual(ScopedStore(conn, "other", ["payments"], replay.END_OF_TIME).items(), [])


class ReplayTest(unittest.TestCase):
    def test_resume_matches_an_uninterrupted_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = loaded_db(tmp)
            for run_id, stop in (("full", None), ("split", 5)):
                replay.start(conn, run_id, "demo", ["payments", "platform"], ["payments"])
                replay.run(conn, run_id, offline=True, stop_after=stop, quiet=True)
            replay.run(conn, "split", offline=True, quiet=True)
            rows = lambda run_id: conn.execute(
                "SELECT id, status FROM warning WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
            self.assertEqual([tuple(r) for r in rows("full")], [tuple(r) for r in rows("split")])
            self.assertEqual(conn.execute("SELECT status FROM run WHERE id='split'").fetchone()[0], "finished")

    def test_ring_fence_suppresses_out_of_scope_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = loaded_db(tmp)
            replay.start(conn, "r", "demo", ["payments", "platform"], ["payments"])
            replay.run(conn, "r", offline=True, quiet=True)
            viewer = ScopedStore(conn, "demo", ["payments"], replay.END_OF_TIME)
            for status, evidence in conn.execute("SELECT status, evidence FROM warning WHERE run_id='r'"):
                visible = all(viewer.can_see(i) for i in json.loads(evidence))
                self.assertEqual(status == "suppressed", not visible)
            self.assertTrue(conn.execute("SELECT 1 FROM warning WHERE status='suppressed'").fetchone())
            self.assertTrue(conn.execute("SELECT 1 FROM score WHERE run_id='r'").fetchone())


class LLMRecoveryTest(unittest.TestCase):
    def test_malformed_reply_retries_then_falls_back(self):
        class Client:
            calls = 0
            class beta:
                class messages:
                    @staticmethod
                    def create(**kwargs):
                        Client.calls += 1
                        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="not json")])
        conn = connect(":memory:")
        llm = LLM(Log(conn, "t", quiet=True), client=Client)
        result = llm.json("critic", "system", {}, {"required": ["x"]}, lambda: {"x": "fallback"})
        self.assertEqual(result, {"x": "fallback"})
        self.assertEqual(Client.calls, 2)
        kinds = [r[0] for r in conn.execute("SELECT kind FROM agent_log ORDER BY seq")]
        self.assertEqual(kinds, ["retry", "recovered"])


if __name__ == "__main__":
    unittest.main()
