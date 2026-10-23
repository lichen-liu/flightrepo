"""Seed a fresh serving replacement from canonical administrative state only."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from core.database_roles import require_serving
from core.storage import SQLiteConnection, flight_record, source_id, store_flight
from ingestion.store import check_quota
from ingestion.updater import FlightUpdater
from ingestion.validation import require_semantic_health

CONTROL_SCHEMA = """CREATE TABLE serving_control_snapshot (
    id INTEGER PRIMARY KEY CHECK(id=1),
    source_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    counts_json TEXT NOT NULL CHECK(json_valid(counts_json))
) STRICT;"""


def controls(connection):
    require_serving(connection)
    contract = connection.execute(
        "SELECT contract,version FROM schema_metadata WHERE id=1"
    ).fetchone()
    if tuple(contract or ()) != ("flight-legs", 1):
        raise ValueError("Unsupported canonical flight contract")
    # Exclude pipeline profiles and old automatically ingested facts. Include
    # explicit edits, human decisions and manually created routes.
    tables = {}
    for name, order in [
        ("database_policy", "id"),
        ("data_sources", "id"),
        ("airports", "iata"),
        ("carriers", "code"),
        ("reference_snapshots", "source_id,kind"),
        ("airport_timezone_snapshots", "id"),
        ("airport_timezone_state", "iata"),
        ("airport_transfers", "arrival_iata,departure_iata"),
        ("flight_tombstones", "source_id,source_record_key"),
    ]:
        if (
            name
            in {
                "carriers",
                "reference_snapshots",
                "airport_timezone_snapshots",
                "airport_timezone_state",
            }
            and not connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE name=?", (name,)
            ).fetchone()
        ):
            tables[name] = []
            continue
        tables[name] = [
            dict(row) for row in connection.execute(f"SELECT * FROM {name} ORDER BY {order}")
        ]
    tables["manual_flights"] = [
        dict(row)
        for row in connection.execute("""SELECT f.* FROM flight_legs f JOIN data_sources s ON s.id=f.source_id
        WHERE f.locally_modified_us IS NOT NULL OR f.itinerary_reviewed=1 OR s.code='manual' ORDER BY f.id""")
    ]
    tables["manual_codeshares"] = [
        dict(row)
        for row in connection.execute("""SELECT c.* FROM flight_codeshares c JOIN flight_legs f ON f.id=c.leg_id JOIN data_sources s ON s.id=f.source_id
        WHERE f.locally_modified_us IS NOT NULL OR f.itinerary_reviewed=1 OR s.code='manual' ORDER BY c.leg_id,c.flight_number""")
    ]
    fingerprint = hashlib.sha256(
        json.dumps(tables, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return tables, fingerprint


def prepare_serving(canonical, destination):
    """Fresh target only; a changed canonical snapshot requires a fresh rebuild.

    This keeps concurrent administrative changes detectable without guessing
    which edit to discard. Revalidate immediately before cutover, with writers stopped.
    """
    canonical, destination = (Path(p).expanduser().resolve() for p in (canonical, destination))
    if canonical == destination or (destination.exists() and canonical.samefile(destination)):
        raise ValueError("Serving controls require a different new replacement")
    if destination.exists():
        return verify_serving_controls(canonical, destination)
    if any(Path(str(destination) + s).exists() for s in ("-wal", "-shm", "-journal")):
        raise ValueError("Refusing existing replacement SQLite sidecars")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteConnection(canonical, read_only=True).connect() as source:
        source.execute("BEGIN")
        snapshot, digest = controls(source)
        with TemporaryDirectory(prefix=".serving-controls-", dir=destination.parent) as directory:
            temporary = Path(directory) / "serving.db"
            writer = FlightUpdater(temporary)
            writer.initialize()
            with writer.connect() as output:
                mapping = {
                    row["id"]: source_id(output, row["code"]) for row in snapshot["data_sources"]
                }
                for row in snapshot["data_sources"]:
                    output.execute(
                        "UPDATE data_sources SET name=?,source_url=?,license_expression=?,license_url=?,attribution=? WHERE id=?",
                        (
                            row["name"],
                            row["source_url"],
                            row["license_expression"],
                            row["license_url"],
                            row["attribution"],
                            mapping[row["id"]],
                        ),
                    )
                from ingestion.references import AIRPORT_FIELDS, CARRIER_FIELDS

                for table, fields in [("airports", AIRPORT_FIELDS), ("carriers", CARRIER_FIELDS)]:
                    for row in snapshot[table]:
                        data = {key: row.get(key) for key in fields}
                        data["metadata_source_id"] = mapping.get(row["metadata_source_id"])
                        output.execute(
                            f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join(':' + key for key in fields)})",
                            data,
                        )
                for row in snapshot["reference_snapshots"]:
                    output.execute(
                        "INSERT INTO reference_snapshots VALUES(?,?,?,?,?,?)",
                        (
                            mapping[row["source_id"]],
                            row["kind"],
                            row["source_url"],
                            row["sha256"],
                            row["fetched_us"],
                            row["row_count"],
                        ),
                    )
                for table in ("airport_timezone_snapshots", "airport_timezone_state"):
                    for row in snapshot[table]:
                        fields = list(row)
                        output.execute(
                            f"INSERT INTO {table}({','.join(fields)}) "
                            f"VALUES({','.join(':' + key for key in fields)})",
                            row,
                        )
                for row in snapshot["airport_transfers"]:
                    output.execute(
                        "INSERT INTO airport_transfers VALUES(:arrival_iata,:departure_iata,:minimum_minutes,:source)",
                        row,
                    )
                for row in snapshot["manual_flights"]:
                    # The compatibility flight view decodes all time semantics.
                    raw = source.execute(
                        "SELECT * FROM flights WHERE provider=(SELECT code FROM data_sources WHERE id=?) AND provider_id=?",
                        (row["source_id"], row["source_record_key"]),
                    ).fetchone()
                    if raw is not None:
                        store_flight(output, flight_record(raw), local_edit=True)
                for row in snapshot["flight_tombstones"]:
                    sid = mapping[row["source_id"]]
                    output.execute(
                        "INSERT INTO flight_tombstones VALUES(?,?,?)",
                        (sid, row["source_record_key"], row["deleted_us"]),
                    )
                quota = snapshot["database_policy"][0]["quota_bytes"]
                output.execute("UPDATE database_policy SET quota_bytes=? WHERE id=1", (quota,))
                output.executescript(CONTROL_SCHEMA)
                counts = {key: len(value) for key, value in snapshot.items()}
                output.execute(
                    "INSERT INTO serving_control_snapshot VALUES(1,?,?,?)",
                    (str(canonical), digest, json.dumps(counts)),
                )
            check_quota(temporary)
            with writer.connect() as output:
                if [r[0] for r in output.execute("PRAGMA integrity_check")] != [
                    "ok"
                ] or output.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Serving controls failed integrity/foreign-key checks")
                require_semantic_health(output)
                if output.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                    raise ValueError("Serving copy checkpoint is busy")
                if output.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise ValueError("Serving copy could not leave WAL mode safely")
            os.link(temporary, destination)
    return dict(
        canonical=str(canonical),
        replacement=str(destination),
        control_sha256=digest,
        counts=counts,
        source_changed=False,
    )


def verify_serving_controls(canonical, destination):
    canonical, destination = (Path(p).expanduser().resolve() for p in (canonical, destination))
    with SQLiteConnection(canonical, read_only=True).connect() as source:
        source.execute("BEGIN")
        _, digest = controls(source)
    with SQLiteConnection(destination, read_only=True).connect() as target:
        require_serving(target)
        if not target.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='serving_control_snapshot'"
        ).fetchone():
            raise ValueError(
                "Replacement has no canonical-control snapshot; use a new serving file"
            )
        row = target.execute(
            "SELECT source_path,sha256,counts_json FROM serving_control_snapshot WHERE id=1"
        ).fetchone()
        if row is None or row["source_path"] != str(canonical) or row["sha256"] != digest:
            raise ValueError(
                "Canonical administrative state changed; build a new serving replacement before cutover"
            )
    return dict(
        canonical=str(canonical),
        replacement=str(destination),
        control_sha256=digest,
        counts=json.loads(row["counts_json"]),
        unchanged=True,
    )
