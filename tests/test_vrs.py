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
