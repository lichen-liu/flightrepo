"""Adopt a finished v5 archive into a new private store without touching its source."""

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import ExitStack, closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from core.database_roles import is_ingestion, require_ingestion
from core.disk_quota import policy
from core.file_lock import file_mutex
from core.storage import SQLiteConnection, utc_us
from ingestion.store import IDENTITY_SCHEMA, IngestionStore, check_quota
from ingestion.validation import require_semantic_health

ARCHIVE_TABLES = {
    "schema_metadata",
    "database_policy",
    "data_sources",
    "airports",
    "airport_transfers",
    "ingestion_batches",
    "flight_legs",
    "flight_codeshares",
    "flight_tombstones",
    "extraction_days",
    "pending_movements",
    "extraction_config",
    "extraction_boundaries",
    "extraction_sources",
}


def table_digests(connection):
    """Hash every original row, including local overrides and extraction state."""
    result = {}
    tables = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for (name,) in tables:
        quoted = '"' + name.replace('"', '""') + '"'
        columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
        keys = [item[1] for item in sorted(columns, key=lambda item: item[5]) if item[5]]
        order = ",".join('"' + key.replace('"', '""') + '"' for key in keys) or "rowid"
        digest, count = hashlib.sha256(), 0
        for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY {order}"):
            digest.update(
                json.dumps(tuple(row), separators=(",", ":"), ensure_ascii=False).encode()
            )
            digest.update(b"\n")
            count += 1
        result[name] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def completed_archive(connection, start, end):
    if is_ingestion(connection):
        raise ValueError("Adoption expects an unmarked legacy archive, not another ingestion store")
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    }
    if not ARCHIVE_TABLES <= tables or tables - (ARCHIVE_TABLES | {"ingestion_profiles"}):
        raise ValueError("Select a completed v5 extraction archive, never a serving database")
    contract = connection.execute(
        "SELECT contract,version FROM schema_metadata WHERE id=1"
    ).fetchone()
    if tuple(contract or ()) != ("flight-legs", 1):
        raise ValueError("Unsupported source flight contract")
    if connection.execute(
        "SELECT 1 FROM ingestion_batches WHERE state='running' LIMIT 1"
    ).fetchone():
        raise ValueError(
            "Source has an active/interrupted running batch; finish or resume it first"
        )
    days = {
        row[0]: json.loads(row[1])
        for row in connection.execute("SELECT day,receipt FROM extraction_days")
    }
    expected = [str(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    missing = [day for day in expected if day not in days]
    if missing:
        raise ValueError("Source input days are not complete: " + ", ".join(missing[:10]))
    for day in expected:
        receipt = days[day]
        batch = connection.execute(
            "SELECT b.state,b.pipeline_version,b.input_start_us,b.input_end_us,s.code FROM ingestion_batches b JOIN data_sources s ON s.id=b.source_id WHERE b.id=?",
            (receipt.get("batch_id"),),
        ).fetchone()
        instant = utc_us(datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC))
        if (
            receipt.get("boundary_version") != 1
            or receipt.get("day") != day
            or batch is None
            or tuple(batch)
            != ("committed", "observed-itinerary-v5", instant, instant + 86400_000000, "adsblol")
        ):
            raise ValueError("Missing or incompatible committed v5 receipt for " + day)
    return expected


def adopt_archive(
    source_db, destination, start, end, *, source_root, policy_db=None, progress=None
):
    """Publish a NEW ingestion file only after a consistent backup and exact row checks.

    The required interval is INPUT days, including the arrival-context day.
    All original tables are fingerprinted before adding private store metadata.
    """
    source_db, destination, source_root = (
        Path(p).expanduser().resolve() for p in (source_db, destination, source_root)
    )
    start = date.fromisoformat(start) if isinstance(start, str) else start
    end = date.fromisoformat(end) if isinstance(end, str) else end
    if end < start:
        raise ValueError("Adoption end must follow start")
    if source_db == destination or (destination.exists() and source_db.samefile(destination)):
        raise ValueError("Adoption requires a different, new destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    locks = [
        Path(str(source_db) + suffix)
        for suffix in (".job.lock", ".extraction.lock", ".producer.lock")
    ]
    locks += [source_root / ".extract.lock"]
    locks += [
        Path(str(destination) + suffix)
        for suffix in (".job.lock", ".extraction.lock", ".producer.lock")
    ]
    with ExitStack() as stack:
        for lock in sorted(set(locks)):
            if not stack.enter_context(file_mutex(lock, blocking=False)):
                raise ValueError(
                    "Source or destination has an active producer/extractor; retry after it finishes"
                )
        if any(
            Path(str(destination) + suffix).exists() for suffix in ("", "-wal", "-shm", "-journal")
        ):
            raise ValueError("Adoption never overwrites an existing database or SQLite sidecars")
        with SQLiteConnection(source_db, read_only=True).connect() as source:
            source.execute("BEGIN")
            expected = completed_archive(source, start, end)
            integrity = [row[0] for row in source.execute("PRAGMA integrity_check")]
            if integrity != ["ok"] or source.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Source integrity/foreign-key validation failed")
            require_semantic_health(source)
            needed = (
                source.execute("PRAGMA page_count").fetchone()[0]
                * source.execute("PRAGMA page_size").fetchone()[0]
            )
            if shutil.disk_usage(destination.parent).free < needed + max(1024**3, needed // 5):
                raise OSError(
                    "Insufficient space for an independent adoption copy and index workspace"
                )
            if progress:
                progress({"phase": "fingerprinting_source", "input_days": len(expected)})
            before = table_digests(source)
            with TemporaryDirectory(prefix=".adopt-", dir=destination.parent) as directory:
                temporary = Path(directory) / "ingestion.db"
                with closing(sqlite3.connect(temporary)) as copy:
                    source.backup(copy, pages=2048)
                    if table_digests(copy) != before:
                        raise ValueError("Adoption backup changed source facts or resume state")
                    # Adoption alone may claim the verified, unpublished copy.
                    copy.executescript(IDENTITY_SCHEMA)
                # Only the private copy is initialized, never the source.
                IngestionStore(temporary).initialize()
                with SQLiteConnection(temporary).connect() as copy:
                    require_ingestion(copy)
                    if table_digests_original(copy, before) != before:
                        raise ValueError(
                            "Initialization changed original facts or extraction state"
                        )
                    live_policy = policy(policy_db or source_db)
                    if live_policy is None:
                        raise ValueError("A persisted source/live quota is required")
                    copy.execute(
                        "UPDATE database_policy SET quota_bytes=? WHERE id=1",
                        (live_policy["quota_bytes"],),
                    )
                    summary = dict(
                        source_database=str(source_db),
                        destination=str(destination),
                        input_start=str(start),
                        input_end=str(end),
                        input_days=len(expected),
                        tables=before,
                        quota_policy_database=str(Path(policy_db or source_db).resolve()),
                        quota_bytes=live_policy["quota_bytes"],
                    )
                    copy.execute(
                        "INSERT INTO ingestion_adoptions VALUES(1,?,?)",
                        (utc_us(datetime.now(UTC)), json.dumps(summary, separators=(",", ":"))),
                    )
                if progress:
                    progress({"phase": "validating_copy"})
                with SQLiteConnection(temporary).connect() as copy:
                    if [row[0] for row in copy.execute("PRAGMA integrity_check")] != [
                        "ok"
                    ] or copy.execute("PRAGMA foreign_key_check").fetchone():
                        raise ValueError("Adopted copy failed integrity/foreign-key validation")
                    require_semantic_health(copy)
                    if copy.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                        raise ValueError("Adoption copy checkpoint is busy")
                    if copy.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                        raise ValueError("Adoption copy could not leave WAL mode safely")
                check_quota(temporary)
                # Atomic no-clobber publication. New store will use WAL on its next initialization.
                os.link(temporary, destination)
                try:
                    handle = os.open(destination.parent, os.O_RDONLY)
                    try:
                        os.fsync(handle)
                    finally:
                        os.close(handle)
                except OSError:
                    # Directory fsync is not supported on every filesystem; the
                    # closed SQLite snapshot itself remains consistent.
                    pass
                return {
                    **summary,
                    "role": "ingestion",
                    "source_changed": False,
                    "serving_changed": False,
                }


def table_digests_original(connection, original):
    return {name: value for name, value in table_digests(connection).items() if name in original}
