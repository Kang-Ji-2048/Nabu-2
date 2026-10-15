import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nabu import (AnthropicProvider, ClosedReviewerError, NotFoundError, Reviewer,
                  ReviewReport, ScopeError, ValidationError, Viewer, WorkItem)
from nabu.demo_data import generate


def epic(id="acme:E-1", **changes):
    return WorkItem(**{"id": id, "tenant_id": "acme", "scope": "payments", "title": "Billing migration",
                       "type": "epic", "created_at": "2026-01-01T09:00:00Z", **changes})


VIEWER = Viewer("acme", ("payments",))


class LibraryTest(unittest.TestCase):
    def test_minimal_review_and_report_serialization(self):
        with Reviewer() as reviewer:
            reviewer.load([epic()])
            report = reviewer.review("acme:E-1", viewer=VIEWER, as_of="2026-01-02T09:00:00Z")
            self.assertIsInstance(report, ReviewReport)
            self.assertEqual(report.warnings, ())
            self.assertIsNone(report.forecast.p50_days)
            self.assertEqual(json.loads(report.to_json())["run_id"], report.run_id)
            self.assertIsInstance(report.to_dict()["warnings"], list)
            self.assertIn("Billing migration", report.to_markdown())
            self.assertEqual(reviewer.reports(report.run_id, viewer=VIEWER), (report,))
            with self.assertRaises(ValidationError):
                reviewer.resume(report.run_id, viewer=VIEWER)

    def test_real_pipeline_keeps_warnings_and_forecast_in_viewer_scope(self):
        with Reviewer() as reviewer:
            reviewer.load(generate())
            viewer = Viewer("demo", ("payments",))
            report = reviewer.review("demo:PAY-111", viewer=viewer, agent_scopes=("payments", "platform"),
                                     as_of="2023-10-04T11:08:10.105835Z")
            self.assertEqual(len(report.warnings), 3)
            self.assertEqual(report.suppressed_by_ring_fence, 1)
            self.assertTrue(all("PAY-" in key for w in report.warnings for key in w.evidence))
            self.assertTrue(all("PAY-" in key for key in report.forecast.basis))

    def test_resume_across_connections_matches_uninterrupted_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp)/"history.sqlite"
            viewer = Viewer("demo", ("payments",))
            with Reviewer(database) as reviewer:
                reviewer.load(generate())
                key = reviewer.start_run(viewer=viewer)
                partial = reviewer.resume(key, viewer=viewer, stop_after=5)
                self.assertEqual((partial.status, partial.cursor), ("running", 5))
                with self.assertRaises(ValidationError):
                    reviewer.load([])
            with Reviewer(database) as reviewer:
                result = reviewer.resume(key, viewer=viewer)
                self.assertEqual((result.status, result.reviews), ("finished", 10))
                self.assertEqual(len(reviewer.reports(key, viewer=viewer)), 10)
                self.assertIsNotNone(result.warning_confirmation_rate)
                metrics = reviewer.evaluate(key, viewer=viewer, after="2023-06-01T00:00:00Z")
                self.assertGreater(metrics["forecast_pairs"], 0)
                # A repeated resume of a finished run is idempotent.
                self.assertEqual(reviewer.resume(key, viewer=viewer), result)

    def test_bad_batch_rolls_back_earlier_items(self):
        with Reviewer() as reviewer:
            with self.assertRaises(ValidationError):
                reviewer.load([epic(), {"table": "wrong", "row": {"tenant_id": "acme", "scope": "payments"}}])
            with self.assertRaises(NotFoundError):
                reviewer.review("acme:E-1", viewer=VIEWER)

    def test_cross_tenant_id_collision_preserves_original(self):
        with Reviewer() as reviewer:
            reviewer.load([epic()])
            with self.assertRaises(ScopeError):
                reviewer.load([epic(tenant_id="other", title="Other tenant")])
            report = reviewer.review("acme:E-1", viewer=VIEWER)
            self.assertEqual(report.title, "Billing migration")

    def test_cross_tenant_link_rolls_back_entire_batch(self):
        with Reviewer() as reviewer:
            records = [epic(), epic("other:E", tenant_id="other"),
                       {"table":"link", "row":{"from_id":"acme:E-1", "to_id":"other:E", "kind":"blocks",
                                                  "tenant_id":"acme", "scope":"payments"}}]
            with self.assertRaises(ScopeError):
                reviewer.load(records)
            with self.assertRaises(NotFoundError):
                reviewer.review("acme:E-1", viewer=VIEWER)

    def test_event_scope_is_checked(self):
        with Reviewer() as reviewer:
            reviewer.load([epic()])
            with self.assertRaises(ScopeError):
                reviewer.load([{"table":"event", "row":{"item_id":"acme:E-1", "kind":"estimate_changed",
                    "at":"2026-01-02T00:00:00Z", "from_value":"1", "to_value":"3",
                    "tenant_id":"acme", "scope":"platform"}}])

    def test_offsets_and_microsecond_visibility(self):
        with Reviewer() as reviewer:
            reviewer.load([epic(created_at="2026-01-01T10:00:00.200000+01:00")])
            with self.assertRaises(NotFoundError):
                reviewer.review("acme:E-1", viewer=VIEWER, as_of="2026-01-01T09:00:00.100000Z")
            report = reviewer.review("acme:E-1", viewer=VIEWER, as_of="2026-01-01T09:00:00.200000Z")
            self.assertEqual(report.as_of, "2026-01-01T09:00:00.200000Z")

    def test_work_added_fractionally_after_day_one_is_learned_as_late(self):
        with Reviewer() as reviewer:
            records = []
            for month in (1, 2):
                key=f"acme:E-{month}"
                records += [epic(key,created_at=f"2026-{month:02}-01T00:00:00Z",status_category="done",
                                 resolved_at=f"2026-{month:02}-05T00:00:00Z"),
                            epic(f"acme:T-{month}",type="task",title="Testing",parent_id=key,
                                 created_at=f"2026-{month:02}-02T00:00:00.200000Z",status_category="done",
                                 resolved_at=f"2026-{month:02}-03T00:00:00Z")]
            reviewer.load([*records,epic("acme:E-new",created_at="2026-03-01T00:00:00Z")])
            report=reviewer.review("acme:E-new",viewer=VIEWER,as_of="2026-03-02T00:00:00Z")
            self.assertTrue(any("testing" in w.claim for w in report.warnings))

    def test_zero_duration_history_does_not_crash_estimate_mining(self):
        with Reviewer() as reviewer:
            records=[epic(f"acme:T-{i}",type="task",estimate=1,status_category="done",
                          resolved_at="2026-01-01T09:00:00Z") for i in range(4)]
            reviewer.load([*records,epic()])
            report=reviewer.review("acme:E-1",viewer=VIEWER)
            self.assertEqual(report.warnings,())

    def test_invalid_input_does_not_partially_load(self):
        for changes in ({"estimate": float("nan")}, {"estimate": -1}, {"estimate": True},
                        {"created_at": "2026-01-01"}, {"created_at": None}, {"labels":"security"},
                        {"started_at":"2025-01-01T00:00:00Z"}, {"status_category":"unknown"}):
            with self.subTest(changes=changes), Reviewer() as reviewer:
                with self.assertRaises(ValidationError):
                    reviewer.load([epic(), epic("acme:E-2", **changes)])
                with self.assertRaises(NotFoundError):
                    reviewer.review("acme:E-1", viewer=VIEWER)

    def test_saved_runs_require_correct_tenant_and_scope(self):
        with Reviewer() as reviewer:
            key = reviewer.start_run(viewer=VIEWER)
            with self.assertRaises(NotFoundError):
                reviewer.run_state(key, viewer=Viewer("other", ("payments",)))
            with self.assertRaises(ScopeError):
                reviewer.reports(key, viewer=Viewer("acme", ()))
            with self.assertRaises(ScopeError):
                reviewer.start_run(viewer=VIEWER, agent_scopes=("platform",))

    def test_empty_viewer_remains_empty(self):
        with Reviewer() as reviewer:
            reviewer.load([epic()])
            viewer = Viewer("acme", ())
            key = reviewer.start_run(viewer=viewer)
            result = reviewer.resume(key, viewer=viewer)
            self.assertEqual((result.status, result.reviews), ("finished", 0))
            self.assertEqual(reviewer.reports(key, viewer=viewer), ())
            with self.assertRaises(NotFoundError):
                reviewer.review("acme:E-1", viewer=viewer)

    def test_closed_reviewer_and_invalid_configuration(self):
        reviewer = Reviewer()
        reviewer.close(); reviewer.close()
        with self.assertRaises(ClosedReviewerError):
            reviewer.load([])
        with self.assertRaises(ValidationError):
            Viewer("acme", "payments")
        with self.assertRaises(ValidationError):
            Reviewer(provider="unknown")
        with self.assertRaises(ValidationError):
            Reviewer(provider=object())

    def test_invalid_limits_fail_before_execution(self):
        with Reviewer() as reviewer:
            key = reviewer.start_run(viewer=VIEWER)
            for value in (-1, True, 1.5):
                with self.subTest(value=value), self.assertRaises(ValidationError):
                    reviewer.resume(key, viewer=VIEWER, stop_after=value)
                with self.assertRaises(ValidationError):
                    reviewer.resume(key, viewer=VIEWER, max_calls=value)

    def test_jsonl_parse_failure_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp, Reviewer() as reviewer:
            path = Path(tmp)/"tickets.jsonl"
            path.write_text(json.dumps(epic().to_record())+'\nnot json\n',encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "line 2"):
                reviewer.load_jsonl(path)
            with self.assertRaises(NotFoundError):
                reviewer.review("acme:E-1", viewer=VIEWER)


class ProvidersTest(unittest.TestCase):
    def test_offline_default_ignores_api_credentials(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY":"test-not-a-real-key"}), patch("nabu.api.AnthropicProvider") as live:
            with Reviewer() as reviewer:
                reviewer.load([epic()])
                reviewer.review("acme:E-1",viewer=VIEWER)
            live.assert_not_called()

    def test_reviewer_closes_only_the_provider_it_constructed(self):
        provider=Mock()
        with patch("nabu.api.AnthropicProvider",return_value=provider):
            with Reviewer(provider="anthropic"):
                pass
        provider.close.assert_called_once()

    def test_custom_provider_and_nested_validation_recovery(self):
        class Provider:
            calls=0
            def generate(self, **request):
                self.calls+=1
                return {"summary": 42} if self.calls < 2 else {"summary":"Provider summary"}
        provider = Provider()
        with Reviewer(provider=provider) as reviewer:
            reviewer.load([epic()])
            report = reviewer.review("acme:E-1", viewer=VIEWER)
            self.assertEqual(report.summary, "Provider summary")
            self.assertEqual(provider.calls, 2)

    def test_budget_is_preserved_across_resume(self):
        class Provider:
            calls=0
            def generate(self, **request):
                self.calls+=1
                raise OSError("unavailable")
        provider = Provider()
        with Reviewer(provider=provider) as reviewer:
            viewer = Viewer("demo", ("payments",))
            reviewer.load(generate())
            key = reviewer.start_run(viewer=viewer)
            partial = reviewer.resume(key, viewer=viewer, stop_after=5, max_calls=1)
            self.assertEqual(partial.model_calls, 1)
            result = reviewer.resume(key, viewer=viewer, max_calls=1)
            self.assertEqual(result.model_calls, 1)
            self.assertEqual(provider.calls, 1)

    def test_interrupted_review_leaves_no_run_and_can_retry(self):
        class Provider:
            interrupt=True
            def generate(self, **request):
                if self.interrupt:
                    self.interrupt=False
                    raise KeyboardInterrupt()
                return {"summary":"Recovered"}
        with Reviewer(provider=Provider()) as reviewer:
            reviewer.load([epic()])
            with self.assertRaises(KeyboardInterrupt):
                reviewer.review("acme:E-1", viewer=VIEWER, run_id="retry-me")
            with self.assertRaises(NotFoundError):
                reviewer.run_state("retry-me", viewer=VIEWER)
            report = reviewer.review("acme:E-1", viewer=VIEWER, run_id="retry-me")
            self.assertEqual(report.summary, "Recovered")

    def test_anthropic_adapter_request_and_truncation(self):
        requests=[]
        def create(**kwargs):
            requests.append(kwargs)
            return SimpleNamespace(stop_reason="end_turn",content=[SimpleNamespace(type="text",text='{"summary":"ok"}')])
        client=SimpleNamespace(messages=SimpleNamespace(create=create))
        provider=AnthropicProvider(client=client,model="test-model")
        self.assertEqual(provider.generate(agent="writer",system="system",payload={},schema={}), {"summary":"ok"})
        self.assertEqual(requests[0]["model"],"test-model")
        self.assertEqual(requests[0]["output_config"]["format"]["type"],"json_schema")
        for timeout in (0, -1, float("nan"), True):
            with self.assertRaises(ValidationError):
                AnthropicProvider(client=client,timeout=timeout)
        client.messages.create=lambda **kwargs: SimpleNamespace(stop_reason="max_tokens")
        with self.assertRaises(ValueError):
            provider.generate(agent="writer",system="",payload={},schema={})
