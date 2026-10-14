"""Measure synthetic flight facts with production indexes; retain no benchmark databases."""

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from core.models import FlightRecord
from ingestion.updater import FlightUpdater


def records(count, *, completed):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    routes = [("YYZ", "LHR"), ("LHR", "AMS"), ("AMS", "CDG"), ("PHX", "SAN")]
    types = ["B789", "A320", "B738", "E75L"]
    for index in range(count):
        departure = start + timedelta(seconds=(index * 31536000 // count))
        origin, destination = routes[index % len(routes)]
        yield FlightRecord(
            provider="synthetic",
            provider_id=f"2025-flight-{index:08d}",
            flight_number=f"AC {index % 9999 + 1}",
            airline_iata="AC",
            origin_iata=origin,
            destination_iata=destination,
            departure_scheduled=departure,
            arrival_scheduled=departure + timedelta(hours=2),
            departure_actual=departure + timedelta(minutes=5) if completed else None,
            arrival_actual=departure + timedelta(hours=2, minutes=10) if completed else None,
            status="landed" if completed else "scheduled",
            codeshares=[f"UA {index % 9999 + 1}"] if index % 3 == 0 else [],
            fetched_at=departure + timedelta(days=1),
            aircraft_type_code=types[index % len(types)],
        )


def measure(count, profile, directory):
    path = directory / (profile + ".db")
    updater = FlightUpdater(path)
    updater.initialize()
    batch = []
    for record in records(count, completed=profile != "scheduled"):
        batch.append(record)
        if len(batch) == 1000:
            updater.upsert(batch)
            batch.clear()
    if batch:
        updater.upsert(batch)
    with closing(sqlite3.connect(path)) as connection, connection:
        if profile == "completed_with_observed_evidence":
            # Hypothetical evidence extension, not a serving schema or ADSB import.
            # Integer microseconds retain all four timing bounds without long strings.
            connection.execute("CREATE TABLE evidence_methods(id INTEGER PRIMARY KEY, name TEXT)")
            connection.execute(
                "INSERT INTO evidence_methods VALUES (1,'ground_brackets_and_nearby_airports')"
            )
            connection.execute(
                "CREATE TABLE observed_evidence(flight_rowid INTEGER PRIMARY KEY, icao_address INTEGER, callsign TEXT, departure_lower_us INTEGER, departure_upper_us INTEGER, arrival_lower_us INTEGER, arrival_upper_us INTEGER, method_id INTEGER)"
            )
            start = datetime(2025, 1, 1, tzinfo=UTC)
            base_us = int(start.timestamp()) * 1_000_000
            connection.executemany(
                "INSERT INTO observed_evidence VALUES (?,?,?,?,?,?,?,?)",
                (
                    (
                        rowid,
                        index % 0x1000000,
                        f"ACA{index % 9999 + 1}",
                        base_us + index * 31536000 // count * 1_000_000,
                        base_us + index * 31536000 // count * 1_000_000 + 2_000_000,
                        base_us + index * 31536000 // count * 1_000_000 + 7_200_000_000,
                        base_us + index * 31536000 // count * 1_000_000 + 7_203_000_000,
                        1,
                    )
                    for index, (rowid,) in enumerate(
                        connection.execute("SELECT rowid FROM flights")
                    )
                ),
            )
            connection.commit()
        assert connection.execute("SELECT COUNT(*) FROM flights").fetchone()[0] == count
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        indexes = connection.execute("PRAGMA index_list(flights)").fetchall()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size = path.stat().st_size
    return {
        "profile": profile,
        "records": count,
        "database_bytes": size,
        "bytes_per_leg_including_indexes": size / count,
        "flight_indexes": sorted(row[1] for row in indexes),
        "one_million_legs_GiB_extrapolated": size / count * 1_000_000 / 2**30,
        "twenty_million_legs_GiB_extrapolated": size / count * 20_000_000 / 2**30,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=100_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.records < 1:
        parser.error("--records must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="flight-storage-", dir=args.output.parent) as directory:
        results = [
            measure(args.records, profile, Path(directory))
            for profile in ("scheduled", "completed", "completed_with_observed_evidence")
        ]
    report = {
        "method": "Synthetic flight legs with current schema and every production flight index. Observed-evidence profile adds a hypothetical compact companion table; no real flight coverage or routing is proved.",
        "results": results,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
