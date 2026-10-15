import unittest

from scripts.build_project_replays import normalize_log_times


class RecordedProjectTest(unittest.TestCase):
    def test_checkpoint_messages_do_not_blank_a_cutoff_snapshot(self):
        early, late = "2017-01-01T00:00:00Z", "2018-01-01T00:00:00Z"
        body = {"run": {"clock": late}, "log": [
            {"sim_at": None, "kind": "start"},
            {"sim_at": early, "kind": "review"},
            {"sim_at": None, "kind": "resume"},
            {"sim_at": late, "kind": "review"},
            {"sim_at": None, "kind": "resume"},
        ]}
        normalize_log_times(body)
        visible = [row for row in body["log"] if row["sim_at"] <= early]
        self.assertEqual(visible[-1]["sim_at"], early)
        self.assertEqual(len(visible), 3)
        self.assertEqual(body["log"][-1]["sim_at"], late)
        self.assertIsNone(body["log"][-1]["source_sim_at"])

    def test_real_simulated_times_are_preserved(self):
        row = {"sim_at": "2017-01-01T00:00:00Z", "message": "published review"}
        body = {"run": {"clock": row["sim_at"]}, "log": [dict(row)]}
        normalize_log_times(body)
        self.assertEqual(body["log"], [row])
