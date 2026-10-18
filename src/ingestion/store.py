"""Pipeline/tool API for the isolated ingestion SQLite store."""

import hashlib
import json
import os
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from threading import get_ident

from core.database_roles import ingestion_target, require_ingestion
from core.disk_quota import database_bytes, file_bytes, policy
from core.file_lock import file_mutex
from core.routing.models import utc_instant
from core.storage import SCHEMA, SQLiteConnection, source_id, upgrade_route_verification, utc_us
from ingestion.updater import FlightUpdater


def instant(value):
    return utc_instant(datetime.fromisoformat(value) if isinstance(value, str) else value)


STORE_SCHEMA = Path(__file__).with_name("schema.sql").read_text()
IDENTITY_SCHEMA = STORE_SCHEMA.split("CREATE TABLE IF NOT EXISTS ingestion_profiles", 1)[0]


# One producer owns the store throughout extraction -> audit -> publication.
# Nested pipeline stages reuse only this process/thread's actual OS lock; other
# processes, inherited fork contexts and manual clear/reset still contend on it.
_owned_jobs = ContextVar("ingestion_job_locks", default=frozenset())


@contextmanager
def job_lock(path, *, blocking=True):
    path = Path(path).resolve()
    key = (os.getpid(), get_ident(), str(path))
    owned = _owned_jobs.get()
    if key in owned:
        yield True
        return
    with file_mutex(Path(str(path) + ".job.lock"), blocking=blocking) as acquired:
        if not acquired:
            yield False
            return
        token = _owned_jobs.set(owned | {key})
        try:
            yield True
        finally:
            _owned_jobs.reset(token)


def check_quota(path, connection=None):
    state = policy(path)
    limit = state["quota_bytes"] if state else None
    if limit is None:
        return
    used = database_bytes(path)
    if connection is not None:
        projected = (
            connection.execute("PRAGMA page_count").fetchone()[0]
            * connection.execute("PRAGMA page_size").fetchone()[0]
        )
        used += max(0, projected - file_bytes(Path(path)))
    if used > limit:
        raise OSError("Database quota exceeded; durable records retained; raw workspace excluded")


class IngestionStore(FlightUpdater):
    """Only producer tools use this writer; admin uses read-only FlightReader."""

    def initialize(self):
        ingestion_target(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_mutex(Path(str(self.path) + ".producer.lock")):
            ingestion_target(self.path)
            with SQLiteConnection(self.path).connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(IDENTITY_SCHEMA + SCHEMA + STORE_SCHEMA)
                upgrade_route_verification(connection)
                require_ingestion(connection)

    @contextmanager
    def connect(self):
        with file_mutex(Path(str(self.path) + ".producer.lock")):
            with SQLiteConnection(self.path).connect() as connection:
                require_ingestion(connection)
                check_quota(self.path)
                yield connection
                check_quota(self.path, connection)

    def document(
        self,
        *,
        source,
        kind,
        key,
        payload,
        source_url,
        source_group=None,
        license_expression=None,
        fetched_at=None,
        valid_from=None,
        valid_to=None,
    ):
        """Idempotently store compact normalized provider evidence, not raw traces."""
        if kind not in {"route", "schedule", "airport", "aircraft", "validation"}:
            raise ValueError("Choose route, schedule, airport, aircraft or validation evidence")
        if not source or not key or not source_url:
            raise ValueError("Source, stable key and source URL are required")
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(data.encode()) > 65536:
            raise ValueError("Normalized evidence is limited to 64 KiB per record")
        first = utc_us(instant(valid_from)) if valid_from is not None else None
        last = utc_us(instant(valid_to)) if valid_to is not None else None
        if first is not None and last is not None and first >= last:
            raise ValueError("Evidence validity must be an increasing interval")
        with self.connect() as connection:
            sid = source_id(connection, source)
            if license_expression:
                connection.execute(
                    "UPDATE data_sources SET license_expression=? WHERE id=?",
                    (license_expression, sid),
                )
            connection.execute(
                """INSERT INTO ingestion_documents
                (source_id,kind,source_record_key,source_group,source_url,fetched_us,
                valid_from_us,valid_to_us,content_sha256,payload_json)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,kind,source_record_key)
                DO UPDATE SET source_group=excluded.source_group,source_url=excluded.source_url,
                fetched_us=excluded.fetched_us,valid_from_us=excluded.valid_from_us,
                valid_to_us=excluded.valid_to_us,content_sha256=excluded.content_sha256,
                payload_json=excluded.payload_json,
                revision=ingestion_documents.revision+(ingestion_documents.content_sha256<>excluded.content_sha256)""",
                (
                    sid,
                    kind,
                    key,
                    source_group or source,
                    source_url,
                    utc_us(instant(fetched_at) if fetched_at is not None else datetime.now(UTC)),
                    first,
                    last,
                    hashlib.sha256(data.encode()).hexdigest(),
                    data,
                ),
            )

    def clear(self, *, reset=False):
        """Explicit lifecycle exception; never unlink SQLite or a live WAL family."""
        with ExitStack() as locks:
            for suffix in (".job.lock", ".extraction.lock", ".producer.lock"):
                acquired = locks.enter_context(
                    file_mutex(Path(str(self.path) + suffix), blocking=False)
                )
                if not acquired:
                    raise ValueError("Stop the active ingestion job before clearing this database")
            with SQLiteConnection(self.path).connect() as connection:
                require_ingestion(connection)
                if (
                    connection.execute(
                        "SELECT 1 FROM sqlite_schema WHERE name='ingestion_fact_authority'"
                    ).fetchone()
                    and connection.execute("SELECT 1 FROM ingestion_fact_authority").fetchone()
                ):
                    raise ValueError(
                        "Serving owns verified facts; preserve its bound ingestion history"
                    )
                connection.execute("BEGIN IMMEDIATE")
                tables = {
                    r[0]
                    for r in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
                }
                for table in (
                    "ingestion_vrs_routes",
                    "ingestion_vrs_airports",
                    "ingestion_vrs_snapshot",
                    "ingestion_audit_changes",
                    "ingestion_audit_runs",
                    "ingestion_adoptions",
                    "flight_legs",
                    "flight_tombstones",
                    "ingestion_documents",
                    "ingestion_batches",
                    "airport_transfers",
                    "airports",
                    "carriers",
                    "reference_snapshots",
                ):
                    if table in tables:
                        connection.execute(f'DELETE FROM "{table}"')
                tables = {
                    r[0]
                    for r in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
                }
                for table in (
                    "extraction_days",
                    "pending_movements",
                    "extraction_config",
                    "extraction_boundaries",
                    "extraction_sources",
                ):
                    if table in tables:
                        connection.execute(f'DELETE FROM "{table}"')
                if reset:
                    connection.execute("DELETE FROM ingestion_profiles")
                    connection.execute("DELETE FROM data_sources")
                    connection.execute(
                        "UPDATE database_policy SET quota_bytes=21474836480 WHERE id=1"
                    )
                connection.execute(
                    "UPDATE ingestion_store_metadata SET generation=generation+1 WHERE id=1"
                )
        return {
            "database": str(self.path.resolve()),
            "cleared": True,
            "reset": reset,
            "serving_changed": False,
        }
