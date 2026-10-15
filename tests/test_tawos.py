import json
import tempfile
import unittest
from pathlib import Path

from nabu.adapters.tawos import convert
from nabu.store import ScopedStore, connect, load_jsonl

ROWS = [
    ("Project", {"ID": 1, "Project_Key": "XD", "Name": "Synthetic"}),
    ("Issue", {"ID": 1, "Project_ID": 1, "Issue_Key": "XD-1", "Type": "Epic", "Title": "Epic", "Status": "Open",
               "Creation_Date": "2021-01-01 09:00:00"}),
    ("Issue", {"ID": 2, "Project_ID": 1, "Issue_Key": "XD-2", "Type": "Story", "Title": "Schema", "Status": "Closed",
               "Resolution": "Fixed", "Story_Point": 3, "Creation_Date": "2021-01-01 10:00:00",
               "Resolution_Date": "2021-01-10 09:00:00"}),
    ("Issue", {"ID": 3, "Project_ID": 1, "Issue_Key": "XD-3", "Type": "Task", "Title": "Tests", "Status": "Closed",
               "Resolution": "Won't Fix", "Creation_Date": "2021-01-05 10:00:00", "Resolution_Date": "2021-01-12 09:00:00"}),
    ("Change_Log", {"ID": 10, "Issue_ID": 2, "Field": "Epic Link", "Creation_Date": "2021-01-01 10:00:00",
                    "From_String": None, "To_String": "XD-1"}),
    ("Change_Log", {"ID": 11, "Issue_ID": 1, "Field": "Epic Child", "Creation_Date": "2021-01-08 10:00:00",
                    "From_String": None, "To_String": "XD-3"}),
    ("Change_Log", {"ID": 12, "Issue_ID": 2, "Field": "status", "Creation_Date": "2021-01-03 10:00:00",
                    "From_String": "Open", "To_String": "In Progress"}),
    ("Change_Log", {"ID": 13, "Issue_ID": 3, "Field": "Link", "Creation_Date": "2021-01-06 10:00:00",
                    "From_String": None, "To_String": "This issue is blocked by XD-2"}),
    ("Issue_Link", {"ID": 20, "Issue_ID": 3, "Target_Issue_ID": 2, "Description": "is blocked by"}),
    ("Issue_Link", {"ID": 21, "Issue_ID": 2, "Target_Issue_ID": 3, "Description": "blocks"}),
    ("Component", {"ID": 30, "Project_ID": 1, "Name": "Database"}),
    ("Issue_Component", {"Issue_ID": 2, "Component_ID": 30}),
]


class TawosAdapterTest(unittest.TestCase):
    def test_converts_membership_links_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            export, canonical = Path(tmp) / "xd.jsonl", Path(tmp) / "canonical.jsonl"
            export.write_text("".join(json.dumps({"table": t, "row": r}) + "\n" for t, r in ROWS))
            report = convert(export, canonical, tenant="t")
            self.assertEqual((report["work_items"], report["links"], report["with_epic"]), (3, 1, 2))
            self.assertEqual(report["epics_closed_from_children"], 1)
            conn = connect(Path(tmp) / "nabu.sqlite")
            load_jsonl(conn, canonical)
            full = ScopedStore(conn, "t", ["XD"], "2099-01-01T00:00:00Z")
            story, task = full.get("t:XD-2"), full.get("t:XD-3")
            self.assertEqual((story["parent_id"], story["components"], story["started_at"]),
                             ("t:XD-1", ["Database"], "2021-01-03T10:00:00Z"))
            self.assertEqual((task["parent_id"], task["parent_at"], task["status_category"]),
                             ("t:XD-1", "2021-01-08T10:00:00Z", "cancelled"))
            epic = full.get("t:XD-1")  # never closed in Jira; finished when its last ticket resolved
            self.assertEqual((epic["status_category"], epic["resolved_at"]), ("done", "2021-01-12T09:00:00Z"))
            link = full.links("t:XD-3")[0]
            self.assertEqual((link["from_id"], link["to_id"], link["created_at"]), ("t:XD-2", "t:XD-3", "2021-01-06T10:00:00Z"))
            # On day one the epic held only the story; the task joined later.
            day_one = full.at("2021-01-06T00:00:00Z")
            self.assertEqual([c["id"] for c in day_one.children("t:XD-1")], ["t:XD-2"])
            self.assertEqual(day_one.links("t:XD-2"), [])


if __name__ == "__main__":
    unittest.main()
