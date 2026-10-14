"""Local development client. Commands call the same core used by the API."""

import argparse
import cmd
import json
import shlex
import sqlite3
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import httpx

from admin.ingestion import run_ingestion
from admin.management import (
    ask,
    create_airport,
    delete_flight,
    edit_flight,
    query_database,
    route_request,
    yes_no,
)
from core.config import LOCAL_DATA_DIR, get_settings
from core.connections import Airport, AirportTransfer, RebuildRequest
from core.disk_quota import policy, set_quota, storage_status
from core.models import FlightRecord, FlightStatus
from core.planning import rebuild_from_snapshot
from core.routing.debug import replay, replay_document
from core.routing.models import RouteSearchRequest, airport_code, utc_instant
from core.routing.search import search_routes
from ingestion.adsblol.diagnostics import configure_logging
from ingestion.updater import FlightUpdater
from server.reader import FlightReader


def timestamp(value):
    return utc_instant(datetime.fromisoformat(value.replace("Z", "+00:00")))


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def write_flight_export(path, records):
    """Keep the previous export intact if a remote chunk fails halfway through."""
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".partial",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write("[\n")
            first = True
            for record in records:
                if not first:
                    output.write(",\n")
                output.write(json.dumps(record.model_dump(mode="json")))
                first = False
            output.write("\n]\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def query_limit(value):
    limit = int(value)
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    return limit


def output(value):
    def encode(item):
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        return str(item)

    print(json.dumps(value, default=encode, indent=2))


def clear_database(path):
    path = path.resolve()
    if not path.is_relative_to(LOCAL_DATA_DIR.resolve()):
        raise ValueError("clear-data only supports data files under the project's var directory")
    FlightUpdater(path).reset()
    return FlightReader(path).stats()


def create_route(path):
    """Collect a connected sequence, then atomically save its flight facts."""
    print(f"Database: {path.resolve()}")
    print("This saves connected flight records; journeys are calculated from them.")
    print("Enter one flight per leg. Timestamps need Z or an offset such as -04:00.")
    print("Type cancel at any prompt to discard the entire route.")

    records = []
    try:
        origin = ask("Origin airport").upper()
        while True:
            print(f"Leg {len(records) + 1}: from {origin}")
            try:
                record = FlightRecord(
                    provider="manual",
                    provider_id=str(uuid4()),
                    origin_iata=origin,
                    destination_iata=ask("Destination airport").upper(),
                    flight_number=ask("Flight number"),
                    departure_scheduled=timestamp(ask("Departure (YYYY-MM-DDTHH:MM:SSZ)")),
                    arrival_scheduled=timestamp(ask("Arrival (YYYY-MM-DDTHH:MM:SSZ)")),
                    status="scheduled",
                    fetched_at=datetime.now(timezone.utc),
                )
                if records and record.departure_scheduled < records[-1].arrival_scheduled:
                    raise ValueError("Next departure must be on or after the previous arrival")
            except ValueError as error:
                print(f"Invalid leg: {error}")
                print("Re-enter this leg.")
                if not records:
                    origin = ask("Origin airport", origin).upper()
                continue
            records.append(record)
            if not yes_no("Add another leg?"):
                break
            origin = record.destination_iata
        print("Route preview (UTC):")
        for index, record in enumerate(records, 1):
            print(
                f"{index}. {record.origin_iata} -> {record.destination_iata} "
                f"{record.flight_number}: {record.departure_scheduled.isoformat()} "
                f"-> {record.arrival_scheduled.isoformat()}"
            )
        if not yes_no(f"Save {len(records)} flight(s) to {path.resolve()}?"):
            print("Route discarded; no flights saved.")
            return
    except (EOFError, KeyboardInterrupt):
        print("\nRoute cancelled; no flights saved.")
        return
    updater = FlightUpdater(path)
    updater.initialize()
    output({"upserted": updater.upsert(records), "database": path.resolve()})


def print_overview(summary):
    print(f"Database: {summary['data_file']} ({summary['journal_mode']} mode)")
    print(
        f"Flights: {summary['flights']:,}   Routes: {summary['routes']:,}   "
        f"Airlines: {summary['airlines']:,}"
    )
    print(
        f"Airports in flights: {summary['flight_airports']:,}   "
        f"Airport catalog: {summary['catalog_airports']:,}   "
        f"Transfer rules: {summary['transfers']:,}"
    )
    print(f"Airports missing catalog metadata: {summary['missing_airports']:,}")
    for label, first, last in (
        ("Departures (UTC)", "first_departure", "last_departure"),
        ("Fetched (UTC)", "oldest_fetch", "latest_fetch"),
    ):
        print(f"{label}: {summary[first] or '—'} to {summary[last] or '—'}")
    for label, key in (("Providers", "providers"), ("Statuses", "statuses")):
        counts = ", ".join(f"{name}: {count:,}" for name, count in summary[key].items())
        print(f"{label}: {counts or '—'}")
    for label, key in (
        ("Searchable time evidence", "time_bases"),
        ("Ingestion attempts", "ingestion_batches"),
    ):
        counts = ", ".join(f"{name}: {count:,}" for name, count in summary.get(key, {}).items())
        print(f"{label}: {counts or '—'}")
    print("Top routes by flight count (includes cancelled flights):")
    for route in summary["top_routes"]:
        print(f"  {route['origin_iata']} -> {route['destination_iata']}: {route['flights']:,}")
    if not summary["flights"]:
        print("  No flights yet. Use create-route or import-json to add flights.")


def disk_quota(value):
    if value.lower() in {"off", "unlimited"}:
        return None
    units = {"b": 1, "kib": 2**10, "mib": 2**20, "gib": 2**30, "tib": 2**40}
    import re

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB|TiB)?", value, re.IGNORECASE)
    if match:
        try:
            return int(Decimal(match[1]) * units[(match[2] or "B").lower()])
        except InvalidOperation:
            pass
    raise argparse.ArgumentTypeError("Use a non-negative size such as 20GiB, 500MiB, bytes, or off")


def parser():
    root = argparse.ArgumentParser(
        description="Flyji local database admin. With no command, start the interactive shell."
    )
    root.add_argument(
        "--db", type=Path, help="SQLite file; overrides FLIGHT_DATABASE_PATH and .env"
    )
    commands = root.add_subparsers(dest="command", metavar="COMMAND")
    db = commands.add_parser("db", help="show or select a SQLite database")
    db.add_argument("target", type=Path, nargs="?")
    db.add_argument("--create", action="store_true", help="explicitly initialize the Flyji schema")
    overview = commands.add_parser(
        "overview", help="database coverage, counts and fetch timestamps"
    )
    overview.add_argument("--json", action="store_true", help="print structured JSON")
    commands.add_parser("create-route", help="enter connected flight records, preview and save")
    commands.add_parser("create-airport", help="enter airport metadata, preview and save")
    storage = commands.add_parser("storage", help="database disk usage and live quota")
    actions = storage.add_subparsers(dest="storage_action", required=True)
    actions.add_parser("status", help="show database/WAL/SHM disk usage and quota")
    quota = actions.add_parser(
        "quota", help="show or change database quota; applied on next ingestion IO"
    )
    quota.add_argument("size", nargs="?", type=disk_quota, default=argparse.SUPPRESS)

    flights = commands.add_parser("flights", help="browse flight records with optional filters")
    flights.add_argument("--origin", type=airport_code)
    flights.add_argument("--destination", type=airport_code)
    flights.add_argument("--provider")
    flights.add_argument(
        "--status", choices=list(FlightStatus), help="default: all statuses, including cancelled"
    )
    flights.add_argument("--start", type=timestamp, help="inclusive departure cutoff")
    flights.add_argument("--end", type=timestamp, help="exclusive departure cutoff")
    flights.add_argument("--limit", type=query_limit, default=20)

    flight = commands.add_parser(
        "flight", help="show, edit or delete one flight by provider and ID"
    )
    actions = flight.add_subparsers(dest="flight_action", required=True)
    for name in ("show", "edit", "delete"):
        record = actions.add_parser(
            name,
            help={
                "show": "view a flight record",
                "edit": "edit interactively or replace from JSON",
                "delete": "preview and confirm deletion of one flight",
            }[name],
        )
        record.add_argument("provider")
        record.add_argument("provider_id")
        if name == "edit":
            record.add_argument(
                "--file",
                type=Path,
                help="replace from a complete FlightRecord JSON object without prompts",
            )
        if name == "delete":
            record.add_argument("--yes", action="store_true", help="skip preview and confirmation")

    search = commands.add_parser(
        "search", help="search journeys using prompts, flags or --request FILE"
    )
    search.add_argument(
        "--request", type=Path, help="full RouteSearchRequest JSON; exclusive with search flags"
    )
    search.add_argument("--origin")
    search.add_argument("--destination")
    search.add_argument("--start", type=timestamp)
    search.add_argument("--end", type=timestamp)
    search.add_argument("--horizon", type=timestamp)
    search.add_argument("--via", nargs="+", help="required intermediate airports, in order")
    search.add_argument("--avoid", nargs="+", help="airports to avoid")
    search.add_argument("--max-legs", type=int)
    search.add_argument("--min-connection", type=int)
    search.add_argument("--max-connection", type=int)
    search.add_argument("--allow-airport-change", action="store_true", default=None)
    search.add_argument(
        "--ranking", choices=("fewest_legs", "shortest_elapsed", "earliest_arrival")
    )
    search.add_argument("--limit", type=int)
    search.add_argument(
        "--trace", action="store_true", default=None, help="include developer diagnostic events"
    )
    search.add_argument("--snapshot-out", type=Path, help="save facts and results for debug replay")

    airports = commands.add_parser(
        "airports", help="find airports, or import/export the airport catalog"
    )
    airports.add_argument("--query", default="")
    airports.add_argument("--city")
    airports.add_argument("--limit", type=query_limit, default=100)
    catalog = airports.add_subparsers(dest="airport_action")
    for name in ("import", "export"):
        action = catalog.add_parser(name, help=f"{name} airport and directed transfer JSON")
        action.add_argument("path", type=Path)

    routes = commands.add_parser(
        "routes", help="summarize direct airport pairs in a departure window"
    )
    routes.add_argument("--start", required=True, type=timestamp)
    routes.add_argument("--end", required=True, type=timestamp)
    routes.add_argument("--origin", type=airport_code)
    routes.add_argument("--destination", type=airport_code)
    routes.add_argument("--limit", type=query_limit, default=20)
    for name in ("import-json", "export-json"):
        batch = commands.add_parser(name, help=f"{name.split('-')[0]} a batch of flight records")
        batch.add_argument("path", type=Path)

    inspect = commands.add_parser("inspect", help="advanced: SQLite tables and read-only SQL")
    inspections = inspect.add_subparsers(dest="inspection", required=True)
    inspections.add_parser("tables", help="list tables and their definitions")
    sql = inspections.add_parser("query", help="run one bounded SQL SELECT, including CTEs")
    sql.add_argument("sql")
    sql.add_argument("--limit", type=query_limit, default=20)

    maintenance = commands.add_parser("maintenance", help="initialize schema or clear local data")
    operations = maintenance.add_subparsers(dest="maintenance_action", required=True)
    operations.add_parser("init", help="explicitly initialize the Flyji schema")
    clear = operations.add_parser(
        "clear-data", help="delete all flights, airports and transfers; keep schema"
    )
    clear.add_argument("--yes", action="store_true", help="confirm deletion")

    debug = commands.add_parser(
        "debug", help="developer: reproduce or recalculate routing decisions"
    )
    diagnostics = debug.add_subparsers(dest="debug_action", required=True)
    replay_command = diagnostics.add_parser(
        "replay", help="reproduce saved facts/request and compare results; no DB access"
    )
    replay_command.add_argument("path", type=Path)
    continuation = diagnostics.add_parser(
        "continue-route",
        help="recalculate remaining journey after a retained prefix; save no flights",
    )
    continuation.add_argument("path", type=Path, help="RebuildRequest JSON")
    continuation.add_argument("--snapshot-out", type=Path)

    ingest = commands.add_parser("ingest", help="configure, run or inspect bounded ingestion jobs")
    ingest.add_argument(
        "ingest_action", nargs="?", choices=("run", "configure", "status"), default="run"
    )
    ingest.add_argument("--source", "--pipeline", choices=("adsblol", "csv", "aerodatabox"))
    ingest.add_argument("--airports", nargs="+")
    ingest.add_argument("--start", type=date.fromisoformat)
    ingest.add_argument("--end", type=date.fromisoformat)
    ingest.add_argument("--csv", type=Path)
    ingest.add_argument("--manifest", type=Path)
    ingest.add_argument("--airport-catalog", type=Path)
    ingest.add_argument("--filter", dest="filter_path", type=Path)
    ingest.add_argument("--data-root", type=Path)
    ingest.add_argument(
        "--output-db",
        type=Path,
        help="ADSB itinerary SQLite destination; defaults to the selected admin database",
    )
    ingest.add_argument("--boundary-manifest", type=Path)
    ingest.add_argument(
        "--budget-gib",
        type=float,
        help="daily raw/staging working-space budget; separate from storage quota",
    )
    ingest.add_argument(
        "--refresh-diagnostics",
        action="store_true",
        help="reprocess selected ADSB days to refresh diagnostic receipts; may download raw inputs again",
    )
    return root


def search_request(args):
    fields = {
        "origin": "origin",
        "destination": "destination",
        "start": "departure_start",
        "end": "departure_end",
        "horizon": "horizon_end",
        "via": "via_airport",
        "avoid": "avoid_airports",
        "max_legs": "max_legs",
        "min_connection": "minimum_connection_minutes",
        "max_connection": "maximum_connection_minutes",
        "allow_airport_change": "allow_airport_change",
        "ranking": "ranking",
        "limit": "limit",
        "trace": "trace",
    }
    data = {
        field: getattr(args, flag)
        for flag, field in fields.items()
        if getattr(args, flag) is not None
    }
    if args.request:
        if data:
            raise ValueError(
                "--request cannot be combined with search flags; edit the request JSON instead"
            )
        return RouteSearchRequest.model_validate_json(args.request.read_text())
    return RouteSearchRequest.model_validate(data) if data else route_request()


def run(arguments, *, database_path=None, interactive=False):
    args = parser().parse_args(arguments)
    # Replay is intentionally independent of database settings and files.
    if args.command == "debug" and args.debug_action == "replay":
        output(replay(read_json(args.path)))
        return
    path = (args.db or database_path or get_settings().database_path).expanduser().resolve()
    if args.command is None:
        LocalShell(path).cmdloop()
    elif args.command == "db":
        target = args.target.expanduser().resolve() if args.target is not None else path
        if args.create:
            FlightUpdater(target).initialize()
        if args.target is not None or args.create:
            query_database(target, "SELECT name FROM sqlite_schema LIMIT 1", 1)
        print(f"Database: {target}")
        return target
    elif args.command == "maintenance":
        if args.maintenance_action == "init":
            FlightUpdater(path).initialize()
            output({"initialized": path})
        else:
            if not args.yes:
                raise ValueError(
                    "clear-data deletes all flights, airports and transfers; supply --yes to confirm"
                )
            output(clear_database(path))
    elif args.command == "create-route":
        create_route(path)
    elif args.command == "create-airport":
        create_airport(path, output)
    elif args.command == "storage":
        if args.storage_action == "status":
            output(storage_status(path))
        elif hasattr(args, "size"):
            set_quota(path, args.size)
            output({"quota_bytes": args.size, "applies": "next ingestion IO"})
        else:
            state = policy(path)
            if state is None:
                raise ValueError("Initialize with maintenance init first")
            output({"quota_bytes": state["quota_bytes"]})
    elif args.command == "overview":
        summary = FlightReader(path).overview()
        output(summary) if args.json else print_overview(summary)
    elif args.command == "inspect":
        sql = (
            args.sql
            if args.inspection == "query"
            else ("SELECT name, sql FROM sqlite_schema WHERE type='table' ORDER BY name")
        )
        output(query_database(path, sql, args.limit if args.inspection == "query" else 1000))
    elif args.command == "flights":
        if args.start is not None and args.end is not None and args.end <= args.start:
            raise ValueError("end must follow start")
        output(
            FlightReader(path).list_flights(
                origin=args.origin,
                destination=args.destination,
                provider=args.provider,
                status=args.status,
                start=args.start,
                end=args.end,
                limit=args.limit,
            )
        )
    elif args.command == "flight":
        if args.flight_action == "show":
            output(FlightReader(path).flight(args.provider, args.provider_id))
        elif args.flight_action == "delete":
            delete_flight(path, args.provider, args.provider_id, args.yes, output)
        elif args.file is None:
            edit_flight(path, args.provider, args.provider_id, output)
        else:
            original = FlightReader(path).flight(args.provider, args.provider_id)
            replacement = FlightRecord.model_validate(read_json(args.file))
            output({"updated": FlightUpdater(path).update(original, replacement)})
    elif args.command == "search":
        request = search_request(args)
        if request is None:
            return
        snapshot = FlightReader(path).routing_snapshot(request)
        result = search_routes(request, snapshot)
        if args.snapshot_out:
            write_json(args.snapshot_out, replay_document("search", request, snapshot, result))
        output(result)
    elif args.command == "routes":
        if args.end <= args.start:
            raise ValueError("end must follow start")
        output(
            FlightReader(path).routes(
                args.start, args.end, args.origin, args.destination, args.limit
            )
        )
    elif args.command == "import-json":
        data = read_json(args.path)
        if not isinstance(data, list):
            raise ValueError("flight import must be a JSON array")
        records = [FlightRecord.model_validate(row) for row in data]
        updater = FlightUpdater(path)
        updater.initialize()
        output({"upserted": updater.upsert(records)})
    elif args.command == "export-json":
        write_flight_export(args.path, FlightReader(path).iter_records())
        output({"exported_to": args.path})
    elif args.command == "airports":
        if args.airport_action == "import":
            data = read_json(args.path)
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("airports"), list)
                or not isinstance(data.get("transfers", []), list)
            ):
                raise ValueError(
                    "airport catalog must contain an airports array and optional transfers array"
                )
            airports = [Airport.model_validate(row) for row in data["airports"]]
            transfers = [AirportTransfer.model_validate(row) for row in data.get("transfers", [])]
            updater = FlightUpdater(path)
            updater.initialize()
            updater.update_airports(airports, transfers)
            output({"airports": len(airports), "transfers": len(transfers)})
        elif args.airport_action == "export":
            write_json(args.path, FlightReader(path).airport_catalog())
            output({"exported_to": args.path})
        else:
            output(FlightReader(path).airports(args.city, args.query, args.limit))
    elif args.command == "debug":
        request = RebuildRequest.model_validate_json(args.path.read_text())
        snapshot = FlightReader(path).rebuild_snapshot(request)
        result = rebuild_from_snapshot(request, snapshot)
        if args.snapshot_out:
            write_json(args.snapshot_out, replay_document("rebuild", request, snapshot, result))
        output(result)
    elif args.command == "ingest":
        run_ingestion(args, path, get_settings(), output, interactive=interactive)


class LocalShell(cmd.Cmd):
    intro = "Flyji local client. Type help for commands; quit to exit."
    prompt = "flyji> "

    def __init__(self, database_path=None):
        super().__init__()
        self.database_path = (database_path or get_settings().database_path).expanduser().resolve()
        self.intro += f"\nDatabase: {self.database_path}"

    def default(self, line):
        try:
            selected = run(shlex.split(line), database_path=self.database_path, interactive=True)
            if isinstance(selected, Path):
                self.database_path = selected
        except SystemExit:
            pass
        except (EOFError, KeyboardInterrupt):
            print("\nCommand cancelled.")
        except Exception as error:
            print(f"Error: {error}")

    def do_db(self, line):
        """db [PATH] [--create]: show or switch the selected database."""
        self.default("db " + line)

    def emptyline(self):
        pass

    def do_help(self, arg):
        if arg:
            self.default(arg + " --help")
            return
        parser().print_help()
        print("Use help COMMAND for options. Start with db, overview, flights or search.")
        print("help maintenance, help inspect and help debug show secondary tools. quit exits.")

    def do_quit(self, arg):
        return True

    def do_EOF(self, arg):
        print()
        return True


def main(arguments=None):
    configure_logging()
    try:
        run(sys.argv[1:] if arguments is None else arguments)
    except (ValueError, OSError, sqlite3.Error, httpx.HTTPError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
