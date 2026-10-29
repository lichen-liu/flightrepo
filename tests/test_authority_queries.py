"""Canonical query changes preserve the original per-record fingerprint contract."""

import sqlite3
from datetime import timedelta

import pytest
from test_serving_authority import DAY, daily_run
from test_serving_authority import pipeline as authority_pipeline

from core.file_lock import file_mutex
from core.storage import TIME_FIELDS, SQLiteConnection, flight_record
from ingestion import authority, daily
from ingestion.publish import PARTITION_TIME_SQL, event_us
from ingestion.store import IngestionStore
from ingestion.updater import FlightUpdater
from server.reader import FlightReader

pipeline = authority_pipeline


def original_fingerprint_rows(c, lower, upper):
    config = authority.configuration(c)
    authority.attach(c)
    rows = c.execute(
        f"""SELECT s.code provider,f.source_record_key record_key FROM flight_legs f
        JOIN data_sources s ON s.id=f.source_id
        WHERE ({PARTITION_TIME_SQL})>=? AND ({PARTITION_TIME_SQL})<?
        UNION
        SELECT s.code,f.source_record_key FROM canonical.flight_legs f
        JOIN canonical.data_sources s ON s.id=f.source_id
        JOIN canonical.publication_membership m
            ON m.source_code=s.code AND m.source_record_key=f.source_record_key
        WHERE m.store_id=? AND ({PARTITION_TIME_SQL})>=? AND ({PARTITION_TIME_SQL})<?
        AND NOT EXISTS(SELECT 1 FROM flight_legs p JOIN data_sources ps ON ps.id=p.source_id
            WHERE ps.code=s.code AND p.source_record_key=f.source_record_key)
        AND NOT EXISTS(SELECT 1 FROM flight_tombstones t JOIN data_sources ts ON ts.id=t.source_id
            WHERE ts.code=s.code AND t.source_record_key=f.source_record_key)
        ORDER BY 1,2""",
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
            f"""SELECT f.locally_modified_us,a.latitude,a.longitude,b.latitude,b.longitude
            FROM {schema}flight_legs f JOIN {schema}data_sources s ON s.id=f.source_id
            JOIN {schema}airports a ON a.iata=f.origin_iata
            JOIN {schema}airports b ON b.iata=f.destination_iata
            WHERE s.code=? AND f.source_record_key=?""",
            (provider, key),
        ).fetchone()
        yield [flight_record(row).model_dump(mode="json"), tuple(metadata)]


def compare_fingerprints(private, day, monkeypatch):
    actual = daily.fingerprint(private, DAY, day, day, None)
    with monkeypatch.context() as patch:
        patch.setattr(authority, "fingerprint_rows", original_fingerprint_rows)
        expected = daily.fingerprint(private, DAY, day, day, None)
    assert actual == expected
    return actual


def test_batched_fingerprints_match_original_across_batches_and_overlays(pipeline, monkeypatch):
    private, serving, _ = pipeline
    daily_run(pipeline)
    record = FlightReader(serving).all_records()[0]
    copies = [record.model_copy(update={"provider_id": f"copy-{i:04}"}) for i in range(530)]
    FlightUpdater(serving).upsert(copies)
    with sqlite3.connect(serving) as c:
        store = c.execute("SELECT store_id FROM publication_origin").fetchone()[0]
        c.executemany(
            "INSERT INTO publication_membership VALUES(?,?,?,?)",
            [(store, r.provider, r.provider_id, event_us(r)) for r in copies],
        )
    # Private values override serving values and may have different reference coordinates.
    IngestionStore(private).upsert(
        [r.model_copy(update={"aircraft_type_code": "B738"}) for r in copies[:270]]
    )
    with sqlite3.connect(private) as c:
        c.execute("UPDATE airports SET latitude=latitude+0.001 WHERE iata=?", (record.origin_iata,))
        c.execute(
            "INSERT INTO flight_tombstones SELECT id,?,1 FROM data_sources WHERE code=?",
            (copies[1].provider_id, record.provider),
        )
        c.execute(
            "INSERT INTO flight_tombstones SELECT id,?,1 FROM data_sources WHERE code=?",
            (copies[400].provider_id, record.provider),
        )
    with sqlite3.connect(serving) as c:
        c.execute(
            "INSERT INTO flight_tombstones SELECT id,?,1 FROM data_sources WHERE code=?",
            (copies[401].provider_id, record.provider),
        )
    compare_fingerprints(private, DAY, monkeypatch)
    statements = []
    with SQLiteConnection(private, read_only=True).connect() as c:
        c.execute("BEGIN")
        c.set_trace_callback(statements.append)
        values = list(
            authority.fingerprint_rows(c, event_us(record), event_us(record) + 86400_000000)
        )
        query = next(s for s in statements if "UNION" in s and "record_key FROM flight_legs" in s)
        plan = c.execute("EXPLAIN QUERY PLAN " + query).fetchall()
    assert len(values) > 500
    assert any("SEARCH f USING INDEX publication_fact_time" in row[3] for row in plan)
    reads = [s for s in statements if "WITH requested(provider,record_key)" in s]
    assert len(reads) <= 6  # Bounded batch reads, rather than several queries for every flight.


def test_current_dates_win_over_stale_membership_after_manual_move(pipeline, monkeypatch):
    private, serving, _ = pipeline
    daily_run(pipeline)
    original = FlightReader(serving).all_records()[0]
    changes = {
        name: getattr(original, name) + timedelta(days=2)
        for name in TIME_FIELDS
        if getattr(original, name) is not None
    }
    moved = original.model_copy(update=changes)
    FlightUpdater(serving).update(original, moved)
    with sqlite3.connect(serving) as c:
        assert c.execute("SELECT departure_us FROM publication_membership").fetchone()[
            0
        ] == event_us(original)
    compare_fingerprints(private, DAY, monkeypatch)
    compare_fingerprints(private, DAY + timedelta(days=2), monkeypatch)
    authority.hydrate_window(private, DAY + timedelta(days=2), DAY + timedelta(days=2))
    assert FlightReader(private).all_records()[0] == moved
    compare_fingerprints(private, DAY + timedelta(days=2), monkeypatch)


@pytest.mark.parametrize("basis", ["observed", "scheduled", "actual", "arrival_only", "cancelled"])
def test_fingerprint_partition_precedence_matches_original(pipeline, monkeypatch, basis):
    private, serving, _ = pipeline
    daily_run(pipeline)
    record = FlightReader(serving).all_records()[0]
    changes = {"provider_id": "mixed-basis"}
    if basis == "scheduled":
        changes.update(
            departure_scheduled=record.departure_observed_lower + timedelta(days=1),
            arrival_scheduled=record.arrival_observed_upper + timedelta(days=1),
        )
    if basis in {"actual", "arrival_only"}:
        changes.update({name: None for name in TIME_FIELDS if "observed" in name})
        changes.update(
            arrival_actual=record.arrival_observed_upper + timedelta(days=1),
            evidence_kind="source_reported",
        )
        if basis == "actual":
            changes.update(departure_actual=record.departure_observed_lower + timedelta(days=1))
    if basis == "cancelled":
        changes.update(status=type(record.status)("cancelled"))
    FlightUpdater(serving).upsert([record.model_copy(update=changes)])
    with sqlite3.connect(serving) as c:
        store = c.execute("SELECT store_id FROM publication_origin").fetchone()[0]
        c.execute(
            "INSERT INTO publication_membership VALUES(?,?,?,?)",
            (store, record.provider, "mixed-basis", event_us(record)),
        )
    for day in [DAY, DAY + timedelta(days=1)]:
        compare_fingerprints(private, day, monkeypatch)


def test_existing_store_index_migration_is_atomic_and_preserves_facts(pipeline, monkeypatch):
    private, serving, _ = pipeline
    daily_run(pipeline)
    with sqlite3.connect(serving) as c:
        c.execute("DROP INDEX publication_fact_time")
        before = c.execute("SELECT * FROM flight_legs").fetchall()
        receipts = c.execute("SELECT * FROM publication_runs").fetchall()
    original = authority.check_quota

    def no_space(path, connection=None):
        original(path, connection)
        if (
            connection is not None
            and connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE name='publication_fact_time'"
            ).fetchone()
        ):
            raise OSError("index quota")

    with monkeypatch.context() as patch:
        patch.setattr(authority, "check_quota", no_space)
        with pytest.raises(OSError, match="index quota"):
            authority.ensure_window_index(private)
    with sqlite3.connect(serving) as c:
        assert not c.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='publication_fact_time'"
        ).fetchone()
        assert c.execute("SELECT * FROM flight_legs").fetchall() == before
        assert c.execute("SELECT * FROM publication_runs").fetchall() == receipts
    authority.ensure_window_index(private)
    with sqlite3.connect(serving) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert c.execute("SELECT * FROM flight_legs").fetchall() == before
        assert c.execute("SELECT * FROM publication_runs").fetchall() == receipts
    assert daily_run(pipeline)[0]["event"] == "daily_already_published"


def test_window_index_requires_real_job_lease(pipeline):
    private, serving, _ = pipeline
    daily_run(pipeline)
    with sqlite3.connect(serving) as c:
        c.execute("DROP INDEX publication_fact_time")
    with file_mutex(private.with_name(private.name + ".job.lock")):
        with pytest.raises(ValueError, match="active ingestion"):
            authority.ensure_window_index(private)
