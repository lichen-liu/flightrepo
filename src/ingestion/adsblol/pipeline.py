"""Daily date-range jobs invoked by the admin console."""

import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from ingestion.adsblol.archive import MAX_BUDGET_BYTES, validate_asset
from ingestion.adsblol.diagnostics import Diagnostics
from ingestion.adsblol.extraction import extract_day
from ingestion.adsblol.filters import CommercialFilter
from ingestion.adsblol.legs import airport_points


def inventory(path, boundary_manifest=None):
    primary = json.loads(Path(path).read_text())
    grouped = {}
    for source in [primary] + (
        [json.loads(Path(boundary_manifest).read_text())] if boundary_manifest else []
    ):
        daily = {}
        for item in source["assets"]:
            validate_asset(item)
            daily.setdefault(date.fromisoformat(item["date"]), []).append(item)
        for day, assets in daily.items():
            grouped.setdefault(day, assets)
    return primary["year"], grouped


def extract_range(
    manifest,
    airports_path,
    filter_path,
    root,
    start,
    end,
    *,
    boundary_manifest=None,
    limit=MAX_BUDGET_BYTES,
    policy_db=None,
    download=True,
    client=None,
    max_days=None,
    departure_year=None,
    output_db=None,
    refresh_diagnostics=False,
):
    start = date.fromisoformat(start) if isinstance(start, str) else start
    end = date.fromisoformat(end) if isinstance(end, str) else end
    count = (end - start).days + 1
    if count < 1 or max_days is not None and count > max_days:
        raise ValueError(
            f"Select a positive date range of at most {max_days} days"
            if max_days
            else "End must follow start"
        )
    _, grouped = inventory(manifest, boundary_manifest)
    days = [start + timedelta(days=offset) for offset in range(count)]
    missing = [str(day) for day in days if day not in grouped]
    if missing:
        raise ValueError("Archive inventory missing for " + ", ".join(missing))
    airports = airport_points(Path(airports_path))
    rules = CommercialFilter.model_validate_json(Path(filter_path).read_text())
    for day in days:
        yield extract_day(
            root,
            day,
            grouped[day],
            airports,
            rules,
            limit=limit,
            policy_db=policy_db,
            download=download,
            client=client,
            departure_year=departure_year,
            output_db=output_db,
            refresh_diagnostics=refresh_diagnostics,
        )


def extraction_status(root, output_db=None):
    root = Path(root).resolve()
    database = Path(output_db).resolve() if output_db else root / "itineraries.db"
    empty = {
        "data_root": str(root),
        "database": str(database),
        "records": 0,
        "days": [],
        "ranges": [],
        "active_batches": [],
    }
    if not database.is_file():
        return empty
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='extraction_days'"
        ).fetchone():
            return empty
        active = [
            dict(row)
            for row in connection.execute(
                "SELECT b.id,date(b.input_start_us/1000000,'unixepoch') AS day,b.started_us FROM ingestion_batches b JOIN data_sources s ON s.id=b.source_id WHERE s.code='adsblol' AND b.state='running'"
            )
        ]
        days = [
            row[0] for row in connection.execute("SELECT day FROM extraction_days ORDER BY day")
        ]
        records = connection.execute("SELECT COUNT(*) FROM itinerary_legs").fetchone()[0]
        legacy = [
            row[0]
            for row in connection.execute("SELECT day,receipt FROM extraction_days")
            if json.loads(row[1]).get("boundary_version") != 1
        ]
        reports = [
            (label, json.loads(raw))
            for label, raw in connection.execute(
                "SELECT day,receipt FROM extraction_days ORDER BY day"
            )
        ]
    totals, missing, latest = Diagnostics(), [], None
    for label, report in reports:
        diagnostic = report.get("diagnostics")
        if not diagnostic or diagnostic.get("version") != 1:
            missing.append(label)
            continue
        for group, values in diagnostic["counts"].items():
            totals.groups[group].update(values)
        latest = {"day": label, "counts": report["counts"], "diagnostics": diagnostic}
    ranges = []
    for label in days:
        if ranges and date.fromisoformat(label) == date.fromisoformat(
            ranges[-1]["end"]
        ) + timedelta(days=1):
            ranges[-1]["end"] = label
            ranges[-1]["days"] += 1
        else:
            ranges.append({"start": label, "end": label, "days": 1})
    return {
        "data_root": str(root),
        "database": str(database),
        "records": records,
        "days": days,
        "ranges": ranges,
        "active_batches": active,
        "legacy_days_needing_boundary_refresh": sorted(legacy),
        "diagnostics": {
            "days_reported": len(reports) - len(missing),
            "days_missing_diagnostics": missing,
            "totals": totals.summary(),
            "latest_day_report": latest,
        },
    }
