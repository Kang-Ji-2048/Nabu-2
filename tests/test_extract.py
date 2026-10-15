from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from nabu.adapters.extract import export_mysql


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "project.jsonl"
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.driver = SimpleNamespace(connect=MagicMock(return_value=self.connection), cursors=SimpleNamespace(SSDictCursor=object()))

    def test_success_publishes_export_and_preserves_timestamps(self):
        self.cursor.__iter__.side_effect = [
            iter([{"ID": 1, "Project_Key": "DEMO"}]),
            iter([{"ID": 10, "Creation_Date": datetime(2021, 1, 1)}]),
            *[iter([]) for _ in range(9)],
        ]
        with patch.dict("sys.modules", {"pymysql": self.driver}), patch.dict(os.environ, {"TAWOS_MYSQL_DATABASE": "TAWOS"}):
            counts = export_mysql("DEMO", self.output)
        self.assertEqual(counts["Issue"], 1)
        rows = [json.loads(line) for line in self.output.read_text().splitlines()]
        self.assertEqual(rows[1]["row"]["Creation_Date"], "2021-01-01 00:00:00")
        self.assertFalse(self.output.with_suffix(".jsonl.partial").exists())
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()
        # A malicious project string must remain a bound parameter.
        project_calls = [call for call in self.cursor.execute.call_args_list if len(call.args) == 2]
        self.assertTrue(all(call.args[1] == ("DEMO",) for call in project_calls))

    def test_failed_export_is_not_published(self):
        self.cursor.__iter__.side_effect = [iter([])]
        with patch.dict("sys.modules", {"pymysql": self.driver}), patch.dict(os.environ, {"TAWOS_MYSQL_DATABASE": "TAWOS"}):
            with self.assertRaisesRegex(ValueError, "Expected one project"):
                export_mysql("MISSING", self.output)
        self.assertFalse(self.output.exists())
        self.assertTrue(self.output.with_suffix(".jsonl.partial").exists())
        self.connection.close.assert_called_once()

    def test_existing_export_is_not_overwritten(self):
        self.output.write_text("keep")
        with patch.dict("sys.modules", {"pymysql": self.driver}), patch.dict(os.environ, {"TAWOS_MYSQL_DATABASE": "TAWOS"}):
            with self.assertRaisesRegex(ValueError, "already exists"):
                export_mysql("DEMO", self.output)
        self.assertEqual(self.output.read_text(), "keep")
        self.driver.connect.assert_not_called()
