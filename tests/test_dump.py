import json
from pathlib import Path
import tempfile
import unittest

from nabu.adapters.dump import values, dump_rows, export_dump, profile_dump
from nabu.adapters.tawos import convert
from nabu.store import connect, load_jsonl


class DumpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_mysql_literals(self):
        self.assertEqual(list(values(r"(1,NULL,'NULL','',0,-2.5,1e2,'a\nb\t\'c\\d','it''s','\0\Z');")),
                         [[1, None, "NULL", "", 0, -2.5, 100.0, "a\nb\t'c\\d", "it's", "\0\x1a"]])
        self.assertEqual(list(values("(1,'a,b);'),(2,'last');\n")), [[1, "a,b);"], [2, "last"]])

    def test_unsupported_or_truncated_sql_fails(self):
        for source in ("(1,now());", "(1,'unfinished", "(1)", "(1,);", "(1);DROP TABLE X;", "(1),;"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                list(values(source))

    def fixture(self):
        path = self.root / "source.sql"
        # DDL is read as schema metadata; no SQL is executed by the parser.
        path.write_text("""CREATE TABLE `Project` (
  `ID` int,
  `Project_Key` text,
  `Name` text
) ENGINE=InnoDB;
INSERT INTO `Project` VALUES (1,'DEMO','Demo'),(2,'OTHER','Other');
CREATE TABLE `Issue` (
  `ID` int,
  `Project_ID` int,
  `Issue_Key` text,
  `Title` text,
  `Creation_Date` datetime,
  `Story_Point` double,
  `Type` text
) ENGINE=InnoDB;
INSERT INTO `Issue` VALUES (10,1,'DEMO-1','Example','2021-01-01',3,'Epic'),(20,2,'OTHER-1','Other','2021-01-01',NULL,'Task');
CREATE TABLE `Comment` (
  `ID` int,
  `Issue_ID` int,
  `Comment` text
) ENGINE=InnoDB;
INSERT INTO `Comment` VALUES (100,10,'One'),(200,20,'Two');
""", encoding="utf-8")
        return path

    def test_dump_export_to_canonical_database(self):
        source = self.fixture()
        output = self.root / "demo.jsonl"
        report = profile_dump(source)
        self.assertEqual(report["projects"][0]["estimated"], 1)
        counts = export_dump(source, "DEMO", output)
        self.assertEqual(counts, {"Project": 1, "Issue": 1, "Comment": 1})
        records = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual([r["row"]["ID"] for r in records], [1, 10, 100])
        canonical = self.root / "canonical.jsonl"
        self.assertEqual(convert(output, canonical)["work_items"], 1)
        self.assertEqual(load_jsonl(connect(self.root / "nabu.sqlite"), canonical)["work_item"], 1)

    def test_column_mismatch_fails(self):
        source = self.root / "bad.sql"
        source.write_text("INSERT INTO `Issue` (`ID`,`Title`) VALUES (1);\n")
        with self.assertRaisesRegex(ValueError, "Expected 2 columns"):
            list(dump_rows(source, {"Issue"}))
