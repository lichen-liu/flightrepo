"""Local development client. Commands call the same core used by the API."""
import argparse
import asyncio
import cmd
import json
import shlex
from datetime import date, datetime, timezone
from pathlib import Path

from flight_engine.core.config import LOCAL_DATA_DIR, get_settings
from flight_engine.core.models import FlightRecord, IngestRequest
from flight_engine.ingestion.providers.aerodatabox import AeroDataBoxProvider
from flight_engine.ingestion.providers.csv_feed import CsvProvider
from flight_engine.core.itineraries import build_itineraries
from flight_engine.ingestion.updater import FlightUpdater
from flight_engine.ingestion.service import IngestionService
from flight_engine.server.reader import FlightReader


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset, e.g. 2026-10-10T00:00:00Z")
    return result.astimezone(timezone.utc)


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
        query.add_argument("--limit", type=int, default=20)
        if name == "flights":
            query.add_argument("--include-cancelled", action="store_true")
        if name == "itineraries":
            query.add_argument("--max-legs", type=int, choices=(1, 2, 3), default=2)
            query.add_argument("--min-connection", type=int, default=45)
            query.add_argument("--max-connection", type=int, default=360)
    for name in ("import-json", "export-json"):
        command = commands.add_parser(name)
        command.add_argument("path", type=Path)
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
    provider = CsvProvider(args.csv) if args.csv else AeroDataBoxProvider(
        settings.provider_api_key, settings.provider_base_url, settings.provider_host)
    try:
        return await IngestionService(updater, provider, settings.max_query_days).ingest(
            request.airports, request.start, request.end)
    finally:
        if isinstance(provider, AeroDataBoxProvider):
            await provider.aclose()


def run(arguments):
    args = parser().parse_args(arguments)
    if args.command == "shell":
        LocalShell().cmdloop()
        return
    path = get_settings().database_path
    if args.command == "reset":
        if not args.yes:
            raise ValueError("reset deletes local flight data; supply --yes to confirm")
        output(reset_database(path))
        return
    updater = FlightUpdater(path)
    updater.initialize()
    store = FlightReader(path)
    if args.command == "stats":
        output(store.stats())
    elif args.command == "import-json":
        # Validate the complete file before changing the data file. Existing IDs are updated.
        records = [FlightRecord.model_validate(row) for row in json.loads(args.path.read_text())]
        output({"upserted": updater.upsert(records)})
    elif args.command == "export-json":
        args.path.write_text(json.dumps([row.model_dump(mode="json") for row in store.all_records()], indent=2) + "\n")
        output({"exported_to": args.path})
    elif args.command == "delete":
        output({"deleted": updater.delete(args.provider, args.provider_id)})
    elif args.command == "ingest":
        output(asyncio.run(ingest_records(args, updater)))
    else:
        if args.end <= args.start or args.limit < 1:
            raise ValueError("end must follow start and limit must be positive")
        if args.command == "flights":
            output(store.search(args.start, args.end, args.origin, args.destination,
                                args.include_cancelled, args.limit))
        elif args.command == "routes":
            output(store.routes(args.start, args.end, args.origin, args.destination, args.limit))
        else:
            if args.min_connection < 0 or args.max_connection < args.min_connection:
                raise ValueError("invalid connection duration range")
            if args.origin.upper() == args.destination.upper():
                raise ValueError("origin and destination must differ")
            output(build_itineraries(store, args.origin.upper(), args.destination.upper(),
                                     args.start, args.end, args.max_legs, args.min_connection,
                                     args.max_connection, args.limit))


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
        print("Use COMMAND --help for options. Edit exported JSON and import-json to update flights.")

    def do_quit(self, arg):
        return True

    def do_EOF(self, arg):
        print()
        return True


def main():
    import sys
    try:
        run(sys.argv[1:] or ["shell"])
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
