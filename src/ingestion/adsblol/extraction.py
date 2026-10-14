"""Resumable daily itinerary extraction with disposable raw inputs and a total budget."""

import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from core.file_lock import file_mutex
from core.itinerary_cache import cache_directory, database_bytes, file_bytes, policy
from ingestion.adsblol.archive import (
    MAX_BUDGET_BYTES,
    archive_traces,
    download_manifest,
    validate_asset,
    verified,
)
from ingestion.adsblol.movements import AirportLookup, MovementState, reduce_movement

SOURCE_ROWS = [
    (
        "adsblol",
        "ADSB.lol",
        "https://www.adsb.lol/docs/open-data/historical/",
        "ODbL-1.0",
        "https://opendatacommons.org/licenses/odbl/1-0/",
    ),
    (
        "ourairports",
        "OurAirports",
        "https://ourairports.com/data/",
        "Public Domain",
        "https://ourairports.com/data/",
    ),
]

SOURCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS extraction_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    license TEXT NOT NULL,
    license_url TEXT NOT NULL
);
"""

SCHEMA = (
    SOURCE_SCHEMA
    + """
CREATE TABLE IF NOT EXISTS itinerary_legs (
    key TEXT PRIMARY KEY,
    departure_date TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_days (
    day TEXT PRIMARY KEY,
    selection TEXT NOT NULL,
    receipt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_movements (
    icao_hex TEXT PRIMARY KEY,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_config (
    id INTEGER PRIMARY KEY CHECK(id=1),
    data TEXT NOT NULL
);
"""
)


def record_sources(connection):
    connection.execute(SOURCE_SCHEMA)
    connection.executemany(
        "INSERT OR REPLACE INTO extraction_sources VALUES (?,?,?,?,?)", SOURCE_ROWS
    )


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def directory_bytes(root):
    return sum(file_bytes(path) for path in root.rglob("*"))


class ExtractionBudget:
    def __init__(self, root, limit=MAX_BUDGET_BYTES, policy_db=None):
        if type(limit) is not int or not 0 < limit <= MAX_BUDGET_BYTES:
            raise ValueError("Extraction budget must be positive and at most 20 GiB")
        self.root, self.limit = root, limit
        self.policy_db = Path(policy_db).resolve() if policy_db else None

    def ceiling(self):
        state = policy(self.policy_db, include_catalog=False) if self.policy_db else None
        if self.policy_db and state is None:
            raise ValueError("Initialize the admin policy database before using --policy-db")
        quota = state["quota_bytes"] if state else None
        return min(self.limit, quota) if quota is not None else self.limit

    def used(self):
        extra = (
            database_bytes(self.policy_db)
            if self.policy_db is not None and not self.policy_db.is_relative_to(self.root)
            else 0
        )
        if self.policy_db is not None:
            cache = cache_directory(self.policy_db)
            if not cache.is_relative_to(self.root):
                extra += directory_bytes(cache)
        return directory_bytes(self.root) + extra

    def check(self, reserve=0):
        limit = self.ceiling()
        used = self.used()
        if used + reserve > limit:
            raise OSError(
                f"Extraction needs {used + reserve:,} bytes but quota is {limit:,}; "
                "extraction stopped. Raise the quota or free working space; durable legs are preserved."
            )
        return limit - used


def cleanup_raw(root, receipt):
    """Remove only recorded inputs that still match their committed hashes."""
    for item in receipt["inputs"]:
        path = root / "raw" / receipt["day"] / item["name"]
        if not path.exists():
            continue
        saved = {**item, "date": receipt["day"]}
        if not verified(path, saved):
            raise ValueError("Committed day's raw input changed; refusing to remove it")
        path.unlink()
        path.with_name(path.name + ".verified.json").unlink(missing_ok=True)


def _extract_day(
    root, day, assets, airports, rules, budget, *, download=False, client=None, departure_year=None
):
    database = root / "itineraries.db"
    lookup = AirportLookup(airports)
    # Set serialization is sorted explicitly for restart-stable configuration.
    config = canonical(
        {
            "algorithm": "observed-itinerary-v1",
            "airports": sorted(airports),
            "departure_year": departure_year,
            "filter": {
                "fixed_wing_types": sorted(rules.fixed_wing_types),
                "callsign_prefixes": sorted(rules.callsign_prefixes),
                # Preserve the existing v1 config shape; owner names never select legs.
                "owner_operators": [],
            },
        }
    )
    selection = canonical(assets)
    # A crash can occur after committing output but before deleting raw inputs.
    # Recover those disposable inputs even if a parallel admin lowered the limit.
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as reader:
            tables = {
                row[0]
                for row in reader.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            saved_config = (
                reader.execute("SELECT data FROM extraction_config WHERE id=1").fetchone()
                if "extraction_config" in tables
                else None
            )
            if saved_config is not None and saved_config[0] != config:
                raise ValueError("Extraction configuration changed; use a separate data root")
            row = (
                reader.execute(
                    "SELECT selection,receipt FROM extraction_days WHERE day=?", (str(day),)
                ).fetchone()
                if "extraction_days" in tables
                else None
            )
            sources = (
                reader.execute("SELECT * FROM extraction_sources ORDER BY id").fetchall()
                if "extraction_sources" in tables
                else []
            )
        if row is not None:
            if row[0] != selection:
                raise ValueError("Committed day's archive selection changed")
            receipt = json.loads(row[1])
            cleanup_raw(root, receipt)
            if sources != SOURCE_ROWS:
                budget.check(16384)
                with closing(sqlite3.connect(database)) as writer, writer:
                    record_sources(writer)
            budget.check()
            return {
                **receipt,
                "result": "already_committed",
                "database_bytes": database.stat().st_size,
            }
    budget.check(1024**2)
    with closing(sqlite3.connect(database, timeout=10)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        saved_config = connection.execute(
            "SELECT data FROM extraction_config WHERE id=1"
        ).fetchone()
        if saved_config is not None and saved_config[0] != config:
            raise ValueError("Extraction configuration changed; use a separate data root")
        last = connection.execute("SELECT MAX(day) FROM extraction_days").fetchone()[0]
        if last is not None and day != date.fromisoformat(last) + timedelta(days=1):
            raise ValueError("Process consecutive UTC days to preserve flights crossing midnight")
        output = root / "raw"
        if download:
            other = budget.used() - directory_bytes(output) if output.exists() else budget.used()
            download_manifest(
                {"assets": assets},
                output,
                client,
                budget_bytes=budget.ceiling() - other - 1024**2,
                before_write=lambda size: budget.check(size + 1024**2),
            )
        paths = [output / str(day) / item["name"] for item in assets]
        if not all(verified(path, item) for path, item in zip(paths, assets, strict=True)):
            raise ValueError("Verified daily archive parts missing; use --download")
        inputs = [
            {
                **item,
                "sha256": json.loads(path.with_name(path.name + ".verified.json").read_text())[
                    "sha256"
                ],
            }
            for path, item in zip(paths, assets, strict=True)
        ]
        start_us = int(datetime.combine(day, time.min, UTC).timestamp()) * 1_000_000
        end_us = start_us + 86_400_000_000
        counters, seen = Counter(), set()
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            record_sources(connection)
            connection.execute("INSERT OR IGNORE INTO extraction_config VALUES (1,?)", (config,))
            for data, compressed_bytes in archive_traces(paths):
                counters["aircraft_members"] += 1
                counters["raw_member_bytes"] += compressed_bytes
                hex_id = data.get("icao")
                if hex_id in seen:
                    raise ValueError("Duplicate aircraft member; day not committed")
                seen.add(hex_id)
                previous = connection.execute(
                    "SELECT state FROM pending_movements WHERE icao_hex=?", (hex_id,)
                ).fetchone()
                state = MovementState.model_validate_json(previous[0]) if previous else None
                legs, state, reason = reduce_movement(data, state, lookup, rules, start_us, end_us)
                counters[reason] += 1
                for leg in legs:
                    # Address is only an internal identity component, not served telemetry.
                    identity = (
                        f"observed-itinerary-v1:{leg.icao_hex}:{leg.departure_lower.isoformat()}"
                    )
                    key = hashlib.blake2b(identity.encode(), digest_size=16).hexdigest()
                    if departure_year is not None and leg.departure_lower.year != departure_year:
                        counters["legs_outside_departure_year"] += 1
                        continue
                    payload = leg.model_dump(mode="json", exclude={"icao_hex"})
                    encoded = canonical(payload)
                    old = connection.execute(
                        "SELECT data FROM itinerary_legs WHERE key=?", (key,)
                    ).fetchone()
                    if old is not None and old[0] != encoded:
                        raise ValueError("Conflicting itinerary identity; day not committed")
                    connection.execute(
                        "INSERT OR IGNORE INTO itinerary_legs VALUES (?,?,?)",
                        (key, str(leg.departure_lower.date()), encoded),
                    )
                    counters["legs_completed"] += 1
                    if counters["legs_completed"] % 100 == 0:
                        budget.check(1024**2)
                connection.execute("DELETE FROM pending_movements WHERE icao_hex=?", (hex_id,))
                if state is not None:
                    connection.execute(
                        "INSERT INTO pending_movements VALUES (?,?)",
                        (hex_id, state.model_dump_json()),
                    )
                if counters["aircraft_members"] % 100 == 0:
                    budget.check(1024**2)
            # Aircraft absent all day cannot bridge the required continuous coverage.
            for hex_id, raw in connection.execute(
                "SELECT icao_hex,state FROM pending_movements"
            ).fetchall():
                state = MovementState.model_validate_json(raw)
                if state.last_us is None or state.last_us < end_us - 120_000_000:
                    connection.execute("DELETE FROM pending_movements WHERE icao_hex=?", (hex_id,))
            pending = connection.execute("SELECT state FROM pending_movements").fetchall()
            receipt = {
                "day": str(day),
                "inputs": inputs,
                "counts": dict(counters),
                "pending_airborne": sum(
                    MovementState.model_validate_json(row[0]).first_airborne_us is not None
                    for row in pending
                ),
                "time_basis": "observed_ground_transition_bounds; not scheduled or gate times",
                "coverage": "conservative inferred legs; configured commercial/type subset",
            }
            connection.execute(
                "INSERT INTO extraction_days VALUES (?,?,?)",
                (str(day), selection, canonical(receipt)),
            )
            budget.check(1024**2)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        budget.check()
    cleanup_raw(root, receipt)
    return {**receipt, "result": "committed", "database_bytes": database.stat().st_size}


def extract_day(
    root,
    day,
    assets,
    airports,
    rules,
    *,
    limit=MAX_BUDGET_BYTES,
    policy_db=None,
    download=False,
    client=None,
    departure_year=None,
):
    day = date.fromisoformat(day) if isinstance(day, str) else day
    assets = sorted(assets, key=lambda item: item["name"])
    if not assets or len({item["name"] for item in assets}) != len(assets):
        raise ValueError("Missing or duplicate daily archive parts")
    for item in assets:
        validate_asset(item)
        if item["date"] != str(day):
            raise ValueError("Archive selection crosses UTC days")
    suffixes = [item["name"].rsplit(".", 1)[1] for item in assets]
    if suffixes != ["tar"] and suffixes != ["a" + chr(ord("a") + n) for n in range(len(assets))]:
        raise ValueError("Daily archive parts are incomplete or out of order")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    budget = ExtractionBudget(root, limit, policy_db)
    with file_mutex(root / ".extract.lock"):
        return _extract_day(
            root,
            day,
            assets,
            airports,
            rules,
            budget,
            download=download,
            client=client,
            departure_year=departure_year,
        )
