"""Interactive, repeatable ingestion jobs over registered source pipelines."""

import asyncio
import json
import math
from datetime import date, timedelta
from pathlib import Path

from admin.management import ask
from core.config import PROJECT_ROOT
from core.itinerary_cache import control
from core.models import IngestRequest
from ingestion.adsblol.filters import CommercialFilter
from ingestion.adsblol.legs import airport_points
from ingestion.adsblol.pipeline import extract_range, extraction_status, inventory
from ingestion.providers.aerodatabox import AeroDataBoxProvider
from ingestion.providers.csv_feed import CsvProvider
from ingestion.service import IngestionService
from ingestion.updater import FlightUpdater

SOURCES = ("adsblol", "csv", "aerodatabox")


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
    FlightUpdater(path).initialize()
    with control(path, writable=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        sequence = connection.execute(
            "SELECT COALESCE(MAX(last_used),0)+1 FROM ingestion_profiles"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO ingestion_profiles VALUES (?,?,?) ON CONFLICT(source) DO UPDATE SET data=excluded.data,last_used=excluded.last_used",
            (source, json.dumps(data), sequence),
        )


def run_ingestion(args, path, settings, emit, *, interactive=False):
    saved = profiles(path)
    source = args.source or (
        "csv" if args.csv else "aerodatabox" if args.airports else next(iter(saved), None)
    )
    if source is None:
        if args.ingest_action == "status":
            source = "adsblol"
        elif interactive or args.ingest_action == "configure":
            source = ask("Pipeline (adsblol/csv/aerodatabox)", "adsblol")
        else:
            source = "aerodatabox"
    if source not in SOURCES:
        raise ValueError("Choose adsblol, csv or aerodatabox")
    data = dict(saved.get(source, {}))
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
            PROJECT_ROOT / "catalogs/adsblol-2025.json",
        )
        field("airport_catalog", args.airport_catalog, "Airport CSV snapshot")
        field(
            "filter_path",
            args.filter_path,
            "Commercial filter JSON",
            PROJECT_ROOT / "fixtures/adsblol-commercial-filter.json",
        )
        field(
            "data_root",
            args.data_root,
            "Extracted data directory",
            path.with_name(path.name + ".adsblol"),
        )
        field(
            "output_db",
            args.output_db,
            "Itinerary output SQLite file",
            path,
        )
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
        if not Path(data["airport_catalog"]).is_file() or not Path(data["filter_path"]).is_file():
            raise ValueError("Airport snapshot and filter files must exist")
        airport_points(Path(data["airport_catalog"]))
        CommercialFilter.model_validate_json(Path(data["filter_path"]).read_text())
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
    if start is None or end is None or not 1 <= (end - start).days + 1 <= settings.max_query_days:
        raise ValueError(f"Choose --start/--end for 1–{settings.max_query_days} days per job")
    if source == "adsblol":
        missing = [
            str(start + timedelta(days=offset))
            for offset in range((end - start).days + 1)
            if start + timedelta(days=offset) not in available
        ]
        if missing:
            raise ValueError("Archive inventory missing for " + ", ".join(missing))
        save_profile(path, source, data)
        inserted = skipped = 0
        for receipt in extract_range(
            data["manifest"],
            data["airport_catalog"],
            data["filter_path"],
            data["data_root"],
            start,
            end,
            boundary_manifest=data.get("boundary_manifest"),
            limit=int(data["budget_gib"] * 2**30),
            policy_db=path,
            max_days=settings.max_query_days,
            output_db=data["output_db"],
            refresh_diagnostics=args.refresh_diagnostics,
        ):
            if receipt["result"] == "already_committed":
                skipped += 1
            else:
                inserted += receipt["counts"].get("records_inserted", 0)
            emit({key: value for key, value in receipt.items() if key != "inputs"})
        emit(
            {
                "source": source,
                "start": str(start),
                "end": str(end),
                "records_inserted": inserted,
                "days_skipped": skipped,
                **extraction_status(data["data_root"], data["output_db"]),
                "serving": "observed candidates in the shared serving database",
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
                    FlightUpdater(path), provider, settings.max_query_days
                ).ingest(request.airports, request.start, request.end)
            finally:
                if isinstance(provider, AeroDataBoxProvider):
                    await provider.aclose()

        emit(asyncio.run(fetch()))
