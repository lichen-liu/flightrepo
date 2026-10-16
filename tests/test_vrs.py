"""VRS reference lifecycle and evidence boundaries, with no live-network test dependency."""

import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import httpx
import pytest

from admin import client as admin
from core.disk_quota import database_bytes, set_quota
from core.file_lock import file_mutex
from core.models import FlightRecord
from ingestion import cli, vrs
from ingestion.publish import publish_day
from ingestion.store import IngestionStore
from ingestion.updater import FlightUpdater

AIRPORTS = b"Code,Name,ICAO,IATA\nKAAA,One,KAAA,AAA\nKBBB,Two,KBBB,BBB\nKCCC,Three,KCCC,CCC\nKDDD,No IATA,KDDD,\n"
ROUTES = b"Callsign,Code,Number,AirlineCode,AirportCodes\nONE1,ONE,1,ONE,KAAA-KBBB\nMULT2,MULT,2,MULT,KAAA-KBBB-KCCC\nMISS3,MISS,3,MISS,KAAA-KDDD\n"


@pytest.fixture
def store(tmp_path):
    store = IngestionStore(tmp_path / "private.db")
    store.initialize()
    return store


def fact(key, callsign="ONE1", origin="AAA", destination="BBB", **changes):
    return FlightRecord.model_validate(
        dict(
            provider="adsblol",
            provider_id=key,
            fetched_at="2023-07-11T00:00:00Z",
            callsign=callsign,
            origin_iata=origin,
            destination_iata=destination,
            departure_actual="2023-07-10T10:00:00Z",
            arrival_actual="2023-07-10T11:00:00Z",
            aircraft_type_code="A320",
            review_reasons=["observation_gap"],
        )
        | changes
    )


def snapshot(store):
    return vrs.import_snapshot(store, ROUTES, AIRPORTS)


def test_match_is_directed_adjacent_undated_and_does_not_release_holds(store):
    snapshot(store)
    store.upsert(
        [
            fact("exact"),
            fact("adjacent", "MULT2", "BBB", "CCC"),
            fact("not-nonstop", "MULT2", "AAA", "CCC"),
            fact("reversed", "ONE1", "BBB", "AAA"),
            fact("unmapped", "MISS3"),
            fact("no-ref", "NOREF1"),
            fact("normalized", " one1 "),
            fact("no-carrier-guess", "ON1"),
        ]
    )
    with closing(sqlite3.connect(store.path)) as c:
        before = c.execute("SELECT * FROM flight_legs ORDER BY id").fetchall()
    report = vrs.report(store.path)
    assert report["counts"] == {
        "exact_two_airport_association": {"records": 2, "held": 2},
        "adjacent_leg_in_multistop_association": {"records": 1, "held": 1},
        "different_undated_route_association": {"records": 2, "held": 2},
        "incomplete_airport_mapping": {"records": 1, "held": 1},
        "no_callsign_reference": {"records": 2, "held": 2},
    }
    assert not report["date_valid_reference"] and not report["admission_changed"]
    with closing(sqlite3.connect(store.path)) as c:
        assert c.execute("SELECT * FROM flight_legs ORDER BY id").fetchall() == before


def test_snapshot_refresh_deduplication_withdrawal_and_receipt_hashes(store):
    receipt = snapshot(store)
    assert receipt["counts"]["routes"] == 3
    assert receipt["counts"]["routes_with_unmapped_airports"] == 1
    assert len(receipt["files"]["routes"]["sha256"]) == 64
    assert receipt["license"] == "CC0-1.0"
    assert snapshot(store)["unchanged"]
    revised = ROUTES.splitlines()[0] + b"\nNEW4,NEW,4,NEW,KBBB-KCCC\n"
    assert not vrs.import_snapshot(store, revised, AIRPORTS)["unchanged"]
    with store.connect() as c:
        assert [r[0] for r in c.execute("SELECT callsign FROM ingestion_vrs_routes")] == ["NEW4"]


@pytest.mark.parametrize(
    "bad",
    [
        b"Callsign,AirportCodes\nONE1,KAAA-KBBB\nONE1,KBBB-KCCC\n",
        b"Callsign,AirportCodes\nONE1,KAAA-KBBB\nBAD!,KAAA-KBBB\n",
        b"Callsign,AirportCodes\nONE1,KAAA-KBBB\nX1,KAAA\n",
        b"Callsign,AirportCodes\nONE1,KAAA-KBBB\nX1,\n",
        b"Callsign,AirportCodes\n",
        b"not,csv\n1,2\n",
        b"Callsign,AirportCodes\nONE1,KAAA-KBBB,unexpected\n",
    ],
)
def test_invalid_refresh_rolls_back_entire_snapshot(store, bad):
    snapshot(store)
    with store.connect() as c:
        previous = c.execute("SELECT * FROM ingestion_vrs_routes ORDER BY callsign").fetchall()
        receipt = c.execute("SELECT receipt_json FROM ingestion_vrs_snapshot").fetchone()[0]
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        vrs.import_snapshot(store, bad, AIRPORTS)
    with store.connect() as c:
        assert (
            c.execute("SELECT * FROM ingestion_vrs_routes ORDER BY callsign").fetchall() == previous
        )
        assert c.execute("SELECT receipt_json FROM ingestion_vrs_snapshot").fetchone()[0] == receipt


def test_quota_failure_rolls_back_and_reset_clears_reference(store, monkeypatch):
    snapshot(store)
    from ingestion.store import check_quota

    def fail_projection(path, connection=None):
        if connection is not None:
            raise OSError("Database quota exceeded")
        return check_quota(path)

    # Force the transaction's final projected-quota check, after all rows were written.
    monkeypatch.setattr("ingestion.store.check_quota", fail_projection)
    with pytest.raises(OSError, match="quota exceeded"):
        vrs.import_snapshot(store, ROUTES.replace(b"ONE1", b"NEW1"), AIRPORTS)
    monkeypatch.undo()
    with store.connect() as c:
        assert c.execute("SELECT 1 FROM ingestion_vrs_routes WHERE callsign='ONE1'").fetchone()
        assert not c.execute("SELECT 1 FROM ingestion_vrs_routes WHERE callsign='NEW1'").fetchone()
    set_quota(store.path, database_bytes(store.path) - 1)
    with pytest.raises(OSError, match="quota exceeded"):
        snapshot(store)
    # Admin reset/clear remains possible above quota and clears all reference tables.
    store.clear(reset=True)
    with store.connect() as c:
        for table in ("ingestion_vrs_snapshot", "ingestion_vrs_routes", "ingestion_vrs_airports"):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_active_job_blocks_refresh(store):
    with file_mutex(Path(str(store.path) + ".job.lock")):
        with pytest.raises(ValueError, match="active ingestion job"):
            snapshot(store)


def test_serving_and_legacy_files_rejected_before_network(tmp_path, monkeypatch):
    serving = FlightUpdater(tmp_path / "serving.db")
    serving.initialize()
    monkeypatch.setattr(vrs, "download", lambda *_: pytest.fail("Must reject before network"))
    with pytest.raises(ValueError, match="ingestion database"):
        vrs.import_files(IngestionStore(serving.path))
    with pytest.raises(ValueError, match="private IngestionStore"):
        vrs.import_snapshot(serving, ROUTES, AIRPORTS)


def test_reporting_is_readonly_date_bounded_and_tombstones_excluded(store, tmp_path):
    snapshot(store)
    store.upsert(
        [
            fact("one"),
            fact(
                "outside",
                departure_actual="2023-07-11T10:00:00Z",
                arrival_actual="2023-07-11T11:00:00Z",
            ),
        ]
    )
    result = vrs.report(store.path, start=date(2023, 7, 10), end=date(2023, 7, 10), cohort_limit=0)
    assert result["counts"]["exact_two_airport_association"]["records"] == 1
    assert result["cohorts"] == []
    with store.connect() as c:
        c.execute(
            "INSERT INTO flight_tombstones SELECT source_id,source_record_key,123 FROM flight_legs WHERE source_record_key='one'"
        )
    assert vrs.report(store.path, start=date(2023, 7, 10), end=date(2023, 7, 10))["counts"] == {}
    serving = tmp_path / "published.db"
    publish_day(store.path, serving, date(2023, 7, 11))
    with sqlite3.connect(serving) as c:
        assert not c.execute(
            "SELECT name FROM sqlite_schema WHERE name LIKE 'ingestion_vrs_%'"
        ).fetchall()
        assert c.execute("SELECT count(*) FROM flights").fetchone()[0] == 0


def test_cli_and_read_only_admin(store, tmp_path, capsys):
    routes, airports = tmp_path / "routes.csv", tmp_path / "airports.csv"
    routes.write_bytes(ROUTES)
    airports.write_bytes(AIRPORTS)
    cli.run(
        [
            "--db",
            str(store.path),
            "vrs",
            "import",
            "--routes-csv",
            str(routes),
            "--airports-csv",
            str(airports),
        ]
    )
    assert json.loads(capsys.readouterr().out)["counts"]["routes"] == 3
    report = tmp_path / "report.json"
    cli.run(["--db", str(store.path), "vrs", "report", "--report", str(report)])
    assert json.loads(report.read_text())["snapshot"]["counts"]["routes"] == 3
    capsys.readouterr()
    admin.run(
        ["--db", str(store.path), "inspect", "query", "SELECT count(*) FROM ingestion_vrs_routes"]
    )
    assert json.loads(capsys.readouterr().out)["rows"] == [[3]]
    with pytest.raises(sqlite3.DatabaseError):
        admin.run(["--db", str(store.path), "inspect", "query", "DELETE FROM ingestion_vrs_routes"])
    with pytest.raises(ValueError, match="overwrite a database"):
        cli.run(["--db", str(store.path), "vrs", "report", "--report", str(store.path)])
    with pytest.raises(ValueError, match="both"):
        vrs.import_files(store, routes_path=routes)


def test_network_is_two_bounded_bulk_csvs_in_memory(store, monkeypatch):
    seen = []

    def stream(method, url, **kwargs):
        seen.append(url)
        data = ROUTES if url.endswith("routes.csv") else AIRPORTS

        class Response:
            def __enter__(self):
                return httpx.Response(200, request=httpx.Request(method, url), content=data)

            def __exit__(self, *args):
                pass

        return Response()

    monkeypatch.setattr(vrs.httpx, "stream", stream)
    assert vrs.import_files(store)["counts"]["routes"] == 3
    assert seen == [vrs.BASE_URL + "routes.csv", vrs.BASE_URL + "airports.csv"]
    monkeypatch.setattr(vrs, "MAX_BYTES", 10)
    with pytest.raises(ValueError, match="exceeds"):
        vrs.download("routes")


def test_nonstandard_optional_iata_codes_are_observable_unknowns(store):
    airports = AIRPORTS.replace(b"KBBB,Two,KBBB,BBB", b"KBBB,Two,KBBB,SB2")
    receipt = vrs.import_snapshot(store, ROUTES, airports)
    assert receipt["counts"]["invalid_iata_mappings"] == 1
    store.upsert([fact("one")])
    assert vrs.report(store.path)["counts"] == {
        "incomplete_airport_mapping": {"records": 1, "held": 1}
    }


@pytest.mark.parametrize("kind", ["exact", "adjacent"])
def test_explicit_admission_preserves_gap_evidence_and_flows_through_consumers(
    store, tmp_path, kind
):
    from core.routing.models import RouteSearchRequest
    from ingestion.audit import audit_database
    from server.reader import FlightReader

    snapshot(store)
    callsign = "ONE1" if kind == "exact" else "MULT2"
    record = fact("gap", callsign, review_reasons=["airborne_airport_estimate", "observation_gap"])
    store.upsert([record])
    dry = vrs.admit(store.path, start="2023-07-10", end="2023-07-10")
    assert dry["counts"]["released"] == 1
    assert FlightReader(store.path).flight("adsblol", "gap").needs_review
    result = vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    assert result["counts"]["released"] == 1 and result["human_approvals_added"] == 0
    saved = FlightReader(store.path).flight("adsblol", "gap")
    assert not saved.needs_review and saved.route_verified and not saved.itinerary_reviewed
    assert saved.review_reasons == record.review_reasons
    assert saved.route_verification.match_kind == kind
    assert saved.model_dump(mode="json")["route_verified"] is True
    assert audit_database(store.path)["counts"]["held"] == 0
    serving = tmp_path / "serving.db"
    assert publish_day(store.path, serving, date(2023, 7, 10))["inserted"] == 1
    reader = FlightReader(serving)
    query = RouteSearchRequest(
        origin="AAA",
        destination="BBB",
        departure_start="2023-07-10T00:00:00Z",
        departure_end="2023-07-11T00:00:00Z",
        horizon_end="2023-07-12T00:00:00Z",
        time_basis="actual",
    )
    assert reader.search_itineraries(query).items[0].legs[0].route_verified
    assert reader.available_dates("AAA", "UTC").dates == [date(2023, 7, 10)]
    assert reader.overview()["needs_review_count"] == 0
    assert (
        vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)["counts"].get(
            "changed", 0
        )
        == 0
    )
    with store.connect() as c:
        proof = c.execute("SELECT route_verification FROM flight_legs").fetchone()[0]
        assert (
            c.execute(
                "SELECT count(*) FROM ingestion_documents WHERE kind='validation'"
            ).fetchone()[0]
            == 1
        )
        assert c.execute("SELECT locally_modified_us FROM flight_legs").fetchone()[0] is None
    with reader.connect() as c:
        assert c.execute("SELECT route_verification FROM flight_legs").fetchone()[0] == proof
        assert not c.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='ingestion_vrs_routes'"
        ).fetchone()


def test_admission_does_not_waive_timing_other_reasons_or_nonadjacent_pairs(store):
    snapshot(store)
    store.upsert(
        [
            fact("timing", review_reasons=["observation_gap", "implausible_airport_timing"]),
            fact("variation", review_reasons=["callsign_variation"]),
            fact("nonadjacent", "MULT2", "AAA", "CCC"),
            fact("reverse", "ONE1", "BBB", "AAA"),
            fact("unknown", "NODATA"),
            fact("approved", itinerary_reviewed=True),
            fact("edited"),
        ]
    )
    with store.connect() as c:
        c.execute("UPDATE flight_legs SET locally_modified_us=1 WHERE source_record_key='edited'")
    result = vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    assert result["counts"]["protected"] == 2
    assert result["counts"]["released"] == 0
    from server.reader import FlightReader

    reader = FlightReader(store.path)
    for key in ["timing", "variation", "nonadjacent", "reverse", "unknown", "edited"]:
        assert reader.flight("adsblol", key).needs_review
    assert reader.flight("adsblol", "approved").route_verification is None


def test_reference_withdrawal_reholds_and_retracts_published_gap(store, tmp_path):
    snapshot(store)
    store.upsert([fact("gap")])
    vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    serving = tmp_path / "serving.db"
    assert publish_day(store.path, serving, date(2023, 7, 10))["inserted"] == 1
    vrs.import_snapshot(store, ROUTES.replace(b"KAAA-KBBB\n", b"KAAA-KCCC\n"), AIRPORTS)
    result = vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    assert result["counts"]["reheld"] == 1
    assert publish_day(store.path, serving, date(2023, 7, 10))["retired"] == 1


def test_route_edit_invalidates_evidence_in_sql_and_model(store):
    from server.reader import FlightReader

    snapshot(store)
    store.upsert([fact("gap")])
    vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    with store.connect() as c:
        c.execute("INSERT INTO airports(iata) VALUES('CCC')")
        c.execute("UPDATE flights SET destination_iata='CCC' WHERE provider_id='gap'")
        assert c.execute("SELECT count(*) FROM searchable_legs").fetchone()[0] == 0
    saved = FlightReader(store.path).flight("adsblol", "gap")
    assert not saved.route_verified and saved.needs_review
    assert not saved.model_dump(mode="json")["route_verified"]


def test_admission_respects_date_bounds_cli_readonly_default_locks_and_quota(
    store, capsys, monkeypatch
):
    snapshot(store)
    store.upsert([fact("gap")])
    cli.run(
        ["--db", str(store.path), "vrs", "admit", "--start", "2023-07-11", "--end", "2023-07-11"]
    )
    assert json.loads(capsys.readouterr().out)["counts"] == {}
    with file_mutex(Path(str(store.path) + ".job.lock")):
        with pytest.raises(ValueError, match="active ingestion job"):
            vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    original = __import__("ingestion.store", fromlist=["check_quota"]).check_quota
    checks = 0

    def fail_changes(path, connection=None):
        nonlocal checks
        if connection is not None:
            checks += 1
            if checks == 2:
                raise OSError("Database quota exceeded")
        return original(path, connection)

    monkeypatch.setattr("ingestion.store.check_quota", fail_changes)
    with pytest.raises(OSError, match="quota exceeded"):
        vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    with store.connect() as c:
        assert c.execute("SELECT route_verification FROM flight_legs").fetchone()[0] is None
