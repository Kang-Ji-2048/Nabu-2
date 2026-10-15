"""Command line: python -m nabu <command>."""

import argparse
import json
import sys

from . import demo_data, replay
from .store import connect, load_jsonl


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nabu", description="Nabu delivery risk reviewer")
    parser.add_argument("--db", default="data/nabu.sqlite")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo-data", help="write synthetic history to a JSONL file")
    demo.add_argument("--out", default="data/demo.jsonl")

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
