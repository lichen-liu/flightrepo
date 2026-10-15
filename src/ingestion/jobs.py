"""Interactive, repeatable ingestion jobs over registered source pipelines."""

import asyncio
import json
import math
from datetime import date, timedelta
from pathlib import Path

from core.disk_quota import control
from core.models import IngestRequest
from core.prompts import ask
from ingestion.adsblol.legs import airport_points
from ingestion.adsblol.pipeline import extract_range, extraction_status, inventory
from ingestion.adsblol.policy import ExtractionPolicy
from ingestion.adsblol.prepare import prepare_inputs
from ingestion.providers.aerodatabox import AeroDataBoxProvider
from ingestion.providers.csv_feed import CsvProvider
from ingestion.service import IngestionService
from ingestion.store import IngestionStore

SOURCES = ("adsblol", "csv", "aerodatabox")
PURPOSES = {
    "adsblol": "Historical observations: extract inferred itineraries and aircraft type, one day at a time",
    "csv": "Licensed CSV: import schedules and optional actual times/aircraft type",
    "aerodatabox": "Airport API: fetch schedules, actual times and status using your API key",
}


def add_ingestion_commands(commands, *, writable=True):
    ingest = commands.add_parser(
        "ingest", help="inspect source pipeline state" if not writable else "run source pipelines"
    )
    return configure_parser(ingest, writable=writable)


def configure_parser(ingest, *, writable=True):
    ingest.set_defaults(
        ingest_action="run" if writable else "status",
        airports=None,
        csv=None,
        start=None,
        end=None,
        manifest=None,
        airport_catalog=None,
        policy_path=None,
        data_root=None,
        output_db=None,
        boundary_manifest=None,
        budget_gib=None,
        refresh_diagnostics=False,
        offline=False,
        departure_year=None,
        finish_year=False,
    )
    sources = ingest.add_subparsers(dest="source")
    sources.add_parser("sources", help="show each pipeline's purpose and configured state")
    for name in SOURCES:
        source = sources.add_parser(name, help=PURPOSES[name], description=PURPOSES[name])
        actions = source.add_subparsers(dest="ingest_action")
        source.set_defaults(ingest_action="run" if writable else "status")
        if name == "adsblol" and writable:
            prepare = actions.add_parser(
                "prepare", help="fetch archive manifest and airport CSV; no raw flights downloaded"
            )
            prepare.add_argument("--start", type=date.fromisoformat)
            prepare.add_argument("--end", type=date.fromisoformat)
            prepare.add_argument(
                "--data-root",
                type=Path,
                help="directory for generated manifest and airport snapshot",
            )
            prepare.add_argument(
                "--finish-year",
                action="store_true",
                help="include January 1 metadata for December 31 arrivals",
            )
        for action in ("configure", "run", "status") if writable else ("status",):
            command = actions.add_parser(
                action,
                help={
                    "configure": "save source inputs for repeated jobs",
                    "run": "ingest a date range into the selected database",
                    "status": "show committed data and current processing day",
                }[action],
            )
            if name == "adsblol":
                command.add_argument(
                    "--data-root", type=Path, help="daily raw/staging working directory"
                )
                command.add_argument(
                    "--output-db",
                    type=Path,
                    help="explicit destination override; default is selected database",
                )
                if action != "status":
                    command.add_argument(
                        "--manifest", type=Path, help="prepared daily archive manifest JSON"
                    )
                    command.add_argument(
                        "--airport-catalog", type=Path, help="immutable OurAirports CSV snapshot"
                    )
                    command.add_argument(
                        "--policy",
                        dest="policy_path",
                        type=Path,
                        help="optional inference quality limits JSON; no type/callsign whitelist",
                    )
                    command.add_argument(
                        "--boundary-manifest",
                        type=Path,
                        help="additional manifest for adjacent-year observations",
                    )
                    command.add_argument(
                        "--budget-gib",
                        type=float,
                        help="daily raw/staging working budget; separate from database quota",
                    )
                if action == "run":
                    command.add_argument(
                        "--offline",
                        action="store_true",
                        help="process verified raw inputs already present; never download",
                    )
                    command.add_argument(
                        "--refresh-diagnostics",
                        action="store_true",
                        help="reprocess committed days, preserving source identities",
                    )
                    command.add_argument(
                        "--departure-year",
                        type=int,
                        help="retain only legs departing in this UTC year",
                    )
                    command.add_argument(
                        "--finish-year",
                        action="store_true",
                        help="process January 1 for December 31 arrivals; exclude next-year departures",
                    )
            elif action != "status":
                command.add_argument("--airports", nargs="+", help="airport codes to import/fetch")
                if name == "csv":
                    command.add_argument(
                        "--csv", type=Path, help="licensed CSV with FlightRecord columns"
                    )
            if action == "run":
                command.add_argument("--start", type=date.fromisoformat)
                command.add_argument("--end", type=date.fromisoformat)

    return sources


def profiles(path):
    if not path.is_file():
        return {}
    with control(path) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='ingestion_profiles'"
        ).fetchone():
            return {}
        return {
            row[0]: json.loads(row[1])
            for row in connection.execute(
                "SELECT source,data FROM ingestion_profiles ORDER BY last_used DESC"
            )
        }


def save_profile(path, source, data):
    store = IngestionStore(path)
    store.initialize()
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        sequence = connection.execute(
            "SELECT COALESCE(MAX(last_used),0)+1 FROM ingestion_profiles"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO ingestion_profiles VALUES (?,?,?) ON CONFLICT(source) DO UPDATE SET data=excluded.data,last_used=excluded.last_used",
            (source, json.dumps(data), sequence),
        )


def run_ingestion(args, path, settings, emit, *, interactive=False):
    if args.ingest_action not in ("status",) and args.source != "sources":
        from core.database_roles import ingestion_target

        ingestion_target(path)
        if args.output_db is not None and Path(args.output_db).resolve() != Path(path).resolve():
            raise ValueError(
                "Use --db to select the single ingestion database; --output-db cannot redirect writes"
            )
    saved = profiles(path)
    if args.source == "sources":
        emit(
            {
                "pipelines": [
                    {"source": source, "purpose": PURPOSES[source], "configured": source in saved}
                    for source in SOURCES
                ],
                "reference_evidence": "document FILE stores normalized API/tool evidence; publication is explicit",
                "producer_api": "IngestionStore + IngestionService",
            }
        )
        return
    source = args.source or (next(iter(saved), None) if interactive else None)
    if source is None:
        if args.ingest_action == "status":
            source = "adsblol"
        elif interactive:
            source = ask("Pipeline (adsblol/csv/aerodatabox)", "adsblol")
        else:
            raise ValueError(
                "Choose ingest adsblol, ingest csv or ingest aerodatabox; ingest sources explains each"
            )
    if source not in SOURCES:
        raise ValueError("Choose adsblol, csv or aerodatabox")
    data = dict(saved.get(source, {}))
    if args.ingest_action != "status":
        # The selected ingestion store is the only pipeline destination.
        data["output_db"] = str(path)
    if args.ingest_action == "prepare":
        first, last = args.start, args.end
        if interactive:
            first = first or date.fromisoformat(ask("First UTC archive day (YYYY-MM-DD)"))
            last = last or date.fromisoformat(
                ask("Last UTC archive day", str(first + timedelta(days=2)))
            )
        if first is None or last is None or last < first:
            raise ValueError("Prepare requires a valid --start/--end range")
        if args.finish_year:
            if (last.month, last.day) != (12, 31):
                raise ValueError("--finish-year requires --end on December 31")
            last += timedelta(days=1)
        root = args.data_root or data.get("data_root") or path.with_name(path.name + ".adsblol")
        data.update(
            prepare_inputs(
                root,
                first,
                last,
                airport_path=data.get("airport_catalog"),
                policy_path=data.get("policy_path"),
            )
        )
        data.setdefault("budget_gib", 20)
        data.setdefault("output_db", str(path))
        save_profile(path, source, data)
        hint = f"adsblol run --start {first} --end {args.end or (last - timedelta(days=1) if args.finish_year else last)}"
        if args.finish_year:
            hint += " --finish-year"
        emit(
            {
                "prepared": source,
                "start": str(first),
                "end": str(last),
                "profile": data,
                "next": hint,
            }
        )
        return
    if args.ingest_action == "status":
        if source == "adsblol":
            root = args.data_root or data.get("data_root") or path.with_name(path.name + ".adsblol")
            emit(
                {
                    "source": source,
                    **extraction_status(root, args.output_db or data.get("output_db") or path),
                }
            )
        else:
            count = 0
            if path.is_file():
                with control(path) as connection:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM flights WHERE provider=?", (source,)
                    ).fetchone()[0]
            emit({"source": source, "database": str(path), "records": count, "profile": data})
        return
    guided = interactive or args.ingest_action == "configure"

    def field(name, value, label, default=None, *, required=True):
        candidate = value if value is not None else data.get(name, default)
        if guided and (
            candidate is None
            and required
            or args.ingest_action == "configure"
            and value is None
            and (required or interactive)
        ):
            candidate = ask(
                label, candidate if candidate is not None else None if required else "-"
            )
            if not required and candidate == "-":
                data.pop(name, None)
                return None
        if candidate is None and required:
            raise ValueError(f"Configure {source}: {label} is required")
        if candidate is not None:
            data[name] = str(Path(candidate).expanduser().resolve())
        return data.get(name)

    if source == "adsblol":
        field(
            "manifest",
            args.manifest,
            "Archive manifest JSON",
        )
        field("airport_catalog", args.airport_catalog, "Airport CSV snapshot")
        field(
            "policy_path",
            args.policy_path,
            "Inference quality policy JSON",
            required=False,
        )
        field(
            "data_root",
            args.data_root,
            "Extracted data directory",
            path.with_name(path.name + ".adsblol"),
        )
        data["output_db"] = str(path)
        field(
            "boundary_manifest",
            args.boundary_manifest,
            "Additional boundary manifest",
            required=False,
        )
        if args.budget_gib is not None:
            data["budget_gib"] = args.budget_gib
        data.setdefault("budget_gib", 20)
        if not math.isfinite(data["budget_gib"]) or not 0 < data["budget_gib"] <= 20:
            raise ValueError("Budget must be greater than zero and at most 20 GiB")
    elif source == "csv":
        field("csv", args.csv, "Licensed CSV file")
    if source != "adsblol":
        airports = args.airports or data.get("airports")
        if guided and (not airports or args.ingest_action == "configure" and args.airports is None):
            airports = (
                ask("Airport codes", " ".join(airports) if airports else None)
                .replace(",", " ")
                .split()
            )
        if not airports:
            raise ValueError("Airport codes are required for CSV/AeroDataBox ingestion")
        data["airports"] = airports
    if source == "adsblol":
        _, available = inventory(data["manifest"], data.get("boundary_manifest"))
        if not available:
            raise ValueError("Archive inventory is empty")
        if not Path(data["airport_catalog"]).is_file():
            raise ValueError("Airport snapshot must exist")
        airport_points(Path(data["airport_catalog"]))
        if data.get("policy_path"):
            ExtractionPolicy.model_validate_json(Path(data["policy_path"]).read_text())
    elif source == "csv" and not Path(data["csv"]).is_file():
        raise ValueError("Licensed CSV file must exist")
    if args.ingest_action == "configure":
        save_profile(path, source, data)
        emit({"configured": source, "profile": data})
        return
    start, end = args.start, args.end
    if guided:
        if start is None:
            start = date.fromisoformat(ask("Start date (YYYY-MM-DD)"))
        if end is None:
            suggested = start + timedelta(days=2)
            if source == "adsblol":
                suggested = min(suggested, max(available))
            end = date.fromisoformat(ask("End date (YYYY-MM-DD)", str(suggested)))
    if start is None or end is None or end < start:
        raise ValueError("Choose a valid --start/--end range")
    if source != "adsblol" and (end - start).days + 1 > settings.max_query_days:
        raise ValueError(f"CSV/API jobs support at most {settings.max_query_days} days")
    if source == "adsblol":
        requested_end = end
        departure_year = args.departure_year
        if departure_year is not None:
            date(departure_year, 1, 1)
        if args.finish_year:
            if (
                (end.month, end.day) != (12, 31)
                or start.year != end.year
                or departure_year not in (None, end.year)
            ):
                raise ValueError("--finish-year requires one year's range ending December 31")
            departure_year = end.year
            end += timedelta(days=1)
        missing = [
            str(start + timedelta(days=offset))
            for offset in range((end - start).days + 1)
            if start + timedelta(days=offset) not in available
        ]
        if missing:
            raise ValueError("Archive inventory missing for " + ", ".join(missing))
        save_profile(path, source, data)
        inserted = updated = skipped = 0
        for receipt in extract_range(
            data["manifest"],
            data["airport_catalog"],
            data.get("policy_path"),
            data["data_root"],
            start,
            end,
            boundary_manifest=data.get("boundary_manifest"),
            limit=int(data["budget_gib"] * 2**30),
            policy_db=path,
            download=not args.offline,
            departure_year=departure_year,
            output_db=data["output_db"],
            refresh_diagnostics=args.refresh_diagnostics,
        ):
            if receipt["result"] == "already_committed":
                skipped += 1
            else:
                inserted += receipt["counts"].get("records_inserted", 0)
                updated += receipt["counts"].get("records_updated", 0)
            emit({key: value for key, value in receipt.items() if key != "inputs"})
        emit(
            {
                "source": source,
                "start": str(start),
                "end": str(requested_end),
                "arrival_boundary_day": str(end) if args.finish_year else None,
                "records_inserted": inserted,
                "records_updated": updated,
                "days_skipped": skipped,
                **extraction_status(data["data_root"], data["output_db"]),
                "serving": "unchanged; publish eligible records explicitly",
                "selected_database": str(path.resolve()),
            }
        )
    else:
        request = IngestRequest(airports=data["airports"], start=start, end=end)
        provider = (
            CsvProvider(Path(data["csv"]))
            if source == "csv"
            else AeroDataBoxProvider(
                settings.provider_api_key, settings.provider_base_url, settings.provider_host
            )
        )
        save_profile(path, source, data)

        async def fetch():
            try:
                return await IngestionService(
                    IngestionStore(path), provider, settings.max_query_days
                ).ingest(request.airports, request.start, request.end)
            finally:
                if isinstance(provider, AeroDataBoxProvider):
                    await provider.aclose()

        emit(asyncio.run(fetch()))
