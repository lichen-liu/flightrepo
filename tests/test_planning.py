import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from public_helpers import rebuild_payload

from core.connections import Airport, AirportTransfer, RebuildRequest
from core.models import FlightRecord, FlightStatus
from ingestion.updater import FlightUpdater
from server.api import app, get_store
from server.reader import FlightReader


@pytest.fixture
def demo(tmp_path):
    data = json.loads((Path(__file__).parents[1] / "fixtures/connections-demo.json").read_text())
    updater = FlightUpdater(tmp_path / "flights.db")
    updater.initialize()
    updater.update_airports(
        [Airport.model_validate(a) for a in data["airports"]],
        [AirportTransfer.model_validate(t) for t in data["transfers"]],
    )
    updater.upsert([FlightRecord.model_validate(f) for f in data["flights"]])
    return data, updater, FlightReader(updater.path)


def request(data, **updates):
    return RebuildRequest.model_validate({**data["request"], **updates})


def ref(identifier):
    return {"provider": "demo", "provider_id": identifier}


def ids(result):
    return [f.provider_id for f in result.legs]


def test_airport_change_opt_in_and_directed_allowance(demo):
    data, _, reader = demo
    options = reader.rebuild(request(data)).options
    assert "lgw-ams-early" in [o.flight.provider_id for o in options]
    assert "lgw-ams-too-soon" not in [o.flight.provider_id for o in options]
    city_option = next(o for o in options if o.flight.provider_id == "lgw-ams-early")
    assert city_option.connection.airport_change
    assert city_option.connection.minimum_minutes == 180
    no_change = reader.rebuild(request(data, allow_airport_change=False))
    assert all(not o.connection.airport_change for o in no_change.options)


def test_changing_next_leg_rebuilds_entire_suffix(demo):
    data, _, reader = demo
    early = reader.rebuild(request(data, chosen_next=ref("lhr-ams-early")))
    later = reader.rebuild(request(data, chosen_next=ref("lhr-ams-late")))
    assert ids(early) == ["d17-lhr-a", "lhr-ams-early", "ams-cdg-early"]
    assert ids(later) == ["d17-lhr-a", "lhr-ams-late", "ams-cdg-late"]
    assert early.legs[0] == later.legs[0]
    assert later.complete


def test_overnight_and_explicit_timestamp_without_six_hour_cap(demo):
    data, _, reader = demo
    later = reader.rebuild(request(data, chosen_next=ref("lgw-ams-overnight")))
    assert ids(later) == ["d17-lhr-a", "lgw-ams-overnight", "ams-cdg-next-day"]
    assert later.connections[0].wait_minutes == 1500
    after = reader.rebuild(request(data, window={"not_before": "2026-10-19T00:00:00Z"}))
    assert ids(after) == ids(later)
    assert all(o.flight.departure_scheduled.isoformat() >= "2026-10-19" for o in after.options)


def test_no_tail_retains_chosen_prefix_and_reports_loaded_gap(demo):
    data, _, reader = demo
    result = reader.rebuild(request(data, chosen_next=ref("no-tail")))
    assert ids(result) == ["d17-lhr-a", "no-tail"]
    assert not result.complete
    assert result.diagnostics.exhaustive
    result = reader.rebuild(request(data, window={"not_before": "2026-10-23T00:00:00Z"}))
    assert ids(result) == ["d17-lhr-a"] and not result.complete


def test_no_automatic_reverse_or_unconfigured_transfer(demo):
    data, updater, reader = demo
    updater.upsert(
        [
            FlightRecord.model_validate(
                {
                    **data["flights"][0],
                    "provider_id": "arrive-lgw",
                    "destination_iata": "LGW",
                }
            )
        ]
    )
    result = reader.rebuild(request(data, prefix=[ref("arrive-lgw")]))
    assert all(o.flight.origin_iata == "LGW" for o in result.options)


def test_metadata_batch_rollback_and_foreign_keys(demo):
    data, updater, reader = demo
    with pytest.raises(ValueError):
        updater.update_airports(
            [Airport.model_validate({**data["airports"][2], "name": "Changed"})],
            [
                AirportTransfer(
                    arrival_iata="LHR",
                    departure_iata="AMS",
                    minimum_minutes=180,
                    source="bad city pair",
                )
            ],
        )
    with reader.connect() as conn:
        assert (
            conn.execute("SELECT name FROM airports WHERE iata='LHR'").fetchone()[0]
            == data["airports"][2]["name"]
        )
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with updater.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO airport_transfers VALUES ('LHR','XXX',180,'bad')")


def test_metadata_city_update_cannot_invalidate_existing_rule(demo):
    data, updater, _ = demo
    lhr = next(a for a in data["airports"] if a["iata"] == "LHR")
    with pytest.raises(ValueError):
        updater.update_airports([Airport.model_validate({**lhr, "city_id": "changed"})], [])


@pytest.mark.parametrize(
    "updates",
    [
        {"horizon_end": "2026-10-18T01:00:00Z"},
        {"horizon_end": "2027-01-01T00:00:00Z"},
        {"horizon_end": "2026-10-24T00:00:00"},
        {"window": {"not_before": "2026-10-19T00:00:00"}},
        {"window": {"minimum_minutes": 45, "maximum_minutes": 30}},
        {"chosen_next": ref("lgw-ams-too-soon")},
        {"prefix": [ref("missing")]},
    ],
)
def test_invalid_window_or_stale_flight_is_rejected(demo, updates):
    data, _, reader = demo
    with pytest.raises(ValueError):
        reader.rebuild(request(data, **updates))


def test_api_calculation_is_read_only_and_errors_are_422(demo):
    data, _, reader = demo
    app.dependency_overrides[get_store] = lambda: reader
    try:
        before = reader.all_records()
        with TestClient(app) as client:
            result = client.post("/v1/plans/rebuild", json=rebuild_payload(reader, data["request"]))
            assert result.status_code == 200 and result.json()["complete"]
            assert [
                a["iata"] for a in client.get("/v1/airports", params={"city_id": "GB:LON"}).json()
            ] == ["LGW", "LHR"]
            bad = client.post(
                "/v1/plans/rebuild",
                json={**data["request"], "chosen_next": ref("missing")},
            )
            assert bad.status_code == 422
        assert reader.all_records() == before
    finally:
        app.dependency_overrides.clear()


def test_reference_lookup_preserves_exact_airports_and_zones(demo):
    _, _, reader = demo
    assert [a.iata for a in reader.airports("GB:LON")] == ["LGW", "LHR"]
    result = reader.airports(query="Gatwick")
    assert len(result) == 1 and result[0].iata == "LGW" and result[0].timezone == "Europe/London"


def test_unreachable_flights_do_not_consume_the_snapshot_budget(demo):
    data, updater, reader = demo
    record = FlightRecord.model_validate(data["flights"][1])
    updater.upsert(
        [
            record.model_copy(
                update={
                    "provider_id": f"filler-{i}",
                    "origin_iata": "XXX",
                    "destination_iata": "YYY",
                }
            )
            for i in range(2001)
        ]
    )
    result = reader.rebuild(request(data))
    assert not result.data_truncated and result.complete
    assert result.diagnostics.stats.rows_loaded < 20


def test_snapshot_flight_cap_is_explicit(demo):
    data, _, reader = demo
    result = reader.rebuild(request(data, limits={"max_records": 2}))
    assert result.data_truncated
    assert not result.diagnostics.exhaustive
    assert "data_limit" in result.diagnostics.termination_reasons


def test_cancelled_prefix_and_chosen_next_are_rejected(demo):
    data, updater, reader = demo
    record = FlightRecord.model_validate(data["flights"][1])
    updater.upsert([record.model_copy(update={"status": FlightStatus.CANCELLED})])
    with pytest.raises(ValueError):
        reader.rebuild(request(data, chosen_next=ref("lhr-ams-early")))
    with pytest.raises(ValueError):
        reader.rebuild(request(data, prefix=[ref("d17-lhr-a"), ref("lhr-ams-early")]))


def test_reset_clears_all_local_data_without_dropping_schema(demo):
    _, updater, reader = demo
    updater.reset()
    with reader.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM flights").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM airports").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM airport_transfers").fetchone()[0] == 0
