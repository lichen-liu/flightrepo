"""Daily visibility, retry boundaries, overnight reconciliation and lock ownership."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from test_adsblol_extraction import run
from test_adsblol_legs import leg_trace
from test_vrs import AIRPORTS, ROUTES

from core.file_lock import file_mutex
from ingestion import cli, daily, references, vrs
from ingestion.store import IngestionStore, job_lock
from server.reader import FlightReader

DAY = date(2025, 1, 1)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    private, serving = tmp_path / "private.db", tmp_path / "serving.db"
    store = IngestionStore(private)
    store.initialize()
    vrs.import_snapshot(store, ROUTES, AIRPORTS)
    references.import_catalog(private, "carriers", b"Code,ICAO,IATA,Name\nONE,ONE,ON,One airline\n")

    def ingest(day, year):
        data = leg_trace()
        data["timestamp"] += (day - DAY).days * 86400
        data["trace"][0][8] = {"flight": "ONE1"}
        run(tmp_path / "work", day, [data], output_db=private, departure_year=year)

    return private, serving, ingest


def test_each_day_is_verified_and_visible_before_next_input_and_rerun_is_noop(setup):
    private, serving, ingest = setup
    seen = []

    def check(day, year):
        if day > DAY:
            assert FlightReader(serving).stats()["flights"] == 1
            with sqlite3.connect(private) as c:
                assert c.execute("SELECT route_verification FROM flight_legs").fetchone()[0]
        ingest(day, year)
        seen.append(day)

    results = list(
        daily.daily_range(private, serving, DAY, DAY + timedelta(days=1), ingest_day=check)
    )
    assert [r["event"] for r in results] == ["daily_published"] * 2
    assert all(r["audit"]["health"]["passed"] for r in results)
    assert FlightReader(serving).stats()["flights"] == 2
    with sqlite3.connect(serving) as c:
        runs = c.execute("SELECT count(*) FROM publication_runs").fetchone()[0]
        assert not c.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='ingestion_documents'"
        ).fetchone()
    repeated = list(
        daily.daily_range(private, serving, DAY, DAY + timedelta(days=1), ingest_day=ingest)
    )
    # The preceding input may be rechecked after the following day's overnight joins.
    assert repeated[-1]["event"] == "daily_already_published"
    with sqlite3.connect(serving) as c:
        assert c.execute("SELECT count(*) FROM publication_runs").fetchone()[0] <= runs + 1


def test_cached_day_publishes_timezone_refresh_without_flight_audit(setup, monkeypatch):
    from test_airport_timezones import Lookup

    from ingestion import airport_timezones

    private, serving, ingest = setup
    monkeypatch.setattr(airport_timezones, "resolver", lambda: Lookup())
    list(daily.daily_range(private, serving, DAY, DAY, ingest_day=ingest))
    with sqlite3.connect(serving) as c:
        before = c.execute("SELECT id,revision FROM flight_legs ORDER BY id").fetchall()
        runs = c.execute("SELECT COUNT(*) FROM publication_runs").fetchone()[0]
    monkeypatch.setattr(airport_timezones, "resolver", lambda: Lookup(["Asia/Tokyo"], 2))

    def unexpected(*a, **kw):
        raise AssertionError("A metadata refresh must not rerun the flight audit")

    monkeypatch.setattr(daily, "audit_database", unexpected)
    repeated = list(daily.daily_range(private, serving, DAY, DAY, ingest_day=ingest))
    assert repeated[0]["event"] == "daily_already_published"
    with sqlite3.connect(serving) as c:
        assert c.execute("SELECT id,revision FROM flight_legs ORDER BY id").fetchall() == before
        assert c.execute("SELECT COUNT(*) FROM publication_runs").fetchone()[0] == runs
        assert c.execute("SELECT DISTINCT timezone FROM airports").fetchall() == [("Asia/Tokyo",)]


def test_audit_failure_stops_publication_and_next_input_then_resumes(setup, monkeypatch):
    private, serving, ingest = setup
    original = daily.audit_database
    seen = []

    def broken(*args, **kwargs):
        raise ValueError("structural test failure")

    monkeypatch.setattr(daily, "audit_database", broken)

    def checked(day, year):
        seen.append(day)
        ingest(day, year)

    with pytest.raises(ValueError, match="structural"):
        list(daily.daily_range(private, serving, DAY, DAY + timedelta(days=1), ingest_day=checked))
    assert seen == [DAY] and not serving.exists()
    monkeypatch.setattr(daily, "audit_database", original)
    assert (
        list(daily.daily_range(private, serving, DAY, DAY, ingest_day=ingest))[0]["event"]
        == "daily_published"
    )
    assert FlightReader(serving).stats()["flights"] == 1


def test_vrs_or_publication_failure_preserves_committed_inputs_and_retries(setup, monkeypatch):
    private, serving, ingest = setup
    original = daily.publish_range

    def broken(*args, **kwargs):
        raise OSError("quota test")

    monkeypatch.setattr(daily, "publish_range", broken)
    with pytest.raises(OSError, match="quota"):
        list(daily.daily_range(private, serving, DAY, DAY, ingest_day=ingest))
    with sqlite3.connect(private) as c:
        assert c.execute("SELECT count(*) FROM extraction_days").fetchone()[0] == 1
        assert c.execute("SELECT route_verification FROM flight_legs").fetchone()[0]
    monkeypatch.setattr(daily, "publish_range", original)
    assert (
        list(daily.daily_range(private, serving, DAY, DAY, ingest_day=ingest))[0]["event"]
        == "daily_published"
    )


def test_nested_job_lease_keeps_real_lock_and_clear_blocked(setup):
    private, _, _ = setup
    with job_lock(private):
        with job_lock(private, blocking=False) as acquired:
            assert acquired
        with file_mutex(Path(str(private) + ".job.lock"), blocking=False) as external:
            assert not external
        with pytest.raises(ValueError, match="active ingestion"):
            IngestionStore(private).clear()
    with job_lock(private, blocking=False) as acquired:
        assert acquired
    with file_mutex(Path(str(private) + ".job.lock")):
        with job_lock(private, blocking=False) as acquired:
            assert not acquired


def test_next_input_republishes_previous_day_for_new_overnight_leg(setup):
    private, serving, ingest = setup

    def with_boundary(day, year):
        ingest(day, year)
        if day > DAY:
            reader = FlightReader(private)
            record = reader.all_records()[0]
            IngestionStore(private).upsert(
                [record.model_copy(update={"provider_id": "new-overnight-leg"})]
            )

    results = list(
        daily.daily_range(private, serving, DAY, DAY + timedelta(days=1), ingest_day=with_boundary)
    )
    assert len(results[-1]["publication"]) == 2
    assert results[-1]["publication"][0]["inserted"] == 1
    assert FlightReader(serving).stats()["flights"] == 3


def test_finish_year_processes_context_without_publishing_next_year(setup):
    private, serving, ingest = setup
    first = date(2024, 12, 31)
    seen = []

    def checked(day, year):
        seen.append((day, year))
        ingest(day, year)

    results = list(
        daily.daily_range(private, serving, first, first, finish_year=True, ingest_day=checked)
    )
    assert seen == [(first, 2024), (DAY, 2024)]
    assert all(p["day"] == "2024-12-31" for r in results for p in r["publication"])
    assert results[-1]["input_day"] == "2025-01-01"


def test_cli_routes_daily_to_same_private_database(tmp_path, monkeypatch):
    private = tmp_path / "private.db"
    serving = tmp_path / "live.db"
    seen = []

    def fake(path, target, start, end, **options):
        assert path == private and target == serving
        assert options["finish_year"]
        # Exercise the nested producer command without performing IO.
        original = cli.run
        monkeypatch.setattr(cli, "run", lambda args, **kw: seen.append((args, kw)))
        options["ingest_day"](DAY, 2025)
        monkeypatch.setattr(cli, "run", original)
        return iter([])

    monkeypatch.setattr(daily, "daily_range", fake)
    cli.run(
        [
            "--db",
            str(private),
            "daily",
            "--serving-db",
            str(serving),
            "--start",
            "2025-01-01",
            "--end",
            "2025-12-31",
            "--finish-year",
        ]
    )
    assert seen[0][1]["database_path"] == private
    assert seen[0][0][-2:] == ["--departure-year", "2025"]


def test_vrs_failure_keeps_day_private_and_does_not_start_next_day(setup, monkeypatch):
    private, serving, ingest = setup
    seen = []

    def checked(day, year):
        seen.append(day)
        ingest(day, year)

    def unavailable(*args, **kwargs):
        raise ValueError("VRS snapshot invalid")

    monkeypatch.setattr(daily, "admit", unavailable)
    with pytest.raises(ValueError, match="VRS snapshot"):
        list(daily.daily_range(private, serving, DAY, DAY + timedelta(days=1), ingest_day=checked))
    assert seen == [DAY]
    assert not serving.exists()
    assert FlightReader(private).stats()["flights"] == 1


def test_active_writer_rejected_before_any_stage(setup):
    private, serving, ingest = setup
    with file_mutex(Path(str(private) + ".job.lock")):
        with pytest.raises(ValueError, match="active ingestion"):
            list(daily.daily_range(private, serving, DAY, DAY, ingest_day=ingest))
    assert not serving.exists()


def test_reset_serving_publication_receipts_forces_republication(setup):
    private, serving, ingest = setup
    list(daily.daily_range(private, serving, DAY, DAY, ingest_day=ingest))
    with sqlite3.connect(serving) as c:
        c.execute("DELETE FROM publication_runs")
    result = list(daily.daily_range(private, serving, DAY, DAY, ingest_day=ingest))
    assert result[0]["event"] == "daily_published"
    assert result[0]["publication"][0]["duplicate"] == 1
