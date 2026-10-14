"""Resumable daily itinerary extraction with disposable raw inputs and a total budget."""

import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing, nullcontext
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
from ingestion.adsblol.movements import (
    AirportLookup,
    LeadingMovement,
    MovementState,
    advance_boundary,
    reduce_movement,
)

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
CREATE TABLE IF NOT EXISTS extraction_boundaries (
    icao_hex TEXT NOT NULL,
    day TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (icao_hex, day)
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


def compatible_config(saved, current):
    old, new = json.loads(saved), json.loads(current)
    # Year selects records for one invocation; it does not change extraction rules.
    old.pop("departure_year", None)
    new.pop("departure_year", None)
    return old == new


def receipt_scope(receipt, saved_config):
    if "departure_years" in receipt:
        return receipt["departure_years"]
    year = json.loads(saved_config).get("departure_year") if saved_config else None
    return [year] if year is not None else None


def save_leg(connection, leg, counters):
    identity = f"observed-itinerary-v1:{leg.icao_hex}:{leg.departure_lower.isoformat()}"
    key = hashlib.blake2b(identity.encode(), digest_size=16).hexdigest()
    encoded = canonical(leg.model_dump(mode="json", exclude={"icao_hex"}))
    old = connection.execute("SELECT data FROM itinerary_legs WHERE key=?", (key,)).fetchone()
    if old is not None and old[0] != encoded:
        raise ValueError("Conflicting itinerary identity; day not committed")
    inserted = connection.execute(
        "INSERT OR IGNORE INTO itinerary_legs VALUES (?,?,?)",
        (key, str(leg.departure_lower.date()), encoded),
    ).rowcount
    counters["records_inserted" if inserted else "records_already_present"] += 1
    return bool(inserted)


def boundary_parts(raw):
    data = json.loads(raw)
    leading = LeadingMovement.model_validate(data["leading"]) if data.get("leading") else None
    tail = MovementState.model_validate(data["tail"]) if data.get("tail") else None
    return leading, tail


def reconcile_boundaries(connection, hex_id, day, rules, counters):
    prior, expected = [], day - timedelta(days=1)
    for label, raw in connection.execute(
        "SELECT day,data FROM extraction_boundaries WHERE icao_hex=? AND day<? ORDER BY day DESC",
        (hex_id, str(day)),
    ):
        if date.fromisoformat(label) != expected:
            break
        leading, tail = boundary_parts(raw)
        prior.append((leading, tail))
        if tail is None or tail.ground is not None:
            break
        expected -= timedelta(days=1)
    carry = None
    for leading, tail in reversed(prior):
        _, carry = advance_boundary(hex_id, carry, leading, tail, rules)
    expected = day
    for label, raw in connection.execute(
        "SELECT day,data FROM extraction_boundaries WHERE icao_hex=? AND day>=? ORDER BY day",
        (hex_id, str(day)),
    ):
        current = date.fromisoformat(label)
        if current != expected:
            break
        leading, tail = boundary_parts(raw)
        candidate, carry = advance_boundary(hex_id, carry, leading, tail, rules)
        if candidate is not None:
            origin_day = str(candidate.departure_lower.date())
            receipt = connection.execute(
                "SELECT receipt FROM extraction_days WHERE day=?", (origin_day,)
            ).fetchone()
            config = connection.execute("SELECT data FROM extraction_config WHERE id=1").fetchone()
            scope = (
                receipt_scope(json.loads(receipt[0]), config[0] if config else None)
                if receipt
                else None
            )
            if scope is None or candidate.departure_lower.year in scope:
                save_leg(connection, candidate, counters)
                counters["boundary_legs_completed"] += 1
        # The first ground observation makes subsequent tail independent of earlier days.
        if (
            carry is None
            or current > day
            and (leading is None or not leading.valid or leading.arrival is not None)
        ):
            break
        expected += timedelta(days=1)


def directory_bytes(root):
    return sum(file_bytes(path) for path in root.rglob("*"))


class ExtractionBudget:
    def __init__(self, root, limit=MAX_BUDGET_BYTES, policy_db=None, *, output_db=None):
        if type(limit) is not int or not 0 < limit <= MAX_BUDGET_BYTES:
            raise ValueError("Extraction budget must be positive and at most 20 GiB")
        self.root, self.limit = root, limit
        self.policy_db = Path(policy_db).resolve() if policy_db else None
        self.database = Path(output_db).resolve() if output_db else root / "itineraries.db"

    def ceiling(self):
        state = policy(self.policy_db, include_catalog=False) if self.policy_db else None
        if self.policy_db and state is None:
            raise ValueError("Initialize the admin policy database before using --policy-db")
        quota = state["quota_bytes"] if state else None
        return min(self.limit, quota) if quota is not None else self.limit

    def used(self):
        # Output and policy may be the same file; count its SQLite sidecars once.
        databases = {self.database}
        if self.policy_db is not None:
            databases.add(self.policy_db)
        extra = sum(
            database_bytes(path) for path in databases if not path.is_relative_to(self.root)
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
    database = budget.database
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
            if saved_config is not None and not compatible_config(saved_config[0], config):
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
            previous_scope = receipt_scope(receipt, saved_config[0] if saved_config else None)
            scope_complete = (
                previous_scope is None
                or departure_year is not None
                and departure_year in previous_scope
            )
            if scope_complete and (receipt.get("boundary_version") == 1 or not download):
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
                    "boundary_metadata_missing": receipt.get("boundary_version") != 1,
                }
        else:
            receipt = None
    else:
        receipt = None
    budget.check(1024**2)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=10)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        saved_config = connection.execute(
            "SELECT data FROM extraction_config WHERE id=1"
        ).fetchone()
        if saved_config is not None and not compatible_config(saved_config[0], config):
            raise ValueError("Extraction configuration changed; use a separate data root")
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
        counters, seen = (
            Counter(
                records_inserted=0,
                records_already_present=0,
                legs_completed=0,
                boundary_legs_completed=0,
            ),
            set(),
        )
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            record_sources(connection)
            connection.execute("INSERT OR IGNORE INTO extraction_config VALUES (1,?)", (config,))
            last = connection.execute("SELECT MAX(day) FROM extraction_days").fetchone()[0]
            last_receipt = (
                connection.execute(
                    "SELECT receipt FROM extraction_days WHERE day=?", (last,)
                ).fetchone()
                if last
                else None
            )
            if last_receipt and json.loads(last_receipt[0]).get("boundary_version") != 1:
                # Preserve usable tails from a v1 dataset when adding its first new period.
                for hex_id, raw in connection.execute(
                    "SELECT icao_hex,state FROM pending_movements"
                ).fetchall():
                    connection.execute(
                        "INSERT OR IGNORE INTO extraction_boundaries VALUES (?,?,?)",
                        (hex_id, last, canonical({"leading": None, "tail": json.loads(raw)})),
                    )
            affected = set()
            current_tails = []
            for data, compressed_bytes in archive_traces(paths):
                counters["aircraft_members"] += 1
                counters["raw_member_bytes"] += compressed_bytes
                hex_id = data.get("icao")
                if hex_id in seen:
                    raise ValueError("Duplicate aircraft member; day not committed")
                seen.add(hex_id)
                leading = LeadingMovement(aircraft_type_code=data.get("t") or "")
                legs, state, reason = reduce_movement(
                    data, None, lookup, rules, start_us, end_us, leading=leading
                )
                counters[reason] += 1
                for leg in legs:
                    if departure_year is not None and leg.departure_lower.year != departure_year:
                        counters["legs_outside_departure_year"] += 1
                        continue
                    save_leg(connection, leg, counters)
                    counters["legs_completed"] += 1
                    if counters["legs_completed"] % 100 == 0:
                        budget.check(1024**2)
                if reason == "processed" and (
                    state is not None or leading.valid and leading.first_us is not None
                ):
                    connection.execute(
                        "INSERT OR REPLACE INTO extraction_boundaries VALUES (?,?,?)",
                        (
                            hex_id,
                            str(day),
                            canonical(
                                {
                                    "leading": leading.model_dump() if leading.valid else None,
                                    "tail": state.model_dump() if state else None,
                                }
                            ),
                        ),
                    )
                    affected.add(hex_id)
                if state is not None:
                    current_tails.append((hex_id, state.model_dump_json()))
                if counters["aircraft_members"] % 100 == 0:
                    budget.check(1024**2)
            if last is None or str(day) >= last:
                connection.execute("DELETE FROM pending_movements")
                connection.executemany("INSERT INTO pending_movements VALUES (?,?)", current_tails)
            prior_scope = (
                receipt_scope(receipt, saved_config[0] if saved_config else None) if receipt else []
            )
            scope = (
                None
                if departure_year is None or prior_scope is None
                else sorted(set(prior_scope + [departure_year]))
            )
            receipt = {
                "day": str(day),
                "inputs": inputs,
                "counts": dict(counters),
                "pending_airborne": sum(
                    MovementState.model_validate_json(raw).first_airborne_us is not None
                    for _, raw in current_tails
                ),
                "departure_years": scope,
                "boundary_version": 1,
                "time_basis": "observed_ground_transition_bounds; not scheduled or gate times",
                "coverage": "conservative inferred legs; configured commercial/type subset",
            }
            connection.execute(
                "INSERT INTO extraction_days VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET receipt=excluded.receipt",
                (str(day), selection, canonical(receipt)),
            )
            for index, hex_id in enumerate(sorted(affected), 1):
                reconcile_boundaries(connection, hex_id, day, rules, counters)
                if index % 100 == 0:
                    budget.check(1024**2)
            counters["legs_completed"] += counters["boundary_legs_completed"]
            receipt["counts"] = dict(counters)
            connection.execute(
                "UPDATE extraction_days SET receipt=? WHERE day=?", (canonical(receipt), str(day))
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
    output_db=None,
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
    budget = ExtractionBudget(root, limit, policy_db, output_db=output_db)
    # Serialize by destination as well as scratch directory when these differ.
    lock = (
        budget.database.with_name(budget.database.name + ".extraction.lock")
        if output_db
        else root / ".extract.lock"
    )
    with file_mutex(root / ".extract.lock"), file_mutex(lock) if output_db else nullcontext():
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
