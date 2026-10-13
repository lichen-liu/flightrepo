"""Local development client. Commands call the same core used by the API."""

import argparse
import asyncio
import cmd
import json
import shlex
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

from core.config import LOCAL_DATA_DIR, get_settings
from core.connections import Airport, AirportTransfer, RebuildRequest
from core.itineraries import build_itineraries
from core.models import FlightRecord, IngestRequest
from core.planning import rebuild_from_snapshot
from core.routing.debug import replay, replay_document
from core.routing.models import RouteSearchRequest, utc_instant
from core.routing.search import search_routes
from ingestion.providers.aerodatabox import AeroDataBoxProvider
from ingestion.providers.csv_feed import CsvProvider
from ingestion.service import IngestionService
from ingestion.updater import FlightUpdater
from server.reader import FlightReader


def timestamp(value):
    return utc_instant(datetime.fromisoformat(value.replace("Z", "+00:00")))


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


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


def reset_database(path):
    path = path.resolve()
    if not path.is_relative_to(LOCAL_DATA_DIR.resolve()):
        raise ValueError("reset only supports data files under the project's var directory")
    FlightUpdater(path).reset()
    return FlightReader(path).stats()


def parser():
    root = argparse.ArgumentParser(description="Flyji local development client")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("shell", help="interactive local client")
    commands.add_parser("stats", help="data file counts and location")
    reset = commands.add_parser("reset", help="clear the local SQLite database")
    reset.add_argument("--yes", action="store_true", help="confirm deletion")
    for name in ("flights", "routes", "itineraries"):
        query = commands.add_parser(name)
        query.add_argument("--start", required=True, type=timestamp)
        query.add_argument("--end", required=True, type=timestamp)
        query.add_argument("--origin", required=name == "itineraries")
        query.add_argument("--destination", required=name == "itineraries")
        query.add_argument("--limit", type=query_limit, default=20)
        if name == "flights":
            query.add_argument("--include-cancelled", action="store_true")
        if name == "itineraries":
            query.add_argument("--max-legs", type=int, choices=(1, 2, 3), default=2)
            query.add_argument("--min-connection", type=int, default=45)
            query.add_argument("--max-connection", type=int, default=360)
            query.add_argument(
                "--horizon",
                type=timestamp,
                help="inclusive arrival cutoff; default end + 7 days",
            )
    for name in ("import-json", "export-json"):
        command = commands.add_parser(name)
        command.add_argument("path", type=Path)
    metadata = commands.add_parser(
        "import-airports", help="upsert airport and directed transfer metadata JSON"
    )
    metadata.add_argument("path", type=Path)
    export_metadata = commands.add_parser(
        "export-airports", help="export the airport and directed transfer catalog JSON"
    )
    export_metadata.add_argument("path", type=Path)
    airport_query = commands.add_parser("airports", help="find airports by exact code or name")
    airport_query.add_argument("--query", default="")
    airport_query.add_argument("--city")
    airport_query.add_argument("--limit", type=query_limit, default=100)
    rebuild = commands.add_parser(
        "rebuild", help="calculate a plan from a request JSON; no plan is saved"
    )
    rebuild.add_argument("path", type=Path)
    rebuild.add_argument(
        "--snapshot-out",
        type=Path,
        help="save request, scoped facts and result for offline replay",
    )
    search = commands.add_parser(
        "search-itineraries", help="rank routes from request JSON with diagnostics"
    )
    search.add_argument("path", type=Path)
    search.add_argument("--snapshot-out", type=Path)
    replay_command = commands.add_parser(
        "replay-routing", help="recalculate a saved snapshot without opening a DB"
    )
    replay_command.add_argument("path", type=Path)
    delete = commands.add_parser("delete", help="delete one flight by provider and ID")
    delete.add_argument("provider")
    delete.add_argument("provider_id")
    ingest = commands.add_parser("ingest", help="fetch from AeroDataBox or import licensed CSV")
    ingest.add_argument("--airports", nargs="+", required=True)
    ingest.add_argument("--start", type=date.fromisoformat, required=True)
    ingest.add_argument("--end", type=date.fromisoformat, required=True)
    ingest.add_argument("--csv", type=Path)
    return root


async def ingest_records(args, updater):
    settings = get_settings()
    request = IngestRequest(airports=args.airports, start=args.start, end=args.end)
    provider = (
        CsvProvider(args.csv)
        if args.csv
        else AeroDataBoxProvider(
            settings.provider_api_key,
            settings.provider_base_url,
            settings.provider_host,
        )
    )
    try:
        return await IngestionService(updater, provider, settings.max_query_days).ingest(
            request.airports, request.start, request.end
        )
    finally:
        if isinstance(provider, AeroDataBoxProvider):
            await provider.aclose()


def run(arguments):
    args = parser().parse_args(arguments)
    if args.command == "shell":
        LocalShell().cmdloop()
        return
    if args.command == "replay-routing":
        output(replay(read_json(args.path)))
        return
    path = get_settings().database_path
    if args.command == "reset":
        if not args.yes:
            raise ValueError("reset deletes local flight data; supply --yes to confirm")
        output(reset_database(path))
        return
    # Reject malformed imports before creating or changing the database.
    records = airports = transfers = None
    if args.command == "import-json":
        data = read_json(args.path)
        if not isinstance(data, list):
            raise ValueError("flight import must be a JSON array")
        records = [FlightRecord.model_validate(row) for row in data]
    elif args.command == "import-airports":
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
    elif args.command in ("flights", "routes", "itineraries"):
        if args.end <= args.start:
            raise ValueError("end must follow start")
        if args.command == "itineraries" and args.limit > 100:
            raise ValueError("itinerary limit must be between 1 and 100")

    updater = FlightUpdater(path)
    updater.initialize()
    store = FlightReader(path)
    if args.command == "stats":
        output(store.stats())
    elif args.command == "import-json":
        output({"upserted": updater.upsert(records)})
    elif args.command == "export-json":
        write_json(args.path, [row.model_dump(mode="json") for row in store.all_records()])
        output({"exported_to": args.path})
    elif args.command == "delete":
        output({"deleted": updater.delete(args.provider, args.provider_id)})
    elif args.command == "import-airports":
        updater.update_airports(airports, transfers)
        output({"airports": len(airports), "transfers": len(transfers)})
    elif args.command == "export-airports":
        write_json(args.path, store.airport_catalog())
        output({"exported_to": args.path})
    elif args.command == "airports":
        output(store.airports(args.city, args.query, args.limit))
    elif args.command == "rebuild":
        request = RebuildRequest.model_validate_json(args.path.read_text())
        snapshot = store.rebuild_snapshot(request)
        result = rebuild_from_snapshot(request, snapshot)
        if args.snapshot_out:
            write_json(args.snapshot_out, replay_document("rebuild", request, snapshot, result))
        output(result)
    elif args.command == "search-itineraries":
        request = RouteSearchRequest.model_validate_json(args.path.read_text())
        snapshot = store.routing_snapshot(request)
        result = search_routes(request, snapshot)
        if args.snapshot_out:
            write_json(args.snapshot_out, replay_document("search", request, snapshot, result))
        output(result)
    elif args.command == "ingest":
        output(asyncio.run(ingest_records(args, updater)))
    else:
        if args.command == "flights":
            output(
                store.search(
                    args.start,
                    args.end,
                    args.origin,
                    args.destination,
                    args.include_cancelled,
                    args.limit,
                )
            )
        elif args.command == "routes":
            output(store.routes(args.start, args.end, args.origin, args.destination, args.limit))
        else:
            if args.min_connection < 0 or args.max_connection < args.min_connection:
                raise ValueError("invalid connection duration range")
            if args.origin.upper() == args.destination.upper():
                raise ValueError("origin and destination must differ")
            output(
                build_itineraries(
                    store,
                    args.origin.upper(),
                    args.destination.upper(),
                    args.start,
                    args.end,
                    args.max_legs,
                    args.min_connection,
                    args.max_connection,
                    args.limit,
                    horizon_end=args.horizon,
                )
            )


class LocalShell(cmd.Cmd):
    intro = "Flyji local client. Type help for commands; quit to exit."
    prompt = "flyji> "

    def default(self, line):
        try:
            run(shlex.split(line))
        except SystemExit:
            pass
        except Exception as error:
            print(f"Error: {error}")

    def emptyline(self):
        pass

    def do_help(self, arg):
        parser().print_help()
        print(
            "Use COMMAND --help for options. Edit exported JSON and import-json to update flights."
        )

    def do_quit(self, arg):
        return True

    def do_EOF(self, arg):
        print()
        return True


def main(arguments=None):
    try:
        run((sys.argv[1:] if arguments is None else arguments) or ["shell"])
    except (ValueError, OSError, sqlite3.Error, httpx.HTTPError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
