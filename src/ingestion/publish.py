"""Bounded, explicit publication of admitted candidates into a clean serving store."""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from core.database_roles import require_ingestion
from core.file_lock import file_mutex
from core.storage import SQLiteConnection, flight_record, source_id, store_flight, utc_us
from ingestion.references import copy_catalog, copy_source
from ingestion.store import check_quota
from ingestion.updater import FlightUpdater

PARTITION_TIME_SQL = """CASE
    WHEN departure_scheduled_us IS NOT NULL AND arrival_scheduled_us IS NOT NULL THEN departure_scheduled_us
    WHEN departure_observed_lower_us IS NOT NULL THEN departure_observed_lower_us
    WHEN departure_actual_us IS NOT NULL OR arrival_actual_us IS NOT NULL THEN COALESCE(departure_actual_us,arrival_actual_us)
    ELSE COALESCE(departure_scheduled_us,arrival_scheduled_us)
END"""

PUBLICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS publication_origin (
    id INTEGER PRIMARY KEY CHECK(id=1), store_id TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS publication_membership (
    store_id TEXT NOT NULL,
    source_code TEXT NOT NULL,
    source_record_key TEXT NOT NULL,
    departure_us INTEGER NOT NULL,
    PRIMARY KEY(store_id,source_code,source_record_key)
) STRICT, WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS publication_window ON publication_membership(store_id,departure_us);
CREATE TABLE IF NOT EXISTS publication_runs (
    id INTEGER PRIMARY KEY,
    store_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    start_us INTEGER NOT NULL,
    end_us INTEGER NOT NULL,
    finished_us INTEGER NOT NULL,
    inserted INTEGER NOT NULL,
    updated INTEGER NOT NULL,
    retired INTEGER NOT NULL,
    protected INTEGER NOT NULL
) STRICT;
"""


def event_us(record):
    # Prefer the selected basis. Preserve partial and cancelled source facts for
    # lookup/admin; the shared search view decides whether a journey is usable.
    basis = record.effective_time_basis
    fields = (
        ["departure_observed_lower", "arrival_observed_lower"]
        if basis == "observed"
        else [f"departure_{basis}", f"arrival_{basis}"]
    )
    fields += ["departure_scheduled", "departure_actual", "arrival_scheduled", "arrival_actual"]
    return utc_us(
        next(getattr(record, field) for field in fields if getattr(record, field) is not None)
    )


def admitted(record):
    return not record.blocked_for_search


def publish_day(ingestion_db, serving_db, day, *, audit_routes=True):
    ingestion_db, serving_db = Path(ingestion_db).resolve(), Path(serving_db).resolve()
    if ingestion_db == serving_db or (serving_db.exists() and ingestion_db.samefile(serving_db)):
        raise ValueError("Ingestion and serving databases must be different files")
    day = date.fromisoformat(day) if isinstance(day, str) else day
    reference_audit = None
    if audit_routes:
        from ingestion.vrs import audit_cached_day

        reference_audit = audit_cached_day(ingestion_db, day)
    lower = utc_us(datetime.combine(day, time.min, UTC))
    upper = lower + 86400_000000
    reader = SQLiteConnection(ingestion_db, read_only=True)
    with reader.connect() as source:
        source.execute("BEGIN")
        require_ingestion(source)
        identity = source.execute(
            "SELECT store_id,generation FROM ingestion_store_metadata WHERE id=1"
        ).fetchone()
        store_id, generation = identity
        records = {}
        for row in source.execute(
            f"""WITH selected AS (
                SELECT source_id,source_record_key FROM flight_legs
                WHERE {PARTITION_TIME_SQL}>=? AND {PARTITION_TIME_SQL}<?
            ) SELECT f.* FROM selected leg JOIN data_sources s ON s.id=leg.source_id
              JOIN flights f ON f.provider=s.code AND f.provider_id=leg.source_record_key""",
            (lower, upper),
        ):
            record = flight_record(row)
            if lower <= event_us(record) < upper:
                records[(record.provider, record.provider_id)] = record
        target = FlightUpdater(serving_db)
        target.initialize()
        with target.connect() as output:
            output.executescript(PUBLICATION_SCHEMA)
            output.execute("BEGIN IMMEDIATE")
            check_quota(serving_db)
            origin = output.execute("SELECT store_id FROM publication_origin WHERE id=1").fetchone()
            if origin is not None and origin[0] != store_id:
                raise ValueError("Serving publication is owned by a different ingestion store")
            output.execute("INSERT OR IGNORE INTO publication_origin VALUES(1,?)", (store_id,))
            owned = {
                tuple(row)
                for row in output.execute(
                    "SELECT source_code,source_record_key FROM publication_membership WHERE store_id=? AND departure_us>=? AND departure_us<?",
                    (store_id, lower, upper),
                )
            }
            # Recheck previously published identities even if corrected departure
            # windows moved them out of this day. Held/missing facts must retract.
            for key in owned:
                if key not in records:
                    row = source.execute(
                        "SELECT * FROM flights WHERE provider=? AND provider_id=?", key
                    ).fetchone()
                    records[key] = flight_record(row) if row is not None else None
            counts = dict(inserted=0, updated=0, duplicate=0, retired=0, protected=0, held=0)
            providers = {
                record.provider
                for record in records.values()
                if record is not None and admitted(record)
            }
            if any(
                record is not None and record.current_route_reference for record in records.values()
            ):
                providers.add("vrs")
            for row in source.execute("SELECT * FROM data_sources"):
                if row["code"] not in providers:
                    continue
                copy_source(row, output)
            copy_catalog(source, output)
            for key, record in records.items():
                if record is None or not admitted(record):
                    counts["held"] += int(record is not None)
                    if key in owned:
                        sid = source_id(output, key[0])
                        counts["retired"] += output.execute(
                            "DELETE FROM flight_legs WHERE source_id=? AND source_record_key=? AND locally_modified_us IS NULL",
                            (sid, key[1]),
                        ).rowcount
                        counts["protected"] += int(
                            output.execute(
                                "SELECT 1 FROM flight_legs WHERE source_id=? AND source_record_key=? AND locally_modified_us IS NOT NULL",
                                (sid, key[1]),
                            ).fetchone()
                            is not None
                        )
                        output.execute(
                            "DELETE FROM publication_membership WHERE store_id=? AND source_code=? AND source_record_key=?",
                            (store_id, *key),
                        )
                    continue
                sid = source_id(output, key[0])
                outcome = store_flight(output, record, restore=False)
                counts[outcome] += 1
                if outcome == "protected":
                    continue
                if record.itinerary_reviewed:
                    output.execute(
                        "UPDATE flight_legs SET locally_modified_us=COALESCE(locally_modified_us,?) WHERE source_id=? AND source_record_key=?",
                        (utc_us(datetime.now(UTC)), sid, key[1]),
                    )
                output.execute(
                    """INSERT INTO publication_membership VALUES(?,?,?,?)
                    ON CONFLICT(store_id,source_code,source_record_key) DO UPDATE SET departure_us=excluded.departure_us
                    WHERE publication_membership.departure_us<>excluded.departure_us""",
                    (store_id, *key, event_us(record)),
                )
            for row in source.execute("SELECT * FROM airport_transfers"):
                # Preserve serving edits and never introduce a transfer invalidated
                # by a serving city override.
                cities = [
                    item[0]
                    for item in output.execute(
                        "SELECT city_id FROM airports WHERE iata IN (?,?)",
                        (row["arrival_iata"], row["departure_iata"]),
                    )
                ]
                if len(cities) == 2 and cities[0] is not None and cities[0] == cities[1]:
                    output.execute(
                        "INSERT OR IGNORE INTO airport_transfers VALUES(?,?,?,?)", tuple(row)
                    )
            output.execute(
                "INSERT INTO publication_runs(store_id,generation,start_us,end_us,finished_us,inserted,updated,retired,protected) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    store_id,
                    generation,
                    lower,
                    upper,
                    utc_us(datetime.now(UTC)),
                    counts["inserted"],
                    counts["updated"],
                    counts["retired"],
                    counts["protected"],
                ),
            )
            check_quota(serving_db, output)
    return dict(
        day=str(day),
        route_audit=reference_audit,
        ingestion_database=str(ingestion_db),
        serving_database=str(serving_db),
        **counts,
    )


def publish_range(
    ingestion_db, serving_db, start, end, *, preserve_serving=None, audit_routes=True
):
    if end < start:
        raise ValueError("Publish end must follow start")
    # One committed publication per day; readers remain live, retries are idempotent.
    with file_mutex(Path(str(Path(serving_db).resolve()) + ".publish.lock")):
        if preserve_serving is not None:
            from ingestion.serving_controls import prepare_serving

            prepare_serving(preserve_serving, serving_db)
        for offset in range((end - start).days + 1):
            yield publish_day(
                ingestion_db, serving_db, start + timedelta(days=offset), audit_routes=audit_routes
            )
