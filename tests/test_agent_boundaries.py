"""Adversarial contracts and failure injection, without paid API calls."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nabu import demo_data, replay
from nabu.agents.critic import Critic, CRITIC_SCHEMA
from nabu.agents.writer import Writer
from nabu.llm import LLM, Log
from nabu.store import ScopedStore, ITEM_FIELDS, connect


class BoundariesTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.addCleanup(self.conn.close)
        for record in demo_data.generate():
            if record["table"] != "work_item":
                continue
            row = dict(record["row"])
            for key in ("components", "labels"):
                row[key] = json.dumps(row[key])
            self.conn.execute(f"INSERT INTO work_item VALUES ({','.join('?' for _ in ITEM_FIELDS)})",
                              [row.get(k) for k in ITEM_FIELDS])
        self.conn.commit()
        self.log = Log(self.conn, "r", quiet=True)

    def model(self, value, stop="end_turn", **kwargs):
        client = Mock()
        client.beta.messages.create.return_value = SimpleNamespace(
            stop_reason=stop, content=[SimpleNamespace(type="text", text=json.dumps(value))])
        return LLM(self.log, client=client, **kwargs), client

    def test_nested_invalid_outputs_retry_then_fall_back(self):
        valid = {"decisions": []}
        invalid = [[], {"decisions": None}, {"decisions": [None]},
                   {"decisions": [{"id": "x", "action": "publish", "reason": "ok"}]},
                   {"decisions": [{"id": 7, "action": "accept", "reason": "ok"}]},
                   {"decisions": [], "extra": True}]
        for value in invalid:
            with self.subTest(value=value):
                llm, client = self.model(value)
                self.assertEqual(llm.json("critic", "", {}, CRITIC_SCHEMA, lambda: valid), valid)
                self.assertEqual(client.beta.messages.create.call_count, 2)

    def test_truncated_valid_json_is_rejected(self):
        llm, client = self.model({"decisions": []}, stop="max_tokens")
        fallback = {"decisions": [{"id": "fallback"}]}
        self.assertEqual(llm.json("critic", "", {}, CRITIC_SCHEMA, lambda: fallback), fallback)
        self.assertEqual(client.beta.messages.create.call_count, 2)

    def test_retry_cannot_exceed_call_budget(self):
        llm, client = self.model(None, max_calls=1)
        for _ in range(3):
            self.assertEqual(llm.json("critic", "", {}, CRITIC_SCHEMA, lambda: "fallback"), "fallback")
        self.assertEqual(client.beta.messages.create.call_count, 1)

    def test_model_cannot_override_suppressed_warning(self):
        store = ScopedStore(self.conn, "demo", ["payments", "platform"], replay.END_OF_TIME)
        viewer = ScopedStore(self.conn, "demo", ["payments"], replay.END_OF_TIME)
        hidden = [i["id"] for i in store.items(scope="platform")][:2]
        visible = [i["id"] for i in viewer.items()][:2]
        warnings = [{"id": key, "agent": "estimator", "claim": "risk", "confidence": "high", "evidence": evidence}
                    for key, evidence in (("hidden", hidden), ("visible", visible))]
        llm, _ = self.model({"decisions": [
            {"id": key, "action": "accept", "reason": "yes"} for key in ("hidden", "visible")]})
        decisions = Critic(llm, self.log).judge(warnings, store, viewer, False)
        self.assertEqual(decisions["hidden"][0], "suppressed")
        self.assertEqual(decisions["visible"][0], "accept")

    def test_duplicate_critic_decisions_fail_closed(self):
        store = ScopedStore(self.conn, "demo", ["payments"], replay.END_OF_TIME)
        warning = {"id": "w", "agent": "estimator", "claim": "risk", "confidence": "high",
                   "evidence": [i["id"] for i in store.items()][:2]}
        llm, _ = self.model({"decisions": [
            {"id": "w", "action": action, "reason": "yes"} for action in ("drop", "accept")]})
        self.assertEqual(Critic(llm, self.log).judge([warning], store, store, False)["w"][0], "drop")

    def test_forecast_uses_viewer_scope(self):
        from nabu.review import ReviewTeam
        store = ScopedStore(self.conn, "demo", ["payments", "platform"], replay.END_OF_TIME)
        viewer = ScopedStore(self.conn, "demo", ["payments"], replay.END_OF_TIME)
        epic = viewer.items(type="epic")[-1]
        team = ReviewTeam(LLM(self.log, offline=True), self.log)
        report = team.review(self.conn, "r", epic, store, viewer)
        self.assertTrue(report["forecast"]["basis"])
        self.assertTrue(all(viewer.can_see(i) for i in report["forecast"]["basis"]))
        self.assertNotIn(epic["id"], report["forecast"]["basis"])

    def test_estimate_events_cannot_cross_tenant_or_scope(self):
        store = ScopedStore(self.conn, "demo", ["payments"], "2023-01-15T00:00:00Z")
        item = next(i for i in store.items() if i["estimate"])
        for tenant, scope, at, value in (("other", "payments", "2024-01-01", "999"),
                                         ("demo", "platform", "2024-01-02", "888"),
                                         ("demo", "payments", "2024-01-03", "3")):
            self.conn.execute("INSERT INTO event VALUES (?,?,?,?,?,?,?)",
                              (item["id"], at, "estimate_changed", value, "10", tenant, scope))
        self.assertEqual(store.get(item["id"])["estimate"], 3)

    def test_empty_viewer_is_not_promoted_to_all_scopes(self):
        replay.start(self.conn, "r", "demo", ["payments"], [])
        replay.run(self.conn, "r", offline=True, quiet=True)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM review").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM run").fetchone()[0], "finished")

    def test_invalid_viewer_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            replay.start(self.conn, "r", "demo", ["payments"], ["platform"])

    def test_failed_step_rolls_back_and_can_resume_on_same_connection(self):
        replay.start(self.conn, "r", "demo", ["payments"])
        original = Writer.run

        def fail_after_write(writer, *args):
            original(writer, *args)
            raise KeyboardInterrupt()

        with patch.object(Writer, "run", fail_after_write), self.assertRaises(KeyboardInterrupt):
            replay.run(self.conn, "r", offline=True, quiet=True)
        self.assertFalse(self.conn.in_transaction)
        for table in ("pattern", "warning", "review", "agent_log"):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT cursor FROM run").fetchone()[0], 0)
        replay.run(self.conn, "r", offline=True, quiet=True)
        self.assertEqual(self.conn.execute("SELECT status FROM run").fetchone()[0], "finished")

    def test_run_timeline_is_stable_when_data_is_added(self):
        replay.start(self.conn, "r", "demo", ["payments"])
        expected = json.loads(self.conn.execute("SELECT steps FROM run_timeline").fetchone()[0])
        replay.run(self.conn, "r", offline=True, quiet=True, stop_after=1)
        self.conn.execute("UPDATE work_item SET type='epic' WHERE type='story'")
        self.conn.commit()
        replay.run(self.conn, "r", offline=True, quiet=True)
        self.assertEqual(self.conn.execute("SELECT cursor FROM run").fetchone()[0], len(expected))

    def test_failure_preserves_existing_checkpoint_exactly(self):
        replay.start(self.conn, "r", "demo", ["payments"])
        replay.run(self.conn, "r", offline=True, quiet=True, stop_after=6)
        before = list(self.conn.iterdump())
        original = Writer.run

        def fail_after_write(writer, *args):
            original(writer, *args)
            raise RuntimeError("injected failure")

        with patch.object(Writer, "run", fail_after_write), self.assertRaises(RuntimeError):
            replay.run(self.conn, "r", offline=True, quiet=True)
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_completed_before_review_is_excluded_from_backtest(self):
        epic = self.conn.execute("SELECT id, created_at FROM work_item WHERE type='epic' LIMIT 1").fetchone()
        self.conn.execute("UPDATE work_item SET resolved_at=? WHERE id=?",
                          (replay.plus_days(epic["created_at"], 0.5), epic["id"]))
        steps = replay.timeline(self.conn, "demo", ["payments", "platform"])
        self.assertNotIn(epic["id"], [step[2] for step in steps])

    def test_filters_are_allowlisted(self):
        store = ScopedStore(self.conn, "demo", ["payments"], replay.END_OF_TIME)
        with self.assertRaises(ValueError):
            store.items(**{"scope OR 1=1 --": "platform"})

    def test_configuration_is_read_when_client_is_created(self):
        with patch.dict("os.environ", {"NABU_MODEL": "test-model", "NABU_EFFORT": "medium"}):
            llm, client = self.model({"decisions": []})
        llm.json("critic", "", {}, CRITIC_SCHEMA, lambda: {})
        kwargs = client.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "test-model")
        self.assertEqual(kwargs["output_config"]["effort"], "medium")

    def test_evaluation_is_scoped_and_read_only(self):
        from nabu.evaluate import evaluate
        replay.start(self.conn, "r", "demo", ["payments", "platform"], ["payments"])
        replay.run(self.conn, "r", offline=True, quiet=True)
        changes = self.conn.total_changes
        metrics = evaluate(self.conn, "r", "2023-06-01T00:00:00Z")
        self.assertGreater(metrics["forecast_pairs"], 0)
        self.assertGreater(metrics["scored_warnings"], 0)
        self.assertGreaterEqual(metrics["p90_coverage"], 0)
        self.assertLessEqual(metrics["p90_coverage"], 1)
        self.assertEqual(self.conn.total_changes, changes)

    def test_evaluation_handles_unobserved_outcomes(self):
        from nabu.evaluate import evaluate
        replay.start(self.conn, "r", "demo", ["payments"])
        replay.run(self.conn, "r", offline=True, quiet=True, stop_after=1)
        metrics = evaluate(self.conn, "r", "2023-01-01T00:00:00Z")
        self.assertEqual(metrics["completed_epics"], 0)
        self.assertIsNone(metrics["p90_coverage"])
        self.assertIsNone(metrics["warning_confirmation_rate"])
