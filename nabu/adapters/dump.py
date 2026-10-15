"""Read TAWOS's mysqldump data without executing SQL or requiring a server.

Intentionally supports CREATE TABLE column lists and single-line extended INSERT
VALUES statements emitted by mysqldump, not arbitrary SQL expressions.
"""

from collections import Counter, defaultdict
import json
from pathlib import Path
import re

from .extract import QUERIES


CREATE = re.compile(r"CREATE TABLE `([^`]+)` \(")
COLUMN = re.compile(r"\s+`([^`]+)` ")
INSERT = re.compile(r"INSERT INTO `([^`]+)`(?: \(([^)]+)\))? VALUES ")
TOKEN = re.compile(r"\s*(?:'((?:[^'\\]+|\\.|'')*)'|(NULL)\b|(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)|([(),;]))", re.DOTALL)
ESCAPE = re.compile(r"\\(.)|''", re.DOTALL)
ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a", "%": "\\%", "_": "\\_"}


def unescape(match):
    char = match.group(1)
    return "'" if char is None else ESCAPES.get(char, char)


def values(statement):
    """Yield tuples while rejecting unsupported syntax and incomplete records."""
    position = 0
    state = "open"
    row = []
    while position < len(statement):
        token = TOKEN.match(statement, position)
        if not token:
            raise ValueError(f"Unsupported dump value at offset {position}")
        position = token.end()
        text, null, number, punctuation = token.groups()
        if state == "open":
            if punctuation != "(":
                raise ValueError("Expected opening tuple")
            row = []
            state = "value"
        elif state == "value":
            if punctuation:
                raise ValueError("Expected a literal value")
            row.append(ESCAPE.sub(unescape, text) if text is not None else None if null else float(number) if any(c in number for c in ".eE") else int(number))
            state = "separator"
        elif state == "separator":
            if punctuation == ",":
                state = "value"
            elif punctuation == ")":
                yield row
                state = "next"
            else:
                raise ValueError("Expected comma or closing tuple")
        elif state == "next":
            if punctuation == ",":
                state = "open"
            elif punctuation == ";":
                if statement[position:].strip():
                    raise ValueError("Unexpected content after INSERT terminator")
                return
            else:
                raise ValueError("Expected next tuple or INSERT terminator")
    raise ValueError("Incomplete INSERT statement")


def dump_rows(path: Path, tables: set[str]):
    columns = {}
    current = None
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.startswith("CREATE TABLE "):
                match = CREATE.match(line)
                current = match.group(1) if match else None
                if current:
                    columns[current] = []
            elif current is not None:
                match = COLUMN.match(line)
                if match:
                    columns[current].append(match.group(1))
                elif line.startswith(")"):
                    current = None
            elif line.startswith("INSERT INTO "):
                match = INSERT.match(line)
                if not match:
                    raise ValueError(f"Unsupported INSERT header at line {line_number}")
                table, explicit = match.groups()
                if table not in tables:
                    continue
                names = [part.strip().strip("`") for part in explicit.split(",")] if explicit else columns.get(table)
                if not names:
                    raise ValueError(f"No column schema for {table}")
                try:
                    for row in values(line[match.end():]):
                        if len(row) != len(names):
                            raise ValueError(f"Expected {len(names)} columns; found {len(row)}")
                        yield table, dict(zip(names, row))
                except ValueError as exc:
                    raise ValueError(f"{table}, dump line {line_number}: {exc}") from exc


def profile_dump(path: Path) -> dict:
    projects = {}
    stats = defaultdict(Counter)
    for table, row in dump_rows(path, {"Project", "Issue"}):
        if table == "Project":
            projects[row["ID"]] = row
        else:
            counts = stats[row["Project_ID"]]
            counts["issues"] += 1
            counts["estimated"] += int(row.get("Story_Point") is not None)
            counts["resolved"] += int(row.get("Resolution_Date") is not None)
            counts["epics"] += int((row.get("Type") or "").casefold() == "epic")
            counts["sprint_members"] += int(row.get("Sprint_ID") is not None)
    if not projects or not stats:
        raise ValueError("Dump contains no project/issue data")
    return {"source": str(path), "projects": sorted([
        {"key": row["Project_Key"], "name": row["Name"], **stats[project_id]}
        for project_id, row in projects.items()
    ], key=lambda row: row.get("issues", 0), reverse=True)}


def export_dump(path: Path, project: str, output: Path) -> dict:
    if output.exists() or output.resolve() == path.resolve():
        raise ValueError("Choose a new output path; existing exports cannot be overwritten")
    # First pass: source tables are alphabetical, so Issue precedes Project.
    projects = [row for _, row in dump_rows(path, {"Project"}) if row["Project_Key"] == project]
    if len(projects) != 1:
        raise ValueError(f"Expected one project for {project!r}; found {len(projects)}")
    project_id = projects[0]["ID"]
    issue_ids = {row["ID"] for _, row in dump_rows(path, {"Issue"}) if row["Project_ID"] == project_id}
    if not issue_ids:
        raise ValueError("Project has no issues")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    counts = Counter()
    # Only source IDs are kept in memory. Record payloads stream to the export.
    with partial.open("x", encoding="utf-8", newline="\n") as stream:
        for table, row in dump_rows(path, set(QUERIES)):
            if table == "Project":
                selected = row.get("ID") == project_id
            elif table == "Issue":
                selected = row.get("ID") in issue_ids
            elif table in {"Component", "Sprint", "Version"}:
                selected = row.get("Project_ID") == project_id
            else:
                selected = row.get("Issue_ID") in issue_ids
            if selected:
                stream.write(json.dumps({"table": table, "row": row}, ensure_ascii=False) + "\n")
                counts[table] += 1
    if counts["Project"] != 1 or counts["Issue"] != len(issue_ids):
        raise ValueError("Dump changed during extraction or export is incomplete; partial file retained")
    partial.rename(output)
    return dict(counts)
