"""Daily extraction with separate database quota and disposable working-space budget."""

import hashlib
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing, nullcontext
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from core.disk_quota import database_bytes, file_bytes, policy
from core.file_lock import file_mutex
from core.models import FlightRecord
from core.storage import SCHEMA as SERVING_SCHEMA
from core.storage import store_flight, utc_us
from ingestion.adsblol.archive import (
    MAX_BUDGET_BYTES,
    archive_traces,
    download_manifest,
    validate_asset,
    verified,
)
from ingestion.adsblol.diagnostics import LOGGER, Diagnostics
from ingestion.adsblol.movements import (
    AirportLookup,
    LeadingMovement,
    MovementState,
    advance_boundary,
    reduce_movement,
)

PIPELINE_VERSION = "observed-itinerary-v4"

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
    SERVING_SCHEMA
    + SOURCE_SCHEMA
    + """
CREATE VIEW IF NOT EXISTS itinerary_legs AS
SELECT provider_id AS key, substr(departure_observed_lower,1,10) AS departure_date,
json_object('aircraft_type_code',aircraft_type_code,
    'arrival_lower',replace(replace(arrival_observed_lower,'.000000',''),'+00:00','Z'),
    'arrival_upper',replace(replace(arrival_observed_upper,'.000000',''),'+00:00','Z'),
    'callsign',callsign,
    'departure_lower',replace(replace(departure_observed_lower,'.000000',''),'+00:00','Z'),
    'departure_upper',replace(replace(departure_observed_upper,'.000000',''),'+00:00','Z'),
    'destination_iata',destination_iata,'review_reasons',json(review_reasons),'evidence',CASE WHEN evidence_kind='inferred_airport_proximity' THEN 'airport_proximity_and_time_window' ELSE 'ground_brackets_and_nearby_airports' END,
    'origin_iata',origin_iata,'status','inferred_candidate') AS data
FROM flights WHERE provider='adsblol' AND departure_observed_lower IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS itinerary_legs_update INSTEAD OF UPDATE ON itinerary_legs
BEGIN
    UPDATE flight_legs SET callsign=json_extract(NEW.data,'$.callsign')
    WHERE source_id=(SELECT id FROM data_sources WHERE code='adsblol') AND source_record_key=OLD.key;
END;
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
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='data_sources'").fetchone():
        connection.executemany(
            "INSERT INTO data_sources(code,name,source_url,license_expression,license_url) VALUES (?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,source_url=excluded.source_url,license_expression=excluded.license_expression,license_url=excluded.license_url",
            SOURCE_ROWS,
        )
        connection.execute(
            "UPDATE data_sources SET attribution=? WHERE code='adsblol'",
            (
                "Contains information from ADSB.lol, available under the Open Database Licence (ODbL) 1.0.",
            ),
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


def save_leg(connection, leg, counters, *, diagnostics=None):
    # Departure evidence may improve when a previous day is backfilled. Anchor
    # the movement to its arrival observation so that refinement keeps one key.
    # Keep the source identity namespace stable across filtering revisions.
    identity = f"observed-itinerary-v3:{leg.icao_hex}:{leg.arrival_lower.isoformat()}"
    key = hashlib.blake2b(identity.encode(), digest_size=16).hexdigest()
    return save_candidate(
        connection,
        key,
        leg.model_dump(mode="json", exclude={"icao_hex"}),
        counters,
        diagnostics=diagnostics,
    )


def save_candidate(connection, key, data, counters, *, diagnostics=None):
    protected = connection.execute(
        "SELECT 1 FROM flight_tombstones WHERE source_id=(SELECT id FROM data_sources WHERE code='adsblol') AND source_record_key=? UNION ALL SELECT 1 FROM flight_legs WHERE source_id=(SELECT id FROM data_sources WHERE code='adsblol') AND source_record_key=? AND locally_modified_us IS NOT NULL",
        (key, key),
    ).fetchone()
    if protected:
        counters["records_protected"] += 1
        return False
    old = connection.execute("SELECT data FROM itinerary_legs WHERE key=?", (key,)).fetchone()
    if old is not None and json.loads(old[0]) != data:
        previous = json.loads(old[0])

        def quality(value):
            return (
                value["evidence"] == "ground_brackets_and_nearby_airports",
                -utc_us(datetime.fromisoformat(value["departure_upper"])),
                utc_us(datetime.fromisoformat(value["departure_lower"])),
                -utc_us(datetime.fromisoformat(value["arrival_upper"])),
            )

        if previous["arrival_lower"] == data["arrival_lower"]:
            if quality(previous) > quality(data):
                counters["records_already_present"] += 1
                if diagnostics is not None:
                    diagnostics.count("metadata", "weaker_reconstruction_ignored")
                return False
            improved = quality(data) > quality(previous)
        else:
            improved = False
        if not improved:
            if diagnostics is not None:
                diagnostics.count("invalid_records", "conflicting_itinerary_identity")
            raise ValueError("Conflicting itinerary identity; day not committed")
    batch = connection.execute(
        "SELECT id FROM ingestion_batches WHERE source_id=(SELECT id FROM data_sources WHERE code='adsblol') AND state='running' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    outcome = store_flight(
        connection,
        FlightRecord(
            provider="adsblol",
            provider_id=key,
            callsign=data["callsign"],
            review_reasons=data.get("review_reasons", []),
            origin_iata=data["origin_iata"],
            destination_iata=data["destination_iata"],
            aircraft_type_code=data["aircraft_type_code"],
            evidence_kind="inferred_airport_proximity"
            if data["evidence"] == "airport_proximity_and_time_window"
            else "inferred_ground_brackets",
            departure_observed_lower=data["departure_lower"],
            departure_observed_upper=data["departure_upper"],
            arrival_observed_lower=data["arrival_lower"],
            arrival_observed_upper=data["arrival_upper"],
            fetched_at=datetime.now(UTC),
        ),
        restore=False,
        batch_id=batch[0] if batch else None,
    )
    if outcome == "protected":
        counters["records_protected"] += 1
        return False
    inserted = old is None
    counters[
        "records_inserted"
        if inserted
        else "records_updated"
        if outcome == "updated"
        else "records_already_present"
    ] += 1
    return bool(inserted)


def boundary_parts(raw):
    data = json.loads(raw)
    leading = LeadingMovement.model_validate(data["leading"]) if data.get("leading") else None
    tail = MovementState.model_validate(data["tail"]) if data.get("tail") else None
    return leading, tail


def reconcile_boundaries(connection, hex_id, day, rules, counters, *, diagnostics=None):
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
        candidate, carry = advance_boundary(
            hex_id, carry, leading, tail, rules, diagnostics=diagnostics
        )
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
                save_leg(connection, candidate, counters, diagnostics=diagnostics)
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
        self.root, self.limit = Path(root).resolve(), limit
        self.policy_db = Path(policy_db).resolve() if policy_db else None
        self.database = Path(output_db).resolve() if output_db else self.root / "itineraries.db"

    def ceiling(self):
        """The CLI limit belongs to scratch space, independent of database policy."""
        return self.limit

    def database_ceiling(self):
        state = policy(self.policy_db or self.database)
        if self.policy_db and state is None:
            raise ValueError("Initialize the admin policy database before using --policy-db")
        return state["quota_bytes"] if state else None

    def used(self):
        """Raw/partial inputs and staging, excluding the durable database family."""
        databases = {self.database}
        if self.policy_db is not None:
            databases.add(self.policy_db)
        excluded = {
            item
            for path in databases
            for item in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
        }
        return sum(file_bytes(path) for path in self.root.rglob("*") if path not in excluded)

    def check_database(self, connection=None):
        limit = self.database_ceiling()
        if limit is None:
            return
        used = database_bytes(self.database)
        if connection is not None:
            # Pending pages may still be in SQLite's memory cache. Include logical
            # growth before commit so an oversized day rolls back, rather than
            # becoming a protected overage. WAL can add transient overhead;
            # subsequent IO rechecks physical usage after checkpointing.
            projected = (
                connection.execute("PRAGMA page_count").fetchone()[0]
                * connection.execute("PRAGMA page_size").fetchone()[0]
            )
            used += max(0, projected - file_bytes(self.database))
        if used > limit:
            raise OSError(
                f"Database needs {used:,} bytes but quota is {limit:,}; "
                "extraction stopped. Raise the database quota; durable legs are preserved."
            )

    def check(self, reserve=0):
        self.check_database()
        limit = self.ceiling()
        used = self.used()
        if used + reserve > limit:
            raise OSError(
                f"Extraction working space needs {used + reserve:,} bytes but budget is {limit:,}; "
                "extraction stopped. Raise --budget-gib or free working space; durable legs are preserved."
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


def scan_day(
    connection,
    paths,
    day,
    lookup,
    rules,
    start_us,
    end_us,
    departure_year,
    budget,
    counters,
    diagnostics,
):
    seen = set()
    affected = set()
    current_tails = []
    diagnostics.phase = "archive_scan"
    for data, compressed_bytes in archive_traces(paths, diagnostics=diagnostics):
        counters["aircraft_members"] += 1
        counters["raw_member_bytes"] += compressed_bytes
        hex_id = data.get("icao")
        diagnostics.aircraft_id = hex_id if isinstance(hex_id, str) else None
        diagnostics.observation_index = None
        if isinstance(hex_id, str) and re.fullmatch(r"[0-9a-f]{6}", hex_id):
            if hex_id in seen:
                diagnostics.count("invalid_records", "duplicate_aircraft_member")
                raise ValueError("Duplicate aircraft member; day not committed")
            seen.add(hex_id)
        kind = data.get("t")
        leading = LeadingMovement(aircraft_type_code=kind if isinstance(kind, str) else "")
        diagnostics.phase = "reduce_movement"
        legs, state, reason = reduce_movement(
            data,
            None,
            lookup,
            rules,
            start_us,
            end_us,
            leading=leading,
            diagnostics=diagnostics,
        )
        counters[reason] += 1
        for leg in legs:
            if departure_year is not None and (
                leg.departure_lower.year != departure_year
                or leg.departure_upper.year != departure_year
            ):
                counters["legs_outside_departure_year"] += 1
                continue
            save_leg(connection, leg, counters, diagnostics=diagnostics)
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
        if counters["aircraft_members"] % 1000 == 0:
            LOGGER.info(
                "%s",
                canonical(
                    {
                        "event": "day_progress",
                        "day": str(day),
                        "committed": False,
                        "counts_provisional": True,
                        "counts": dict(counters),
                        "diagnostics": diagnostics.summary(),
                    }
                ),
            )
        diagnostics.phase = "archive_scan"
        diagnostics.aircraft_id = None
        diagnostics.observation_index = None
    return affected, current_tails


def _extract_day(
    root,
    day,
    assets,
    airports,
    rules,
    budget,
    counters,
    diagnostics,
    *,
    download=False,
    client=None,
    departure_year=None,
    refresh_diagnostics=False,
):
    database = budget.database
    lookup = AirportLookup(airports)
    # Set serialization is sorted explicitly for restart-stable configuration.
    config = canonical(
        {
            "algorithm": PIPELINE_VERSION,
            "quality": rules.model_dump(),
            "airports": sorted(airports),
            "departure_year": departure_year,
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
                raise ValueError("Extraction configuration changed; use a fresh output database")
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
            if (
                not refresh_diagnostics
                and scope_complete
                and (receipt.get("boundary_version") == 1 or not download)
            ):
                diagnostics.committed = True
                diagnostics.phase = "cleanup"
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
    with (
        closing(sqlite3.connect(database, timeout=10)) as connection,
        TemporaryDirectory(prefix=".day-stage-", dir=root) as scratch,
        closing(sqlite3.connect(Path(scratch) / "day.db")) as staged,
    ):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        legacy = connection.execute(
            "SELECT type FROM sqlite_master WHERE name='itinerary_legs'"
        ).fetchone()
        if legacy and legacy[0] == "table":
            if connection.execute("SELECT COUNT(*) FROM itinerary_legs").fetchone()[0]:
                raise ValueError("Legacy extraction schema; use a new output database")
            connection.execute("DROP TABLE itinerary_legs")
        connection.executescript(SCHEMA)
        saved_config = connection.execute(
            "SELECT data FROM extraction_config WHERE id=1"
        ).fetchone()
        if saved_config is not None and not compatible_config(saved_config[0], config):
            raise ValueError("Extraction configuration changed; use a fresh output database")
        start_us = int(datetime.combine(day, time.min, UTC).timestamp()) * 1_000_000
        end_us = start_us + 86_400_000_000
        with connection:
            record_sources(connection)
            sid = connection.execute("SELECT id FROM data_sources WHERE code='adsblol'").fetchone()[
                0
            ]
            started = utc_us(datetime.now(UTC))
            connection.execute(
                "UPDATE ingestion_batches SET state='interrupted',finished_us=MAX(started_us,?),error_summary='Previous extraction ended before commit' WHERE source_id=? AND state='running'",
                (started, sid),
            )
            batch_id = connection.execute(
                "INSERT INTO ingestion_batches(source_id,input_start_us,input_end_us,started_us,state,pipeline_version,config_sha256,config_json,inputs_json,diagnostics_json) VALUES (?,?,?,?,'running',?,?,?,?, '{}')",
                (
                    sid,
                    start_us,
                    end_us,
                    started,
                    PIPELINE_VERSION,
                    hashlib.sha256(config.encode()).hexdigest(),
                    config,
                    canonical(assets),
                ),
            ).lastrowid
        output = root / "raw"
        diagnostics.phase = "download"
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
        with connection:
            connection.execute(
                "UPDATE ingestion_batches SET inputs_json=? WHERE id=?",
                (canonical(inputs), batch_id),
            )
        staged.executescript(SCHEMA)
        with staged:
            record_sources(staged)
            affected, current_tails = scan_day(
                staged,
                paths,
                day,
                lookup,
                rules,
                start_us,
                end_us,
                departure_year,
                budget,
                counters,
                diagnostics,
            )
        counters["candidate_records"] = counters["records_inserted"]
        counters["records_inserted"] = counters["records_already_present"] = 0
        diagnostics.phase = "prepare_commit"
        budget.check(1024**2)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            record_sources(connection)
            airport_source = connection.execute(
                "SELECT id FROM data_sources WHERE code='ourairports'"
            ).fetchone()[0]
            connection.executemany(
                "INSERT INTO airports(iata,latitude,longitude,metadata_source_id) VALUES(?,?,?,?) ON CONFLICT(iata) DO UPDATE SET latitude=COALESCE(airports.latitude,excluded.latitude),longitude=COALESCE(airports.longitude,excluded.longitude),metadata_source_id=COALESCE(airports.metadata_source_id,excluded.metadata_source_id)",
                [(point[0], point[1], point[2], airport_source) for point in airports],
            )
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
            diagnostics.phase = "merge_candidates"
            for key, raw in staged.execute("SELECT key,data FROM itinerary_legs"):
                save_candidate(connection, key, json.loads(raw), counters, diagnostics=diagnostics)
            connection.executemany(
                "INSERT OR REPLACE INTO extraction_boundaries VALUES (?,?,?)",
                staged.execute("SELECT icao_hex,day,data FROM extraction_boundaries"),
            )
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
                "time_basis": "inferred ground brackets or airborne proximity windows; not schedules or certified actual times",
                "coverage": "incomplete observed movement candidates; commercial service is not certified",
            }
            connection.execute(
                "INSERT INTO extraction_days VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET receipt=excluded.receipt",
                (str(day), selection, canonical(receipt)),
            )
            diagnostics.phase = "reconcile_boundaries"
            for index, hex_id in enumerate(sorted(affected), 1):
                diagnostics.aircraft_id = hex_id
                reconcile_boundaries(
                    connection, hex_id, day, rules, counters, diagnostics=diagnostics
                )
                if index % 100 == 0:
                    budget.check(1024**2)
            counters["legs_completed"] += counters["boundary_legs_completed"]
            receipt["counts"] = dict(counters)
            receipt["diagnostics"] = diagnostics.summary()
            receipt["batch_id"] = batch_id
            connection.execute(
                "UPDATE extraction_days SET receipt=? WHERE day=?", (canonical(receipt), str(day))
            )
            budget.check(1024**2)
            connection.execute(
                "UPDATE ingestion_batches SET state='committed',finished_us=?,inserted_count=?,updated_count=?,duplicate_count=?,diagnostics_json=? WHERE id=?",
                (
                    utc_us(datetime.now(UTC)),
                    counters["records_inserted"],
                    counters["records_updated"],
                    counters["records_already_present"],
                    canonical(diagnostics.summary()),
                    batch_id,
                ),
            )
            budget.check_database(connection)
            diagnostics.phase = "commit"
        diagnostics.committed = True
        diagnostics.phase = "checkpoint"
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        budget.check()
    diagnostics.phase = "cleanup"
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
    refresh_diagnostics=False,
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
        counters = Counter(
            records_inserted=0,
            records_updated=0,
            records_already_present=0,
            legs_completed=0,
            boundary_legs_completed=0,
        )
        diagnostics = Diagnostics()
        LOGGER.info(
            "%s",
            canonical({"event": "day_started", "day": str(day), "database": str(budget.database)}),
        )
        try:
            receipt = _extract_day(
                root,
                day,
                assets,
                airports,
                rules,
                budget,
                counters,
                diagnostics,
                download=download,
                client=client,
                departure_year=departure_year,
                refresh_diagnostics=refresh_diagnostics,
            )
        except (Exception, KeyboardInterrupt) as error:
            if not diagnostics.committed and budget.database.is_file():
                with closing(sqlite3.connect(budget.database, timeout=10)) as failure, failure:
                    if failure.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='ingestion_batches'"
                    ).fetchone():
                        failure.execute(
                            "UPDATE ingestion_batches SET state=?,finished_us=MAX(started_us,?),error_summary=?,diagnostics_json=? WHERE source_id=(SELECT id FROM data_sources WHERE code='adsblol') AND input_start_us=? AND state='running'",
                            (
                                "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                                utc_us(datetime.now(UTC)),
                                str(error),
                                canonical(diagnostics.summary()),
                                utc_us(datetime.combine(day, time.min, UTC)),
                            ),
                        )
            LOGGER.error(
                "%s",
                canonical(
                    {
                        "event": "day_failed",
                        "day": str(day),
                        "database": str(budget.database),
                        "phase": diagnostics.phase,
                        "committed": diagnostics.committed,
                        "counts_provisional": not diagnostics.committed,
                        "records_applied": counters["records_inserted"]
                        + counters["records_updated"]
                        if diagnostics.committed
                        else 0,
                        "aircraft_id": diagnostics.aircraft_id,
                        "observation_index": diagnostics.observation_index,
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "counts": dict(counters),
                        "diagnostics": diagnostics.summary(),
                    }
                ),
            )
            raise
        LOGGER.info(
            "%s",
            canonical(
                {
                    "event": "day_finished",
                    "day": str(day),
                    "result": receipt["result"],
                    "committed": True,
                    "counts_historical": receipt["result"] == "already_committed",
                    "records_applied": counters["records_inserted"] + counters["records_updated"],
                    "counts": receipt["counts"],
                    "diagnostics": receipt.get("diagnostics"),
                    "diagnostics_missing": "diagnostics" not in receipt,
                }
            ),
        )
        return receipt
