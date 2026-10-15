"""Export a project from a restored TAWOS MySQL database to portable JSONL."""

import json
import os
from pathlib import Path


# All SQL identifiers are constants. User input is passed as a bound parameter.
QUERIES = {
    "Project": "SELECT * FROM Project WHERE Project_Key = %s",
    "Issue": "SELECT i.* FROM Issue i JOIN Project p ON p.ID=i.Project_ID WHERE p.Project_Key=%s",
    "Change_Log": "SELECT c.* FROM Change_Log c JOIN Issue i ON i.ID=c.Issue_ID JOIN Project p ON p.ID=i.Project_ID WHERE p.Project_Key=%s",
    "Issue_Link": "SELECT l.* FROM Issue_Link l JOIN Issue i ON i.ID=l.Issue_ID JOIN Project p ON p.ID=i.Project_ID WHERE p.Project_Key=%s",
    "Comment": "SELECT c.* FROM Comment c JOIN Issue i ON i.ID=c.Issue_ID JOIN Project p ON p.ID=i.Project_ID WHERE p.Project_Key=%s",
    "Component": "SELECT c.* FROM Component c JOIN Project p ON p.ID=c.Project_ID WHERE p.Project_Key=%s",
    "Issue_Component": "SELECT c.* FROM Issue_Component c JOIN Issue i ON i.ID=c.Issue_ID JOIN Project p ON p.ID=i.Project_ID WHERE p.Project_Key=%s",
    "Sprint": "SELECT s.* FROM Sprint s JOIN Project p ON p.ID=s.Project_ID WHERE p.Project_Key=%s",
    "Version": "SELECT v.* FROM Version v JOIN Project p ON p.ID=v.Project_ID WHERE p.Project_Key=%s",
    "Fix_Version": "SELECT v.* FROM Fix_Version v JOIN Issue i ON i.ID=v.Issue_ID JOIN Project p ON p.ID=i.Project_ID WHERE p.Project_Key=%s",
    "Affected_Version": "SELECT v.* FROM Affected_Version v JOIN Issue i ON i.ID=v.Issue_ID JOIN Project p ON p.ID=i.Project_ID WHERE p.Project_Key=%s",
}


def export_mysql(project: str, output: Path) -> dict:
    try:
        import pymysql
    except ImportError as exc:
        raise ValueError('Install the MySQL extra: pip install -e ".[mysql]"') from exc

    if not os.environ.get("TAWOS_MYSQL_DATABASE"):
        raise ValueError("Set TAWOS_MYSQL_DATABASE to the restored source database name")
    if output.exists():
        raise ValueError(f"Export already exists: {output}; choose a new filename")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    counts = {}
    connection = pymysql.connect(
        host=os.environ.get("TAWOS_MYSQL_HOST", "localhost"),
        port=int(os.environ.get("TAWOS_MYSQL_PORT", "3306")),
        user=os.environ.get("TAWOS_MYSQL_USER", "root"),
        password=os.environ.get("TAWOS_MYSQL_PASSWORD", ""),
        database=os.environ["TAWOS_MYSQL_DATABASE"],
        charset="utf8mb4", cursorclass=pymysql.cursors.SSDictCursor,
        connect_timeout=15, read_timeout=300, write_timeout=30,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            with partial.open("x", encoding="utf-8", newline="\n") as stream:
                for table, query in QUERIES.items():
                    cursor.execute(query, (project,))
                    counts[table] = 0
                    for row in cursor:
                        stream.write(json.dumps({"table": table, "row": row}, default=str, ensure_ascii=False) + "\n")
                        counts[table] += 1
                    if table == "Project" and counts[table] != 1:
                        raise ValueError(f"Expected one project for {project!r}; found {counts[table]}")
        partial.rename(output)
    finally:
        connection.rollback()
        connection.close()
    return counts
