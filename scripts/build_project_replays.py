"""Build authentic project replays from a local TAWOS dump, without model calls.

Example:
  python scripts/build_project_replays.py --source /path/TAWOS.sql --projects STL MESOS
Raw extracts and SQLite files stay in the selected work directory; only recorded
dashboard data and provenance are written to frontend/runs.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nabu.adapters.dump import dump_rows
from nabu.adapters.tawos import convert
from nabu import replay
from nabu.store import connect, load_jsonl
from nabu.ui import state

NAMES = {"STL": "Hyperledger Sawtooth", "MESOS": "Apache Mesos"}
SOURCE = "https://github.com/SOLAR-group/TAWOS"


def normalize_log_times(body):
    """Give start/resume messages the preceding checkpoint's simulated time.

    The static player derives its clock from the last visible log row. A null
    resume timestamp otherwise survives every cutoff and can blank the view.
    Preserve the original null timestamp separately for provenance.
    """
    previous = next((row["sim_at"] for row in body["log"] if row["sim_at"]), body["run"]["clock"])
    for row in body["log"]:
        if row["sim_at"]:
            previous = row["sim_at"]
        else:
            row["source_sim_at"] = None
            row["sim_at"] = previous


def extract(source, projects, directory):
    """Share the large dump scans across projects; keep only adapter inputs."""
    metadata = {row["Project_Key"]: row for _, row in dump_rows(source, {"Project"})
                if row["Project_Key"] in projects}
    missing = set(projects) - metadata.keys()
    if missing:
        raise ValueError(f"Projects not present in dump: {sorted(missing)}")
    by_id = {row["ID"]: key for key, row in metadata.items()}
    issue_ids, counts, streams = {}, {key: Counter() for key in projects}, {}
    paths = {key: directory / f"{key}-source.jsonl" for key in projects}
    try:
        for key, path in paths.items():
            streams[key] = path.with_suffix(".partial").open("x", encoding="utf-8")

        def write(key, table, row):
            streams[key].write(json.dumps({"table": table, "row": row}, ensure_ascii=False) + "\n")
            counts[key][table] += 1

        for key, row in metadata.items():
            write(key, "Project", row)
        print("Extracting project issues", flush=True)
        for _, row in dump_rows(source, {"Issue"}):
            key = by_id.get(row["Project_ID"])
            if key:
                issue_ids[row["ID"]] = key
                write(key, "Issue", row)
        print("Extracting change history, components and links", flush=True)
        selected = {"Change_Log", "Component", "Issue_Component", "Issue_Link"}
        for number, (table, row) in enumerate(dump_rows(source, selected), 1):
            key = by_id.get(row.get("Project_ID")) if table == "Component" else issue_ids.get(row.get("Issue_ID"))
            if key:
                write(key, table, row)
            if number % 500000 == 0:
                print(f"Scanned {number:,} history/relationship rows", flush=True)
    finally:
        for stream in streams.values():
            stream.close()
    for key, path in paths.items():
        path.with_suffix(".partial").rename(path)
        print(f"{key} extract: {dict(counts[key])}", flush=True)
    return paths, metadata, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--projects", nargs="+", default=["STL", "MESOS"])
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("frontend/runs"))
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    print("Reading project metadata", flush=True)
    paths, metadata, counts = extract(args.source, args.projects, args.work_dir)
    for key in args.projects:
        canonical = args.work_dir / f"{key}-canonical.jsonl"
        conversion = convert(paths[key], canonical)
        print(f"{key} canonical: {conversion}", flush=True)
        database = args.work_dir / f"{key}.sqlite"
        conn = connect(database)
        try:
            load_jsonl(conn, canonical)
            run_id = key.lower() + "-demo"
            replay.start(conn, run_id, "tawos", [key])
            print(f"{key}: replaying history offline", flush=True)
            # Preserve checkpoints and emit occasional progress for long histories.
            while conn.execute("SELECT status FROM run WHERE id=?", (run_id,)).fetchone()[0] != "finished":
                stats = replay.run(conn, run_id, offline=True, quiet=True, stop_after=20)
                cursor = conn.execute("SELECT cursor FROM run WHERE id=?", (run_id,)).fetchone()[0]
                print(f"{key}: step {cursor}, {stats['reviews']} reviews", flush=True)
            body = state(conn, run_id, 0)
            while True:
                more = state(conn, run_id, body["log"][-1]["seq"] if body["log"] else 0)["log"]
                if not more:
                    break
                body["log"].extend(more)
            normalize_log_times(body)
            dates = sorted(review["as_of"] for review in body["reviews"])
            if not dates:
                raise ValueError(f"{key}: no reviewable epics")
            cutoff = dates[min(len(dates) - 1, int(len(dates) * 0.8))]
            name = NAMES.get(key, metadata[key]["Name"])
            provenance = {"project": key, "name": name, "dataset": "TAWOS v1.1", "source": SOURCE,
                          "mode": "offline deterministic replay", "source_counts": dict(counts[key]),
                          "canonical_counts": dict(conversion), "reviews": len(body["reviews"]),
                          "cutoff": cutoff, "model_calls": 0}
            body["provenance"] = provenance
            (args.output / f"{run_id}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            (args.output / f"{run_id}-provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
            index_path = args.output / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
            index = [entry for entry in index if entry["id"] != run_id]
            position = next((i for i, entry in enumerate(index) if entry["id"] == "demo"), len(index))
            index.insert(position, {"id": run_id, "name": name, "project": key, "status": "recorded",
                                    "clock": cutoff, "pause_at": cutoff})
            index_path.write_text(json.dumps(index, indent=1), encoding="utf-8")
            print(f"{key}: recorded {len(body['reviews'])} reviews / {len(body['log'])} log rows", flush=True)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
