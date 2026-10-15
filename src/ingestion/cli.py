"""Producer CLI/API; admin is restricted to inspection and explicit clearing."""

import argparse
import cmd
import json
import shlex
import sqlite3
import sys
from datetime import date
from pathlib import Path

import httpx

from core.config import get_settings
from core.disk_quota import set_quota
from ingestion.adsblol.diagnostics import configure_logging
from ingestion.jobs import configure_parser, run_ingestion
from ingestion.publish import publish_range
from ingestion.store import IngestionStore


def output(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, default=str, indent=2))


def parser():
    root = argparse.ArgumentParser(
        description="Pipeline-owned ingestion SQLite; never writes serving except explicit publish"
    )
    root.add_argument(
        "--db", type=Path, help="ingestion SQLite; overrides FLIGHT_INGESTION_DATABASE_PATH"
    )
    commands = configure_parser(root)
    init = commands.add_parser("init", help="initialize a NEW ingestion store")
    init.add_argument("--quota-bytes", type=int)
    publish = commands.add_parser(
        "publish", help="publish admitted candidates, one transaction per UTC day"
    )
    publish.add_argument("--serving-db", type=Path, required=True)
    publish.add_argument("--start", type=date.fromisoformat, required=True)
    publish.add_argument("--end", type=date.fromisoformat, required=True)
    document = commands.add_parser(
        "document", help="import normalized provider evidence; never publishes automatically"
    )
    document.add_argument(
        "file", type=Path, help="one normalized evidence object for IngestionStore.document"
    )
    return root


def run(arguments, *, database_path=None, interactive=False):
    arguments = list(arguments)
    args = parser().parse_args(arguments)
    path = (
        (args.db or database_path or get_settings().ingestion_database_path).expanduser().resolve()
    )
    if args.source is None and not interactive:
        LocalShell(path).cmdloop()
        return
    if args.source == "init":
        store = IngestionStore(path)
        store.initialize()
        if args.quota_bytes is not None:
            set_quota(path, args.quota_bytes)
        output(dict(initialized=str(path), role="ingestion"))
    elif args.source == "publish":
        for result in publish_range(path, args.serving_db, args.start, args.end):
            output(result)
    elif args.source == "document":
        data = json.loads(args.file.read_text())
        store = IngestionStore(path)
        store.initialize()
        store.document(**data)
        output(dict(stored=True, database=str(path), serving_changed=False))
    else:
        run_ingestion(args, path, get_settings(), output, interactive=interactive)


class LocalShell(cmd.Cmd):
    prompt = "ingest> "

    def __init__(self, path):
        super().__init__()
        self.database_path = Path(path)
        self.intro = f"Producer tools: {path}. Type help; quit exits. No manual record editing."

    def default(self, line):
        try:
            run(shlex.split(line), database_path=self.database_path, interactive=True)
        except SystemExit:
            pass
        except (EOFError, KeyboardInterrupt):
            print("Command cancelled; committed days preserved.")
        except Exception as error:
            print(f"Error: {error}")

    def emptyline(self):
        pass

    def do_help(self, arg):
        self.default(arg + " --help") if arg else parser().print_help()

    def do_quit(self, arg):
        return True

    def do_EOF(self, arg):
        return True


def main(arguments=None):
    configure_logging()
    try:
        run(sys.argv[1:] if arguments is None else arguments)
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(130)
    except (ValueError, OSError, sqlite3.Error, httpx.HTTPError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
