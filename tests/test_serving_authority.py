"""Canonical serving facts survive bounded staging, backfills and interrupted handoffs."""

import json
import sqlite3
from collections import Counter
from datetime import date, timedelta

import pytest
from test_adsblol_extraction import midnight_traces, run
from test_adsblol_legs import leg_trace
from test_vrs import AIRPORTS, ROUTES

from core.storage import SQLiteConnection
from ingestion import authority, daily, references, vrs
from ingestion.adsblol.extraction import save_candidate
from ingestion.publish import publish_day
from ingestion.store import IngestionStore, job_lock
from ingestion.updater import FlightUpdater
from server.reader import FlightReader

DAY = date(2025, 1, 1)


@pytest.fixture
def pipeline(tmp_path):
    private, serving = tmp_path / "candidates.db", tmp_path / "serving.db"
    store = IngestionStore(private)
    store.initialize()
    vrs.import_snapshot(store, ROUTES, AIRPORTS)
    references.import_catalog(private, "carriers", b"Code,ICAO,IATA,Name\nONE,ONE,ON,One airline\n")

    def ingest(day, year):
        trace = leg_trace()
        trace["timestamp"] += (day - DAY).days * 86400
        trace["trace"][0][8] = {"flight": "ONE1"}
        run(tmp_path / "work", day, [trace], output_db=private, departure_year=year)

    return private, serving, ingest


def daily_run(pipeline, *, end=DAY, **kwargs):
    private, serving, ingest = pipeline
    return list(
        daily.daily_range(
            private, serving, DAY, end, ingest_day=ingest, serving_authoritative=True, **kwargs
        )
    )


def rows(path, table):
    with sqlite3.connect(path) as c:
        return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_daily_handoff_releases_only_facts_and_restart_reuses_proofs(pipeline):
    private, serving, _ = pipeline
    results = daily_run(pipeline, end=DAY + timedelta(days=2))
    assert all(r["released_private_copies"] for r in results)
    assert rows(private, "flight_legs") == 0
    assert rows(private, "ingestion_fact_history") == 3
    assert rows(private, "extraction_days") == 3
    assert rows(private, "extraction_boundaries") > 0
    assert rows(serving, "flight_legs") == 3
    with sqlite3.connect(private) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM ingestion_fact_history WHERE first_batch_id IS NOT NULL"
            ).fetchone()[0]
            == 3
        )
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    repeated = daily_run(pipeline, end=DAY + timedelta(days=2))
    assert repeated[-1]["event"] == "daily_already_published"
    assert rows(private, "flight_legs") == 0
    assert FlightReader(serving, serving_only=True).stats()["flights"] == 3
    assert publish_day(private, serving, DAY, audit_routes=False)["retired"] == 0
    assert rows(serving, "flight_legs") == 3


@pytest.mark.parametrize("failure", ["before_release", "after_release"])
def test_crash_between_serving_commit_cleanup_and_checkpoint_is_recoverable(
    pipeline, monkeypatch, failure
):
    private, serving, _ = pipeline
    release, document = authority.release_window, IngestionStore.document

    def broken_release(*args, **kwargs):
        raise OSError("interrupted handoff")

    def broken_document(self, **kwargs):
        if kwargs["key"].startswith(daily.VERSION):
            raise OSError("interrupted checkpoint")
        return document(self, **kwargs)

    if failure == "before_release":
        monkeypatch.setattr(authority, "release_window", broken_release)
    else:
        monkeypatch.setattr(IngestionStore, "document", broken_document)
    with pytest.raises(OSError, match="interrupted"):
        daily_run(pipeline)
    assert rows(serving, "flight_legs") == 1
    assert rows(private, "flight_legs") == int(failure == "before_release")
    monkeypatch.setattr(authority, "release_window", release)
    monkeypatch.setattr(IngestionStore, "document", document)
    assert daily_run(pipeline)[0]["event"] == "daily_published"
    assert rows(private, "flight_legs") == 0
    assert rows(serving, "flight_legs") == 1


def test_hydration_keeps_provenance_and_fingerprint_stable(pipeline):
    private, serving, _ = pipeline
    daily_run(pipeline)
    before = daily.fingerprint(private, DAY, DAY, DAY, None)
    authority.hydrate_window(private, DAY, DAY)
    assert daily.fingerprint(private, DAY, DAY, DAY, None) == before
    with sqlite3.connect(private) as c:
        assert (
            c.execute("SELECT first_batch_id FROM flight_legs").fetchone()[0]
            == c.execute("SELECT first_batch_id FROM ingestion_fact_history").fetchone()[0]
        )
    receipts = daily.serving_receipts(serving, *before[0], DAY, DAY)
    assert authority.release_window(private, serving, DAY, DAY, receipts) == 1
    assert daily.fingerprint(private, DAY, DAY, DAY, None) == before
    with pytest.raises(ValueError, match="receipts"):
        authority.release_window(private, serving, DAY, DAY, [])


def test_backfill_uses_canonical_quality_instead_of_accepting_weaker_reconstruction(pipeline):
    private, _, _ = pipeline
    daily_run(pipeline)
    authority.hydrate_window(private, DAY, DAY)
    with sqlite3.connect(private) as c:
        key, raw = c.execute("SELECT key,data FROM itinerary_legs").fetchone()
        c.execute("DELETE FROM flight_legs")
    data = json.loads(raw)
    data["evidence"] = "airport_proximity_and_time_window"
    counts = Counter()
    with SQLiteConnection(private).connect() as c:
        authority.attach(c)
        assert not save_candidate(c, key, data, counts)
    assert counts["records_already_present"] == 1
    assert rows(private, "flight_legs") == 1


def test_serving_manual_changes_and_deletions_win_after_copies_are_gone(pipeline):
    private, serving, _ = pipeline
    daily_run(pipeline)
    record = FlightReader(serving).all_records()[0]
    corrected = record.model_copy(update={"aircraft_type_code": "B738"})
    FlightUpdater(serving).update(record, corrected)
    assert daily_run(pipeline)[0]["event"] == "daily_published"
    assert FlightReader(serving).all_records()[0].aircraft_type_code == "B738"
    assert rows(private, "flight_legs") == 0
    FlightUpdater(serving).delete(record.provider, record.provider_id)
    daily_run(pipeline)
    assert rows(serving, "flight_legs") == 0
    assert rows(serving, "flight_tombstones") == 1
    assert rows(private, "flight_legs") == 0


def test_explicit_private_tombstone_retracts_but_absence_alone_does_not(pipeline):
    private, serving, _ = pipeline
    daily_run(pipeline)
    assert publish_day(private, serving, DAY, audit_routes=False)["retired"] == 0
    record = FlightReader(serving).all_records()[0]
    with IngestionStore(private).connect() as c:
        c.execute(
            "INSERT INTO flight_tombstones SELECT id,?,1 FROM data_sources WHERE code=?",
            (record.provider_id, record.provider),
        )
    assert publish_day(private, serving, DAY, audit_routes=False)["retired"] == 1
    assert rows(serving, "flight_legs") == 0


@pytest.mark.parametrize("failure", ["missing", "replaced", "wrong_target", "generation"])
def test_missing_or_wrong_authority_stops_before_ingest_and_never_recreates_it(
    pipeline, tmp_path, failure
):
    private, serving, _ = pipeline
    daily_run(pipeline)
    if failure == "missing":
        serving.rename(tmp_path / "saved.db")
    elif failure == "replaced":
        with sqlite3.connect(serving) as c:
            c.execute("UPDATE serving_fact_authority SET identity='different'")
    elif failure == "generation":
        with sqlite3.connect(private) as c:
            c.execute("UPDATE ingestion_store_metadata SET generation=generation+1")
    else:
        serving = tmp_path / "wrong.db"
    seen = []
    with pytest.raises((ValueError, sqlite3.OperationalError)):
        list(daily.daily_range(private, serving, DAY, DAY, ingest_day=lambda *a: seen.append(a)))
    assert not seen
    if failure in {"missing", "wrong_target"}:
        assert not serving.exists()


def test_bound_pipeline_history_cannot_be_cleared(pipeline):
    private, _, _ = pipeline
    daily_run(pipeline)
    with pytest.raises(ValueError, match="preserve"):
        IngestionStore(private).clear(reset=True)
    assert rows(private, "extraction_days") == 1


def test_existing_private_history_migrates_without_changing_facts_or_receipts(pipeline):
    private, serving, ingest = pipeline
    list(daily.daily_range(private, serving, DAY, DAY, ingest_day=ingest))
    original = FlightReader(serving).all_records()
    with sqlite3.connect(private) as c:
        receipt = c.execute("SELECT receipt FROM extraction_days").fetchone()[0]
    with job_lock(private):
        authority.bind(private, serving)
    assert rows(private, "flight_legs") == 1
    assert FlightReader(serving).all_records() == original
    assert daily_run(pipeline)[0]["released_private_copies"] == 1
    assert FlightReader(serving).all_records() == original
    with sqlite3.connect(private) as c:
        assert c.execute("SELECT version FROM ingestion_store_metadata").fetchone()[0] == 2
        assert c.execute("SELECT receipt FROM extraction_days").fetchone()[0] == receipt
    for path in [private, serving]:
        with sqlite3.connect(path) as c:
            assert (
                c.execute("SELECT quota_bytes FROM database_policy").fetchone()[0] == 20 * 1024**3
            )


def test_stale_serving_receipt_cannot_release_a_changed_candidate(pipeline):
    private, serving, _ = pipeline
    daily_run(pipeline)
    authority.hydrate_window(private, DAY, DAY)
    identity, _ = daily.fingerprint(private, DAY, DAY, DAY, None)
    proofs = daily.serving_receipts(serving, *identity, DAY, DAY)
    with sqlite3.connect(private) as c:
        c.execute("UPDATE flight_legs SET aircraft_type_code='B738'")
    assert authority.release_window(private, serving, DAY, DAY, proofs) == 0
    assert rows(private, "flight_legs") == 1
    assert FlightReader(serving).all_records()[0].aircraft_type_code == "A320"
    daily_run(pipeline)
    assert FlightReader(serving).all_records()[0].aircraft_type_code == "B738"
    assert rows(private, "flight_legs") == 0


def test_failed_publication_retains_candidates_and_input_checkpoint(pipeline, monkeypatch):
    private, serving, _ = pipeline
    original = daily.publish_range

    def unavailable(*args, **kwargs):
        raise OSError("serving quota")

    monkeypatch.setattr(daily, "publish_range", unavailable)
    with pytest.raises(OSError, match="quota"):
        daily_run(pipeline)
    assert rows(private, "flight_legs") == rows(private, "extraction_days") == 1
    assert rows(serving, "flight_legs") == 0
    monkeypatch.setattr(daily, "publish_range", original)
    daily_run(pipeline)
    assert rows(private, "flight_legs") == 0
    assert rows(serving, "flight_legs") == 1


def test_changed_reference_reverifies_canonical_records(pipeline):
    private, serving, _ = pipeline
    daily_run(pipeline)
    original = FlightReader(serving).all_records()[0].route_verification
    vrs.import_snapshot(IngestionStore(private), ROUTES.replace(b"ONE1", b"OTHER1"), AIRPORTS)
    result = daily_run(pipeline)[0]
    assert result["event"] == "daily_published"
    changed = FlightReader(serving).all_records()[0].route_verification
    assert changed != original
    assert changed.match_kind == "missing"
    assert rows(private, "flight_legs") == 0


def test_overnight_join_is_published_after_the_arrival_input(pipeline, tmp_path):
    private, serving, _ = pipeline
    first, second = midnight_traces()

    def ingest(day, year):
        if day > DAY:
            assert rows(serving, "flight_legs") == 0
        run(tmp_path / "overnight", day, [first if day == DAY else second], output_db=private)

    result = list(
        daily.daily_range(
            private,
            serving,
            DAY,
            DAY + timedelta(days=1),
            ingest_day=ingest,
            serving_authoritative=True,
        )
    )
    assert result[-1]["publication"][0]["inserted"] == 1
    assert rows(serving, "flight_legs") == 1
    assert rows(private, "flight_legs") == 0
    assert rows(private, "extraction_boundaries") == 2


def test_readthrough_is_read_only_and_extraction_cannot_change_serving(pipeline):
    private, serving, ingest = pipeline
    daily_run(pipeline)
    before = FlightReader(serving).all_records()
    ingest(DAY + timedelta(days=1), None)
    assert FlightReader(serving).all_records() == before
    with SQLiteConnection(private).connect() as c:
        authority.attach(c)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            c.execute("DELETE FROM canonical.flight_legs")


def test_hydration_and_release_respect_active_job_lease(pipeline):
    from core.file_lock import file_mutex

    private, serving, _ = pipeline
    daily_run(pipeline)
    identity, _ = daily.fingerprint(private, DAY, DAY, DAY, None)
    proofs = daily.serving_receipts(serving, *identity, DAY, DAY)
    with file_mutex(private.with_name(private.name + ".job.lock")):
        with pytest.raises(ValueError, match="active ingestion"):
            authority.hydrate_window(private, DAY, DAY)
        with pytest.raises(ValueError, match="active ingestion"):
            authority.release_window(private, serving, DAY, DAY, proofs)
