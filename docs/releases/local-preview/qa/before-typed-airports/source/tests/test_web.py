"""End-to-end serving contracts and local clock edge cases for the real workspace."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.connections import Airport, AirportTransfer
from core.models import FlightRecord
from core.routing.models import RoutingError
from core.timezones import resolve_local
from ingestion.updater import FlightUpdater
from server.api import app, get_store
from server.reader import FlightReader
from server.web_models import LocalRouteSearchRequest


@pytest.fixture
def demo(tmp_path):
    data = json.loads((Path(__file__).parents[1] / "fixtures/connections-demo.json").read_text())
    updater = FlightUpdater(tmp_path / "demo.db")
    updater.initialize()
    updater.update_airports(
        [Airport.model_validate(a) for a in data["airports"]],
        [AirportTransfer.model_validate(t) for t in data["transfers"]],
    )
    updater.upsert([FlightRecord.model_validate(f) for f in data["flights"]])
    return updater, FlightReader(updater.path), data


@pytest.fixture
def client(demo):
    app.dependency_overrides[get_store] = lambda: demo[1]
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def request(**changes):
    return {
        "origin": "YYZ",
        "destination": "CDG",
        "departure_date": "2026-10-17",
        "arrival_by_local": "2026-10-21T23:59",
        "allow_airport_change": True,
        **changes,
    }


@pytest.mark.parametrize(
    "zone,clock",
    [
        ("America/Toronto", "2026-03-08T02:30"),
        ("Australia/Lord_Howe", "2026-10-04T02:15"),
        ("Pacific/Apia", "2011-12-30T00:00"),
    ],
)
def test_nonexistent_local_time(zone, clock):
    for fold in (None, 0, 1):
        with pytest.raises(RoutingError) as error:
            resolve_local(datetime.fromisoformat(clock), zone, fold=fold)
        assert error.value.code == "nonexistent_local_time"


@pytest.mark.parametrize(
    "zone,clock,difference",
    [
        ("America/Toronto", "2026-11-01T01:30", 3600),
        ("Australia/Lord_Howe", "2026-04-05T01:45", 1800),
    ],
)
def test_repeated_clock_requires_choice(zone, clock, difference):
    value = datetime.fromisoformat(clock)
    with pytest.raises(RoutingError) as error:
        resolve_local(value, zone)
    assert error.value.code == "ambiguous_local_time"
    assert len(error.value.context["utc_choices"]) == 2
    assert (
        resolve_local(value, zone, fold=1) - resolve_local(value, zone, fold=0)
    ).total_seconds() == difference


@pytest.mark.parametrize("date,hours", [("2026-03-08", 23), ("2026-11-01", 25)])
def test_local_departure_day_tracks_dst(demo, date, hours):
    airports = tuple(Airport.model_validate(a) for a in demo[2]["airports"])
    query = LocalRouteSearchRequest.model_validate(
        request(departure_date=date, arrival_by_local=date + "T23:59")
    )
    # Horizon must follow the departure window: use a later destination-local date.
    query.arrival_by_local += timedelta(days=2)
    utc = query.utc_request(airports)
    assert utc.departure_end - utc.departure_start == timedelta(hours=hours)


def test_local_search_uses_exact_dates_and_applies_constraints(client):
    response = client.post(
        "/v1/itineraries/search/local",
        json=request(
            minimum_connection_minutes=120,
            maximum_connection_minutes=None,
            ranking="earliest_arrival",
            avoid_airports=["lgw"],
        ),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["query"]["departure_start"] == "2026-10-17T04:00:00Z"
    assert result["query"]["departure_end"] == "2026-10-18T04:00:00Z"
    assert result["query"]["horizon_end"] == "2026-10-21T21:59:00Z"
    assert result["query"]["minimum_connection_minutes"] == 120
    assert result["query"]["maximum_connection_minutes"] is None
    assert result["departure_timezone"] == "America/Toronto"
    assert result["arrival_timezone"] == "Europe/Paris"
    assert result["items"]
    for item in result["items"]:
        assert all(c["wait_minutes"] >= 120 for c in item["connections"])
        assert all("LGW" not in (f["origin_iata"], f["destination_iata"]) for f in item["legs"])
    utc = client.post("/v1/itineraries/search", json=result["query"])
    assert utc.json()["items"] == result["items"]


def test_missing_timezone_and_aware_wall_time_are_actionable(client, demo):
    with demo[0].connect() as connection:
        connection.execute("DELETE FROM airport_transfers")
        connection.execute("DELETE FROM airports WHERE iata='YYZ'")
    result = client.post("/v1/itineraries/search/local", json=request())
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == "airport_metadata_missing"
    result = client.post(
        "/v1/time/resolve", json={"airport": "CDG", "local_time": "2026-10-18T12:00Z"}
    )
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == "local_time_has_offset"


def test_dst_errors_have_field_and_explicit_utc_choices(client):
    gap = client.post("/v1/time/resolve", json={"airport": "YYZ", "local_time": "2026-03-08T02:30"})
    assert gap.status_code == 422 and gap.json()["detail"]["code"] == "nonexistent_local_time"
    repeated = client.post(
        "/v1/time/resolve", json={"airport": "YYZ", "local_time": "2026-11-01T01:30"}
    )
    assert repeated.status_code == 422
    assert repeated.json()["detail"]["context"]["timezone"] == "America/Toronto"
    first = client.post(
        "/v1/time/resolve",
        json={"airport": "YYZ", "local_time": "2026-11-01T01:30", "fold": 0},
    ).json()
    second = client.post(
        "/v1/time/resolve",
        json={"airport": "YYZ", "local_time": "2026-11-01T01:30", "fold": 1},
    ).json()
    assert first["utc"] == "2026-11-01T05:30:00Z"
    assert second["utc"] == "2026-11-01T06:30:00Z"


@pytest.mark.parametrize(
    "changes",
    [
        {"departure_last_date": "2026-10-16"},
        {"departure_last_date": "2026-11-18"},
        {"minimum_connection_minutes": 500, "maximum_connection_minutes": 20},
        {"arrival_by_local": "2026-10-21T23:59Z"},
        {"origin": 5},
        {"min_connection_minutes": 90},
        {"departure_date": "9999-12-31"},
    ],
)
def test_invalid_local_query_is_422(client, changes):
    response = client.post("/v1/itineraries/search/local", json=request(**changes))
    assert response.status_code == 422, response.text


def test_constraint_typos_are_not_silently_ignored(client, demo):
    query = (
        demo[1]
        .search_local(LocalRouteSearchRequest.model_validate(request()))
        .query.model_dump(mode="json")
    )
    query["max_connection_minutes"] = 5
    assert client.post("/v1/itineraries/search", json=query).status_code == 422
    rebuild = demo[2]["request"] | {"window": {"min_minutes": 500}}
    assert client.post("/v1/plans/rebuild", json=rebuild).status_code == 422


def test_rebuild_preserves_avoided_airports(client, demo):
    query = demo[2]["request"] | {
        "avoid_airports": ["ams"],
        "allow_airport_change": True,
        "window": {"minimum_minutes": 45},
    }
    response = client.post("/v1/plans/rebuild", json=query)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["complete"] and [f["destination_iata"] for f in plan["legs"]] == [
        "LHR",
        "CDG",
    ]
    assert all(o["flight"]["destination_iata"] != "AMS" for o in plan["options"])
    query["chosen_next"] = {"provider": "demo", "provider_id": "lhr-ams-early"}
    denied = client.post("/v1/plans/rebuild", json=query)
    assert (
        denied.status_code == 422
        and denied.json()["detail"]["code"] == "chosen_next_avoided_airport"
    )


def test_local_normalization_and_flight_loading_share_a_snapshot(demo, monkeypatch):
    updater, reader, data = demo
    load = reader._routing_rows

    def change_zone(connection, query, airports, transfers, **kwargs):
        yyz = next(Airport.model_validate(a) for a in data["airports"] if a["iata"] == "YYZ")
        updater.update_airports([yyz.model_copy(update={"timezone": "Asia/Tokyo"})], [])
        return load(connection, query, airports, transfers, **kwargs)

    monkeypatch.setattr(reader, "_routing_rows", change_zone)
    result = reader.search_local(LocalRouteSearchRequest.model_validate(request()))
    assert result.departure_timezone == "America/Toronto"
    assert next(a.timezone for a in result.airports if a.iata == "YYZ") == "America/Toronto"
    assert result.query.departure_start.isoformat() == "2026-10-17T04:00:00+00:00"
    assert result.items
    assert reader.airports(query="YYZ")[0].timezone == "Asia/Tokyo"


def test_catalog_has_facts_but_does_not_claim_coverage(client):
    catalog = client.get("/v1/catalog").json()
    assert catalog["dataset"]["flight_count"] == 12
    assert catalog["dataset"]["providers"] == ["demo"]
    assert catalog["dataset"]["coverage"] == "unknown"
    assert not catalog["airports_truncated"]
    assert len(catalog["airports"]) == 8
    assert not any("data_file" in key for key in catalog["dataset"])


def test_catalog_includes_imports_without_airport_metadata(client, demo):
    record = FlightRecord.model_validate(demo[2]["flights"][0]).model_copy(
        update={
            "provider_id": "no-metadata",
            "origin_iata": "SFO",
            "destination_iata": "MIA",
        }
    )
    demo[0].upsert([record])
    catalog = client.get("/v1/catalog").json()
    airport = next(a for a in catalog["airports"] if a["iata"] == "SFO")
    assert airport["timezone"] is None and airport["latitude"] is None


def test_web_assets_and_api_use_same_origin(client):
    response = client.get("/")
    assert response.status_code == 200 and "<title>Flyji" in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    for path in (
        "app.mjs",
        "map.mjs",
        "format.mjs",
        "app.css",
        "vendor/d3-7.9.0.min.js",
        "vendor/world-110m.geojson",
    ):
        assert client.get("/assets/" + path).status_code == 200
    assert client.get("/v1/catalog").headers["cache-control"] == "no-store"
    assert client.get("/assets/../../.env").status_code == 404
    assert client.get("/assets/%2e%2e/%2e%2e/.env").status_code == 404


def test_readiness_checks_storage_without_creating_it(tmp_path):
    missing = tmp_path / "missing.db"
    app.dependency_overrides[get_store] = lambda: FlightReader(missing)
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            result = client.get("/health/ready")
            assert result.status_code == 503
            assert result.json()["detail"]["code"] == "storage_unavailable"
            assert str(tmp_path) not in result.text
            assert client.get("/").status_code == 200
        assert not missing.exists()
    finally:
        app.dependency_overrides.clear()


def test_ready_can_be_empty(client, demo):
    demo[0].reset()
    assert client.get("/health/ready").json() == {"status": "ready", "flight_count": 0}
    assert client.get("/v1/catalog").json()["airports"] == []


def test_budget_field_typos_are_422(client):
    response = client.post(
        "/v1/itineraries/search/local", json=request(limits={"max_candidates_typo": 1})
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "field,zone",
    [("departure_timezone", "Asia/Tokyo"), ("arrival_timezone", "America/Toronto")],
)
def test_local_search_rejects_changed_timezone_reference(client, field, zone):
    response = client.post("/v1/itineraries/search/local", json=request(**{field: zone}))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "airport_timezone_changed"
    assert response.json()["detail"]["context"]["expected_timezone"] == zone


def test_not_before_rejects_changed_timezone_reference(client):
    response = client.post(
        "/v1/time/resolve",
        json={
            "airport": "LHR",
            "local_time": "2026-10-18T05:00",
            "expected_timezone": "Europe/Paris",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["context"]["actual_timezone"] == "Europe/London"


def test_world_geometry_is_compressed_in_transit(client):
    response = client.get("/assets/vendor/world-110m.geojson", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in response.headers["vary"]
    assert len(response.json()["features"]) == 177
