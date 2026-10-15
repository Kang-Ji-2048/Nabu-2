"""Command line: python -m nabu <command>."""

import argparse
import json
import os
import sys
from pathlib import Path

from . import demo_data, replay
from .store import connect, load_jsonl


def load_env(path=".env"):
    """Read KEY=value lines from .env (if present) into the environment, without overriding it."""
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv=None):
    load_env()
    parser = argparse.ArgumentParser(prog="nabu", description="Nabu delivery risk reviewer")
    parser.add_argument("--db", default="data/nabu.sqlite")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo-data", help="write synthetic history to a JSONL file")
    demo.add_argument("--out", default="data/demo.jsonl")

    profile = commands.add_parser("profile-dump", help="list TAWOS projects in the SQL dump, with data coverage")
    profile.add_argument("--input", required=True, help="path to TAWOS.sql")

    extract = commands.add_parser("extract-dump", help="export one TAWOS project from the SQL dump to JSONL")
    extract.add_argument("--input", required=True)
    extract.add_argument("--project", required=True, help="project key, e.g. XD")
    extract.add_argument("--output", required=True)

    tawos = commands.add_parser("tawos", help="convert a TAWOS project export to canonical JSONL")
    tawos.add_argument("--input", required=True)
    tawos.add_argument("--output", required=True)
    tawos.add_argument("--tenant", default="tawos")

    load = commands.add_parser("load", help="load canonical JSONL into the store")
    load.add_argument("input")

    start = commands.add_parser("replay", help="start a replay run")
    start.add_argument("--run", required=True)
    start.add_argument("--tenant", required=True)
    start.add_argument("--scopes", required=True, help="comma-separated scopes the agents may read")
    start.add_argument("--viewer-scopes", help="comma-separated scopes the reader may see (default: --scopes)")

    resume = commands.add_parser("resume", help="continue a run from its last checkpoint")
    resume.add_argument("--run", required=True)

    for sub in (start, resume):
        sub.add_argument("--offline", action="store_true", help="no Claude calls; deterministic agents only")
        sub.add_argument("--stop-after", type=int, help="pause after N steps (to demo checkpoint and resume)")
        sub.add_argument("--quiet", action="store_true")

    show = commands.add_parser("show", help="print the stored review for an epic")
    show.add_argument("--run", required=True)
    show.add_argument("--epic", required=True, help="epic id")

    args = parser.parse_args(argv)
    if args.command == "demo-data":
        print(json.dumps({"records": demo_data.write(args.out), "out": args.out}))
        return 0
    if args.command == "profile-dump":
        from .adapters.dump import profile_dump
        print(json.dumps(profile_dump(Path(args.input)), indent=2))
        return 0
    if args.command == "extract-dump":
        from .adapters.dump import export_dump
        print(json.dumps(export_dump(Path(args.input), args.project, Path(args.output))))
        return 0
    if args.command == "tawos":
        from .adapters.tawos import convert
        print(json.dumps(convert(Path(args.input), Path(args.output), tenant=args.tenant)))
        return 0
    conn = connect(args.db)
    if args.command == "load":
        print(json.dumps(load_jsonl(conn, args.input)))
    elif args.command in ("replay", "resume"):
        if args.command == "replay":
            replay.start(conn, args.run, args.tenant, args.scopes.split(","),
                         args.viewer_scopes.split(",") if args.viewer_scopes else None)
        stats = replay.run(conn, args.run, offline=True if args.offline else None,
                           stop_after=args.stop_after, quiet=args.quiet)
        print(json.dumps({k: v for k, v in stats.items() if k != "accuracy"}))
    elif args.command == "show":
        row = conn.execute("SELECT report FROM review WHERE run_id=? AND epic_id=?", (args.run, args.epic)).fetchone()
        if not row:
            print("no review stored", file=sys.stderr)
            return 1
        print(json.dumps(json.loads(row["report"]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
