"""Producer CLI/API; admin is restricted to inspection and explicit clearing."""

import argparse
import cmd
import json
import shlex
import sqlite3
import sys
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from core.config import get_settings
from core.database_roles import sqlite_file
from core.disk_quota import set_quota
from ingestion import review as review_tools
from ingestion.adoption import adopt_archive
from ingestion.adsblol.diagnostics import configure_logging
from ingestion.audit import audit_database
from ingestion.jobs import configure_parser, run_ingestion
from ingestion.publish import publish_range
from ingestion.store import IngestionStore
from ingestion.vrs import admit as admit_vrs
from ingestion.vrs import ensure_snapshot
from ingestion.vrs import import_files as import_vrs
from ingestion.vrs import report as report_vrs


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
    daily = commands.add_parser(
        "daily", help="ADSB: ingest, audit, VRS-check and publish each day before the next input"
    )
    daily.add_argument("--serving-db", type=Path, required=True)
    daily.add_argument("--start", type=date.fromisoformat, required=True)
    daily.add_argument("--end", type=date.fromisoformat, required=True)
    daily.add_argument("--finish-year", action="store_true")
    daily.add_argument(
        "--serving-authoritative",
        action="store_true",
        help="bind verified facts to serving; retain only candidates and pipeline history privately",
    )
    daily.add_argument("--data-root", type=Path)
    daily.add_argument("--offline", action="store_true")
    init = commands.add_parser("init", help="initialize a NEW ingestion store")
    init.add_argument("--quota-bytes", type=int)
    publish = commands.add_parser(
        "publish", help="publish admitted candidates, one transaction per UTC day"
    )
    publish.add_argument("--serving-db", type=Path, required=True)
    publish.add_argument(
        "--vrs",
        choices=("auto", "cached", "refresh", "skip"),
        default="auto",
        help="default: use cached VRS or fetch once if absent, then audit each publication day",
    )
    publish.add_argument(
        "--preserve-serving",
        type=Path,
        help="seed a new replacement with canonical admin state; reject later drift",
    )
    publish.add_argument("--start", type=date.fromisoformat, required=True)
    publish.add_argument("--end", type=date.fromisoformat, required=True)
    document = commands.add_parser(
        "document", help="import normalized provider evidence; never publishes automatically"
    )
    document.add_argument(
        "file", type=Path, help="one normalized evidence object for IngestionStore.document"
    )
    adopt = commands.add_parser(
        "adopt", help="copy and verify a completed legacy v5 archive into a NEW ingestion store"
    )
    adopt.add_argument("--from-db", type=Path, required=True)
    adopt.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="the archive's working directory, for its extractor lock",
    )
    adopt.add_argument(
        "--policy-db",
        type=Path,
        help="inherit this live persisted quota once; the new store then owns its policy",
    )
    adopt.add_argument(
        "--start", type=date.fromisoformat, required=True, help="first UTC INPUT day"
    )
    adopt.add_argument(
        "--end",
        type=date.fromisoformat,
        required=True,
        help="last UTC INPUT day, including arrival context",
    )
    adopt.add_argument(
        "--profiles-db",
        type=Path,
        help="preserve producer configuration from the old selected database",
    )
    adopt.add_argument("--report", type=Path)
    audit = commands.add_parser(
        "audit",
        help="bulk retained-fact audit; read-only unless --apply selects private flag corrections",
    )
    audit.add_argument(
        "--apply",
        action="store_true",
        help="apply supported flag corrections in private ingestion only",
    )
    audit.add_argument("--start", type=date.fromisoformat)
    audit.add_argument("--end", type=date.fromisoformat)
    audit.add_argument("--origin")
    audit.add_argument("--destination")
    audit.add_argument("--reason")
    audit.add_argument("--sample-limit", type=int, default=3)
    audit.add_argument("--cohort-limit", type=int, default=50)
    audit.add_argument("--report", type=Path)
    vrs = commands.add_parser(
        "vrs", help="CC0 route reference, reports and explicit best-effort admission"
    )
    vrs_actions = vrs.add_subparsers(dest="vrs_action", required=True)
    vrs_import = vrs_actions.add_parser(
        "import", help="atomically refresh routes and airport mappings"
    )
    vrs_import.add_argument("--routes-csv", type=Path)
    vrs_import.add_argument("--airports-csv", type=Path)
    vrs_admit = vrs_actions.add_parser(
        "audit",
        aliases=["admit"],
        help="record VRS matches, disagreements and absence; dry-run unless --apply",
    )
    vrs_admit.add_argument("--start", type=date.fromisoformat, required=True)
    vrs_admit.add_argument("--end", type=date.fromisoformat, required=True)
    vrs_admit.add_argument("--apply", action="store_true")
    vrs_admit.add_argument("--report", type=Path)
    vrs_report = vrs_actions.add_parser("report", help="read-only ADSB route-plausibility cohorts")
    vrs_report.add_argument("--start", type=date.fromisoformat)
    vrs_report.add_argument("--end", type=date.fromisoformat)
    vrs_report.add_argument("--cohort-limit", type=int, default=50)
    vrs_report.add_argument("--report", type=Path)
    references = commands.add_parser(
        "references", help="shared airport and operator catalog, with provenance"
    )
    actions = references.add_subparsers(dest="reference_action", required=True)
    refresh = actions.add_parser(
        "refresh", help="download public reference CSVs in memory, or import local snapshots"
    )
    refresh.add_argument("--airports-csv", type=Path)
    refresh.add_argument("--carriers-csv", type=Path)
    timezones = actions.add_parser(
        "timezones", help="enrich stored airport timezones without flight reprocessing"
    )
    timezones.add_argument("--dry-run", action="store_true")
    timezones.add_argument("--release-override", action="append", default=[], metavar="IATA")
    publish_ref = actions.add_parser(
        "publish", help="publish names/reference metadata without reprocessing flight facts"
    )
    publish_ref.add_argument("--serving-db", type=Path, required=True)
    review_tools.configure_commands(commands, writes=True)
    return root


def report_destination(path, databases):
    if path is None:
        return None
    path = Path(path).expanduser().resolve()
    family = {
        Path(str(Path(database).expanduser().resolve()) + suffix)
        for database in databases
        for suffix in ("", "-wal", "-shm", "-journal")
    }
    if sqlite_file(path) or any(
        path == member or (path.exists() and member.exists() and path.samefile(member))
        for member in family
    ):
        raise ValueError("Report output cannot overwrite a database or SQLite sidecar")
    for suffix in ("-wal", "-shm", "-journal"):
        if str(path).endswith(suffix) and sqlite_file(Path(str(path)[: -len(suffix)])):
            raise ValueError("Report output cannot overwrite a SQLite sidecar")
    return path


def save_report(path, report):
    if path is None:
        output(report)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2, default=str)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    output(
        {
            "report": str(path),
            **{
                key: report[key]
                for key in (
                    "mode",
                    "counts",
                    "findings",
                    "changes",
                    "health",
                    "role",
                    "input_days",
                    "source_changed",
                    "serving_changed",
                )
                if key in report
            },
        }
    )


def progress(event):
    print(json.dumps({"event": "ingestion_tool_progress", **event}), file=sys.stderr, flush=True)


def run(arguments, *, database_path=None, interactive=False):
    arguments = list(arguments)
    args = parser().parse_args(arguments)
    path = (
        (args.db or database_path or get_settings().ingestion_database_path).expanduser().resolve()
    )
    if args.source is None and not interactive:
        LocalShell(path).cmdloop()
        return
    if args.source == "daily":
        from ingestion.daily import daily_range

        def ingest_day(day, departure_year):
            command = ["adsblol", "run", "--start", str(day), "--end", str(day)]
            if departure_year is not None:
                command += ["--departure-year", str(departure_year)]
            if args.data_root:
                command += ["--data-root", str(args.data_root)]
            if args.offline:
                command += ["--offline"]
            run(command, database_path=path)

        for result in daily_range(
            path,
            args.serving_db,
            args.start,
            args.end,
            ingest_day=ingest_day,
            finish_year=args.finish_year,
            serving_authoritative=args.serving_authoritative,
            progress=progress,
        ):
            output(result)
    elif args.source in {"adopt", "audit"}:
        databases = [path] + ([args.from_db] if args.source == "adopt" else [])
        destination = report_destination(args.report, databases)
        if args.source == "adopt":
            result = adopt_archive(
                args.from_db,
                path,
                args.start,
                args.end,
                source_root=args.source_root,
                policy_db=args.policy_db,
                profiles_db=args.profiles_db,
                progress=progress,
            )
        else:
            result = audit_database(
                path,
                apply=args.apply,
                start=args.start,
                end=args.end,
                origin=args.origin,
                destination=args.destination,
                reason=args.reason,
                sample_limit=args.sample_limit,
                cohort_limit=args.cohort_limit,
                progress=progress,
            )
        save_report(destination, result)
    elif args.source == "review":
        destination = report_destination(args.report, [path])
        if args.review_action == "decide":
            if (args.action == "correct") != (args.replacement is not None):
                raise ValueError("--replacement is required only for a correction")
            result = review_tools.decide(
                path,
                json.loads(args.case.read_text()),
                action=args.action,
                note=args.note,
                replacement=json.loads(args.replacement.read_text()) if args.replacement else None,
            )
        else:
            result = review_tools.read_command(path, args)
        save_report(destination, result)
    elif args.source == "references":
        from ingestion.references import publish_catalog, refresh

        if args.reference_action == "timezones":
            from ingestion.airport_timezones import enrich

            output(
                enrich(
                    path,
                    dry_run=args.dry_run,
                    release=[code.upper() for code in args.release_override],
                )
            )
            return
        output(
            refresh(path, airports_path=args.airports_csv, carriers_path=args.carriers_csv)
            if args.reference_action == "refresh"
            else publish_catalog(path, args.serving_db)
        )
    elif args.source == "vrs":
        if args.vrs_action == "import":
            output(
                import_vrs(
                    IngestionStore(path),
                    routes_path=args.routes_csv,
                    airports_path=args.airports_csv,
                )
            )
        else:
            destination = report_destination(args.report, [path])
            result = (
                admit_vrs(path, start=args.start, end=args.end, apply=args.apply, progress=progress)
                if args.vrs_action in {"audit", "admit"}
                else report_vrs(
                    path, start=args.start, end=args.end, cohort_limit=args.cohort_limit
                )
            )
            save_report(destination, result)
    elif args.source == "init":
        store = IngestionStore(path)
        store.initialize()
        if args.quota_bytes is not None:
            set_quota(path, args.quota_bytes)
        output(dict(initialized=str(path), role="ingestion"))
    elif args.source == "publish":
        from ingestion.references import ensure_carriers

        ensure_carriers(path)
        if args.vrs != "skip":
            ensure_snapshot(path, refresh=args.vrs == "refresh", fetch_missing=args.vrs == "auto")
        for result in publish_range(
            path,
            args.serving_db,
            args.start,
            args.end,
            preserve_serving=args.preserve_serving,
            audit_routes=args.vrs != "skip",
        ):
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
