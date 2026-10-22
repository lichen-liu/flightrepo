"""Verified serving facts with a durable, separate candidate workspace.

Binding is opt-in. Publication commits first; only then may private copies be
removed. A missing private row is never a retraction in this mode. Explicit
tombstones and held replacement candidates still retract producer-owned facts.
"""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from core.database_roles import require_serving
from core.file_lock import file_mutex
from core.storage import SQLiteConnection, flight_record, source_id, store_flight, utc_us
from ingestion.store import IngestionStore, check_quota, job_lock
from ingestion.updater import FlightUpdater

CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingestion_fact_authority (
    id INTEGER PRIMARY KEY CHECK(id=1),
    serving_path TEXT NOT NULL,
    serving_identity TEXT NOT NULL,
    store_id TEXT NOT NULL,
    generation INTEGER NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS ingestion_fact_history (
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    source_record_key TEXT NOT NULL,
    first_batch_id INTEGER,
    first_seen_us INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY(source_id,source_record_key),
    FOREIGN KEY(first_batch_id,source_id) REFERENCES ingestion_batches(id,source_id)
) STRICT, WITHOUT ROWID;
"""
TARGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS serving_fact_authority (
    id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL
) STRICT;
"""


@contextmanager
def candidate_writer(path):
    with job_lock(path, blocking=False) as acquired:
        if not acquired:
            raise ValueError("Stop the active ingestion job before staging or releasing facts")
        with IngestionStore(path).connect() as c:
            yield c


def configuration(c):
    if not c.execute(
        "SELECT 1 FROM sqlite_schema WHERE name='ingestion_fact_authority'"
    ).fetchone():
        return None
    row = c.execute("SELECT * FROM ingestion_fact_authority WHERE id=1").fetchone()
    if row is None:
        return None
    values = tuple(row)
    if (
        tuple(
            c.execute(
                "SELECT store_id,generation FROM ingestion_store_metadata WHERE id=1"
            ).fetchone()
        )
        != values[3:]
    ):
        raise ValueError("Serving authority belongs to a different ingestion generation")
    return dict(zip(("id", "serving_path", "serving_identity", "store_id", "generation"), values))


def validate_target(c, config, schema=""):
    if schema:
        if c.execute(
            f"SELECT 1 FROM {schema}sqlite_schema WHERE name='ingestion_store_metadata'"
        ).fetchone():
            raise ValueError("Authoritative facts require a serving database")
    else:
        require_serving(c)
    if not c.execute(
        f"SELECT 1 FROM {schema}sqlite_schema WHERE name='serving_fact_authority'"
    ).fetchone():
        raise ValueError("Authoritative serving database identity is missing")
    row = c.execute(f"SELECT identity FROM {schema}serving_fact_authority WHERE id=1").fetchone()
    if row is None or row[0] != config["serving_identity"]:
        raise ValueError("Authoritative serving database was replaced; restore its verified backup")
    row = c.execute(f"SELECT store_id FROM {schema}publication_origin WHERE id=1").fetchone()
    if row is None or row[0] != config["store_id"]:
        raise ValueError("Authoritative serving database has a different publication owner")


def selected(path, serving=None):
    with SQLiteConnection(path, read_only=True).connect() as c:
        config = configuration(c)
    if config is not None:
        target = Path(config["serving_path"])
        if serving is not None and target != Path(serving).resolve():
            raise ValueError("Use the bound authoritative serving database")
        # Never initialize a missing canonical store or silently fall back to a
        # partial candidate database. The URI prevents accidental file creation.
        with SQLiteConnection(target, read_only=True).connect() as c:
            validate_target(c, config)
    return config


def bind(path, serving):
    """Bind under a producer lease; do not remove any existing facts."""
    with job_lock(path, blocking=False) as acquired:
        if not acquired:
            raise ValueError("Stop the active ingestion job before binding serving authority")
        _bind(path, serving)


def _bind(path, serving):
    from ingestion.publish import PUBLICATION_SCHEMA

    path, serving = Path(path).resolve(), Path(serving).resolve()
    if path == serving or serving.exists() and path.samefile(serving):
        raise ValueError("Ingestion and serving databases must be different files")
    if selected(path, serving) is not None:
        return
    with file_mutex(Path(str(serving) + ".publish.lock")):
        if serving.exists():
            with SQLiteConnection(serving, read_only=True).connect() as target:
                require_serving(target)
                owner = (
                    target.execute("SELECT store_id FROM publication_origin WHERE id=1").fetchone()
                    if target.execute(
                        "SELECT 1 FROM sqlite_schema WHERE name='publication_origin'"
                    ).fetchone()
                    else None
                )
            with SQLiteConnection(path, read_only=True).connect() as source:
                own_id = source.execute(
                    "SELECT store_id FROM ingestion_store_metadata WHERE id=1"
                ).fetchone()[0]
            if owner is not None and owner[0] != own_id:
                raise ValueError("Serving publication is owned by a different ingestion store")
        target = FlightUpdater(serving)
        target.initialize()
        with IngestionStore(path).connect() as source, target.connect() as output:
            identity = tuple(
                source.execute(
                    "SELECT store_id,generation FROM ingestion_store_metadata WHERE id=1"
                ).fetchone()
            )
            output.executescript(PUBLICATION_SCHEMA + TARGET_SCHEMA)
            output.execute("BEGIN IMMEDIATE")
            owner = output.execute("SELECT store_id FROM publication_origin WHERE id=1").fetchone()
            if owner is not None and owner[0] != identity[0]:
                raise ValueError("Serving publication is owned by a different ingestion store")
            output.execute("INSERT OR IGNORE INTO publication_origin VALUES(1,?)", (identity[0],))
            output.execute(
                "INSERT OR IGNORE INTO serving_fact_authority VALUES(1,?)", (str(uuid.uuid4()),)
            )
            canonical_id = output.execute(
                "SELECT identity FROM serving_fact_authority WHERE id=1"
            ).fetchone()[0]
            check_quota(serving, output)
        # A crash between these commits leaves only a harmless serving identity;
        # no private records have been evicted and binding can be retried.
        with IngestionStore(path).connect() as c:
            c.executescript(CONFIG_SCHEMA)
            c.execute("BEGIN IMMEDIATE")
            # Version 2 fences old producer binaries, whose missing-row logic
            # assumes the private store still contains every published flight.
            c.execute(
                "CREATE TABLE ingestion_identity_v2 (id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL CHECK(version IN (1,2)),store_id TEXT NOT NULL UNIQUE,generation INTEGER NOT NULL CHECK(generation>0)) STRICT"
            )
            c.execute(
                "INSERT INTO ingestion_identity_v2 SELECT id,2,store_id,generation FROM ingestion_store_metadata"
            )
            c.execute("DROP TABLE ingestion_store_metadata")
            c.execute("ALTER TABLE ingestion_identity_v2 RENAME TO ingestion_store_metadata")
            c.execute(
                "INSERT INTO ingestion_fact_authority VALUES(1,?,?,?,?)",
                (str(serving), canonical_id, *identity),
            )


def attach(c):
    """Attach canonical facts read-only to a candidate writer, validating ownership."""
    config = configuration(c)
    if config is None:
        return False
    c.execute(
        "ATTACH DATABASE ? AS canonical", (Path(config["serving_path"]).as_uri() + "?mode=ro",)
    )
    validate_target(c, config, "canonical.")
    return True


def hydrate_key(c, provider, key):
    """Recover the current canonical leg before comparing a backfill candidate."""
    if not any(row[1] == "canonical" for row in c.execute("PRAGMA database_list")):
        return
    sid = source_id(c, provider)
    # Serving tombstones and manual changes take precedence even when no private
    # copy exists. Retain these controls in the candidate workspace as well.
    deleted = c.execute(
        "SELECT t.deleted_us FROM canonical.flight_tombstones t JOIN canonical.data_sources s ON s.id=t.source_id WHERE s.code=? AND t.source_record_key=?",
        (provider, key),
    ).fetchone()
    if deleted:
        c.execute("INSERT OR IGNORE INTO flight_tombstones VALUES(?,?,?)", (sid, key, deleted[0]))
        return
    local = c.execute(
        "SELECT f.locally_modified_us FROM canonical.flight_legs f JOIN canonical.data_sources s ON s.id=f.source_id WHERE s.code=? AND f.source_record_key=?",
        (provider, key),
    ).fetchone()
    exists = c.execute(
        "SELECT 1 FROM flight_legs WHERE source_id=? AND source_record_key=?", (sid, key)
    ).fetchone()
    if exists:
        if local and local[0] is not None:
            c.execute(
                "UPDATE flight_legs SET locally_modified_us=? WHERE source_id=? AND source_record_key=?",
                (local[0], sid, key),
            )
        return
    row = c.execute(
        "SELECT * FROM canonical.flights WHERE provider=? AND provider_id=?", (provider, key)
    ).fetchone()
    if row is None:
        return
    # Raw extractor connections use tuple rows, unlike the shared store API.
    names = [d[0] for d in c.execute("SELECT * FROM canonical.flights LIMIT 0").description]
    record = flight_record(dict(zip(names, row)))
    outcome = store_flight(c, record, restore=False)
    if outcome == "protected":
        return
    c.execute(
        "UPDATE flight_legs SET locally_modified_us=? WHERE source_id=? AND source_record_key=?",
        (local[0], sid, key),
    )
    history = c.execute(
        "SELECT first_batch_id,first_seen_us,revision FROM ingestion_fact_history WHERE source_id=? AND source_record_key=?",
        (sid, key),
    ).fetchone()
    if history:
        c.execute(
            "UPDATE flight_legs SET first_batch_id=?,first_seen_us=?,revision=? WHERE source_id=? AND source_record_key=?",
            (*history, sid, key),
        )


def hydrate_window(path, start, end):
    """Stage a bounded canonical partition for the existing audit/VRS machinery."""
    from ingestion.publish import PARTITION_TIME_SQL
    from ingestion.references import copy_catalog, copy_source

    if selected(path) is None:
        return
    lower = utc_us(datetime.combine(start, time.min, UTC))
    upper = utc_us(datetime.combine(end + timedelta(days=1), time.min, UTC))
    with candidate_writer(path) as c:
        attach(c)
        c.execute("BEGIN IMMEDIATE")
        config = configuration(c)
        with SQLiteConnection(config["serving_path"], read_only=True).connect() as source:
            source.execute("BEGIN")
            for row in source.execute("SELECT * FROM data_sources"):
                copy_source(row, c)
            copy_catalog(source, c)
        keys = c.execute(
            f"SELECT s.code,f.source_record_key FROM canonical.flight_legs f JOIN canonical.data_sources s ON s.id=f.source_id JOIN canonical.publication_membership m ON m.source_code=s.code AND m.source_record_key=f.source_record_key WHERE m.store_id=? AND ({PARTITION_TIME_SQL})>=? AND ({PARTITION_TIME_SQL})<?",
            (config["store_id"], lower, upper),
        ).fetchall()
        for provider, key in keys:
            hydrate_key(c, provider, key)


def release_window(path, serving, start, end, receipts):
    """Drop only successfully handed-off copies; leave held/unpublished candidates.

    The serving write has already committed. Concurrent serving edits/deletions
    are authoritative. The private deletion and its quota check are one commit.
    No VACUUM, file replacement, receipt deletion or boundary pruning occurs.
    """
    from ingestion.daily import serving_receipts
    from ingestion.publish import PARTITION_TIME_SQL, admitted

    config = selected(path, serving)
    if config is None:
        return 0
    if (
        not receipts
        or serving_receipts(
            serving, config["store_id"], config["generation"], start, end, expected=receipts
        )
        != receipts
    ):
        raise ValueError("Verified serving receipts required before releasing private copies")
    lower = utc_us(datetime.combine(start, time.min, UTC))
    upper = utc_us(datetime.combine(end + timedelta(days=1), time.min, UTC))
    released = 0
    with candidate_writer(path) as c:
        attach(c)
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute(
            f"WITH selected AS (SELECT source_id,source_record_key FROM flight_legs WHERE ({PARTITION_TIME_SQL})>=? AND ({PARTITION_TIME_SQL})<?) SELECT f.* FROM selected l JOIN data_sources s ON s.id=l.source_id JOIN flights f ON f.provider=s.code AND f.provider_id=l.source_record_key",
            (lower, upper),
        ).fetchall()
        for row in rows:
            record = flight_record(row)
            if not admitted(record):
                continue
            key = (record.provider, record.provider_id)
            proof = c.execute(
                "SELECT 1 FROM canonical.publication_membership WHERE store_id=? AND source_code=? AND source_record_key=?",
                (config["store_id"], *key),
            ).fetchone()
            protected = c.execute(
                "SELECT 1 FROM canonical.flight_tombstones t JOIN canonical.data_sources s ON s.id=t.source_id WHERE s.code=? AND t.source_record_key=? UNION ALL SELECT 1 FROM canonical.flight_legs f JOIN canonical.data_sources s ON s.id=f.source_id WHERE s.code=? AND f.source_record_key=? AND f.locally_modified_us IS NOT NULL",
                (*key, *key),
            ).fetchone()
            canonical = c.execute(
                "SELECT * FROM canonical.flights WHERE provider=? AND provider_id=?", key
            ).fetchone()
            same = canonical is not None and flight_record(canonical) == record
            if protected or proof and same:
                c.execute(
                    "INSERT INTO ingestion_fact_history SELECT source_id,source_record_key,first_batch_id,first_seen_us,revision FROM flight_legs WHERE source_id=(SELECT id FROM data_sources WHERE code=?) AND source_record_key=? ON CONFLICT(source_id,source_record_key) DO UPDATE SET first_batch_id=COALESCE(ingestion_fact_history.first_batch_id,excluded.first_batch_id),first_seen_us=MIN(ingestion_fact_history.first_seen_us,excluded.first_seen_us),revision=excluded.revision",
                    key,
                )
                released += c.execute(
                    "DELETE FROM flight_legs WHERE source_id=(SELECT id FROM data_sources WHERE code=?) AND source_record_key=?",
                    key,
                ).rowcount
    return released


def fingerprint_rows(c, lower, upper):
    """Stable content identities across hydration/eviction; candidates overlay serving."""
    from ingestion.publish import PARTITION_TIME_SQL

    config = configuration(c)
    attach(c)
    # Include all owned canonical rows in the window except explicit private
    # replacements/deletions. Never use private row IDs as cross-store identity.
    rows = c.execute(
        f"""
        SELECT s.code provider,f.source_record_key record_key FROM flight_legs f
        JOIN data_sources s ON s.id=f.source_id
        WHERE ({PARTITION_TIME_SQL})>=? AND ({PARTITION_TIME_SQL})<?
        UNION
        SELECT s.code,f.source_record_key FROM canonical.flight_legs f
        JOIN canonical.data_sources s ON s.id=f.source_id
        JOIN canonical.publication_membership m ON m.source_code=s.code AND m.source_record_key=f.source_record_key
        WHERE m.store_id=? AND ({PARTITION_TIME_SQL})>=? AND ({PARTITION_TIME_SQL})<?
        AND NOT EXISTS(SELECT 1 FROM flight_legs p JOIN data_sources ps ON ps.id=p.source_id WHERE ps.code=s.code AND p.source_record_key=f.source_record_key)
        AND NOT EXISTS(SELECT 1 FROM flight_tombstones t JOIN data_sources ts ON ts.id=t.source_id WHERE ts.code=s.code AND t.source_record_key=f.source_record_key)
        ORDER BY 1,2
    """,
        (lower, upper, config["store_id"], lower, upper),
    )
    for provider, key in rows:
        row = c.execute(
            "SELECT * FROM flights WHERE provider=? AND provider_id=?", (provider, key)
        ).fetchone()
        schema = "" if row is not None else "canonical."
        if row is None:
            row = c.execute(
                "SELECT * FROM canonical.flights WHERE provider=? AND provider_id=?",
                (provider, key),
            ).fetchone()
        if row is None:
            continue
        metadata = c.execute(
            f"SELECT f.locally_modified_us,a.latitude,a.longitude,b.latitude,b.longitude FROM {schema}flight_legs f JOIN {schema}data_sources s ON s.id=f.source_id JOIN {schema}airports a ON a.iata=f.origin_iata JOIN {schema}airports b ON b.iata=f.destination_iata WHERE s.code=? AND f.source_record_key=?",
            (provider, key),
        ).fetchone()
        yield [flight_record(row).model_dump(mode="json"), tuple(metadata)]
