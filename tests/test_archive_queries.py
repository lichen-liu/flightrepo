"""Exact archive aggregates and dimension seeks; budgets count SQLite VM work."""

from contextlib import contextmanager
from datetime import timedelta
from itertools import product
from string import ascii_uppercase

import pytest

from core.quality import review_recommended_sql
from core.storage import EPOCH
from ingestion.updater import FlightUpdater
from server.archive_queries import AIRPORT_PAIRS_CTE, airport_pairs
from server.reader import FlightReader


@pytest.fixture
def archive(tmp_path):
    updater = FlightUpdater(tmp_path / "archive.db")
    updater.initialize()
    with updater.connect() as connection:
        codes = ["".join(code) for code in product(ascii_uppercase, repeat=3)][:1200]
        connection.executemany(
            "INSERT INTO airports(iata) VALUES(?)", [(c,) for c in [*codes, "ZZZ"]]
        )
        connection.execute(
            "INSERT INTO data_sources(id,code,name) VALUES(1,'archive','Archive'),(2,'reference','Reference')"
        )
        connection.execute("""WITH RECURSIVE n(i) AS (
            VALUES(1) UNION ALL SELECT i+1 FROM n WHERE i<20000)
            INSERT INTO flight_legs(source_id,source_record_key,origin_iata,destination_iata,
                status,evidence_kind,itinerary_reviewed,review_reasons,
                departure_scheduled_us,arrival_scheduled_us,
                departure_actual_us,arrival_actual_us,departure_actual_kind,arrival_actual_kind,
                departure_observed_lower_us,departure_observed_upper_us,
                arrival_observed_lower_us,arrival_observed_upper_us,first_seen_us,last_seen_us)
            SELECT 1,printf('%09d',i),CASE WHEN i%2=0 THEN 'AAA' ELSE 'AAB' END,'ZZZ',
                CASE WHEN i%11=0 THEN 'cancelled' ELSE 'landed' END,
                'inferred_airport_proximity',i%17=0,
                CASE i%5 WHEN 0 THEN '["future_unknown"]' WHEN 1 THEN '[null]'
                    WHEN 2 THEN '["observation_gap"]' ELSE '[]' END,
                CASE WHEN i%3=0 THEN i*1000000 END,CASE WHEN i%3=0 THEN i*1000000+100 END,
                CASE WHEN i%7=0 THEN i*1000000 END,CASE WHEN i%7=0 THEN i*1000000+100 END,
                CASE WHEN i%7=0 THEN 'takeoff' END,CASE WHEN i%7=0 THEN 'landing' END,
                ((i*15485863)%20000)*1000000-1000000,((i*15485863)%20000)*1000000-999999,
                ((i*15485863)%20000)*1000000+100,((i*15485863)%20000)*1000000+101,
                i*1000000,i*1000000+123456 FROM n""")
        connection.execute(
            "INSERT INTO flight_tombstones SELECT source_id,source_record_key,0 FROM flight_legs WHERE id%13=0"
        )
        connection.execute("UPDATE flight_legs SET itinerary_reviewed=1 WHERE id=20000")
    return updater, FlightReader(updater.path)


def vm_work(connection, operation):
    steps = [0]

    def progress():
        steps[0] += 100
        return steps[0] > 2_000_000

    connection.set_progress_handler(progress, 100)
    try:
        value = operation()
    finally:
        connection.set_progress_handler(None, 0)
    return value, steps[0]


def test_bulk_summary_matches_public_view_and_quality_semantics(archive):
    updater, reader = archive
    with reader.connect() as connection:
        expected = {}
        for basis in ("scheduled", "observed", "actual"):
            expected[basis] = tuple(
                connection.execute(
                    "SELECT COUNT(*),MIN(departure_lower_us),MAX(departure_lower_us) FROM searchable_legs WHERE time_basis=?",
                    (basis,),
                ).fetchone()
            )
        total = connection.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
        live = connection.execute("SELECT COUNT(DISTINCT leg_id) FROM searchable_legs").fetchone()[
            0
        ]
        review = connection.execute(
            f"SELECT COALESCE(SUM({review_recommended_sql()}),0) FROM flights"
        ).fetchone()[0]
        summary = reader._time_summary(connection)
        for basis, row in expected.items():
            assert tuple(summary[f"{basis}_{key}"] for key in ("count", "first", "last")) == row
        assert expected["observed"][1] == -1_000_000
    catalog = reader.web_catalog(include_airports=False).dataset
    assert (catalog.total_record_count, catalog.flight_count, catalog.needs_review_count) == (
        total,
        live,
        review,
    )
    assert catalog.time_bases == {b: row[0] for b, row in expected.items()}
    assert catalog.first_departure == EPOCH + timedelta(microseconds=expected["scheduled"][1])
    assert reader.stats()["flights"] == total
    assert reader.validate_dataset(data_mode="real") == total


def test_dimension_seeks_preserve_complete_relaxed_graph_with_bounded_work(archive):
    updater, reader = archive
    with reader.connect() as connection:
        expected = set(
            map(
                tuple,
                connection.execute("SELECT DISTINCT origin_iata,destination_iata FROM flight_legs"),
            )
        )
        actual, steps = vm_work(connection, lambda: set(map(tuple, airport_pairs(connection))))
        assert actual == expected and steps < 1000
        plans = [
            row[3]
            for row in connection.execute(
                "EXPLAIN QUERY PLAN " + AIRPORT_PAIRS_CTE + " SELECT * FROM pairs"
            )
        ]
        assert any("origin_iata=? AND destination_iata>?" in plan for plan in plans)
    # More than 1,000 successive destinations verifies sentinel termination,
    # rather than an implicit response/recursion cap on dimension enumeration.
    with updater.connect() as connection:
        destinations = connection.execute(
            "SELECT iata FROM airports WHERE iata NOT IN ('AAA','ZZZ') ORDER BY iata LIMIT 1100"
        ).fetchall()
        connection.executemany(
            "UPDATE flight_legs SET origin_iata='AAA',destination_iata=? WHERE id=?",
            [(row[0], index) for index, row in enumerate(destinations, 1)],
        )
    with reader.connect() as connection:
        actual = set(map(tuple, airport_pairs(connection)))
        assert len(actual) == 1102
        assert actual == set(
            map(
                tuple,
                connection.execute("SELECT DISTINCT origin_iata,destination_iata FROM flight_legs"),
            )
        )


@pytest.mark.parametrize("tombstones", [False, True])
def test_airport_priority_matches_live_set_without_scanning_duplicate_facts(archive, tombstones):
    updater, reader = archive
    with updater.connect() as connection:
        if not tombstones:
            connection.execute("DELETE FROM flight_tombstones")
        else:
            # All AAB facts tombstoned; AAA still active, including cancellation.
            connection.execute(
                "INSERT OR REPLACE INTO flight_tombstones SELECT source_id,source_record_key,0 FROM flight_legs WHERE origin_iata='AAB'"
            )
    with reader.connect() as connection:
        # A fully tombstoned pair must inspect its records to prove absence.
        actual = reader._active_airports(connection)
        expected = connection.execute("""WITH active_codes(iata) AS (
            SELECT origin_iata FROM flights UNION SELECT destination_iata FROM flights)
            SELECT a.* FROM airports a ORDER BY CASE WHEN a.iata IN active_codes THEN 0 ELSE 1 END,a.iata LIMIT 1001""").fetchall()
        assert list(map(dict, actual)) == list(map(dict, expected))
        if not tombstones:
            _, steps = vm_work(connection, lambda: reader._active_airports(connection))
            assert steps < 50_000
        assert any(row["iata"] == "ZZZ" for row in actual[:1000])
    bootstrap = reader.web_bootstrap()
    assert bootstrap.airports_truncated and len(bootstrap.airports) == 1000


def test_legacy_pair_index_absence_retains_exact_results(archive):
    updater, reader = archive
    with reader.connect() as connection:
        expected = set(map(tuple, airport_pairs(connection)))
        airports = list(map(dict, reader._active_airports(connection)))
    with updater.connect() as connection:
        connection.execute("DROP INDEX flight_legs_airport_pair")
    with reader.connect() as connection:
        assert set(map(tuple, airport_pairs(connection))) == expected
        assert list(map(dict, reader._active_airports(connection))) == airports


def test_preflight_sources_and_summary_avoid_archive_aggregation(archive, monkeypatch):
    _, reader = archive
    connect = reader.connect
    work = [0]

    @contextmanager
    def bounded():
        with connect() as connection:

            def progress():
                work[0] += 100
                return work[0] > 10_000

            connection.set_progress_handler(progress, 100)
            yield connection

    monkeypatch.setattr(reader, "connect", bounded)
    assert reader.validate_dataset(data_mode="real", count=False) is None
    bootstrap = reader.web_bootstrap(include_airports=False)
    assert bootstrap.data_mode == "real" and bootstrap.has_records
    assert bootstrap.available_time_bases == ["scheduled", "observed", "actual"]
    assert not bootstrap.airports and not bootstrap.airports_truncated
    assert work[0] < 10_000


def test_summary_cache_keeps_modes_independent_and_invalidates_on_wal_commit(archive):
    updater, reader = archive
    assert reader.web_bootstrap(include_airports=False).airports == []
    assert reader.web_bootstrap().airports
    with updater.connect() as connection:
        connection.execute("INSERT INTO data_sources(id,code,name) VALUES(3,'demo','Demo')")
        connection.execute("UPDATE flight_legs SET source_id=3 WHERE id=1")
        connection.commit()
        assert reader.web_bootstrap(include_airports=False).data_mode == "mixed"
        with pytest.raises(ValueError, match="demo flight records"):
            reader.validate_dataset(data_mode="real", count=False)


@pytest.mark.parametrize("method", ["web_bootstrap", "web_catalog"])
def test_publication_during_read_does_not_cache_an_obsolete_snapshot(archive, monkeypatch, method):
    updater, reader = archive
    read_name = "_read_" + method
    original = getattr(reader, read_name)
    calls = []

    def publish_after_snapshot(**kwargs):
        result = original(**kwargs)
        calls.append(1)
        if len(calls) == 1:
            with updater.connect() as connection:
                connection.execute("INSERT INTO data_sources(id,code,name) VALUES(3,'demo','Demo')")
                connection.execute("UPDATE flight_legs SET source_id=3 WHERE id=1")
        return result

    monkeypatch.setattr(reader, read_name, publish_after_snapshot)
    getattr(reader, method)(include_airports=False)
    refreshed = getattr(reader, method)(include_airports=False)
    getattr(reader, method)(include_airports=False)
    assert len(calls) == 2
    if method == "web_bootstrap":
        assert refreshed.data_mode == "mixed"
    else:
        assert set(refreshed.dataset.providers) == {"archive", "demo"}
