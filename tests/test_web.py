"""End-to-end serving contracts and local clock edge cases for the real workspace."""

import json
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from itertools import islice, product
from pathlib import Path
from string import ascii_uppercase
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from core.connections import Airport, AirportTransfer
from core.models import FlightRecord, FlightStatus
from core.routing.models import RoutingError
from core.timezones import resolve_local
from ingestion.updater import FlightUpdater
from server.api import WEB_ROOT, app, get_store
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


def departure_request(**changes):
    payload = request(**changes)
    payload.pop("arrival_by_local")
    return payload


def test_date_availability_is_origin_specific_and_uses_requested_clock(client, demo):
    response = client.get("/v1/dates", params={"origin": "yyz"})
    assert response.status_code == 200
    assert response.json() == {
        "origin": "YYZ",
        "timezone": "America/Toronto",
        "dates": ["2026-10-17"],
    }
    record = FlightRecord.model_validate(demo[2]["flights"][0]).model_copy(
        update={
            "provider_id": "late-night",
            "departure_scheduled": datetime.fromisoformat("2026-10-20T02:00:00Z"),
            "arrival_scheduled": datetime.fromisoformat("2026-10-20T10:00:00Z"),
        }
    )
    demo[0].upsert([record])
    assert client.get("/v1/dates?origin=YYZ").json()["dates"] == ["2026-10-17", "2026-10-19"]
    assert client.get("/v1/dates?origin=YYZ&clock=UTC").json()["dates"] == [
        "2026-10-17",
        "2026-10-20",
    ]
    assert client.get("/v1/dates?origin=XYZ&clock=UTC").json()["dates"] == []
    assert client.get("/v1/dates?origin=XYZ").status_code == 422
    assert client.get("/v1/dates?origin=YYZ&clock=invalid").status_code == 422
    assert client.get("/v1/dates?origin=invalid").status_code == 422


def test_cancelled_departures_do_not_enable_calendar_dates(client, demo):
    with demo[0].connect() as connection:
        connection.execute("UPDATE flights SET status='cancelled' WHERE origin_iata='YYZ'")
    assert client.get("/v1/dates?origin=YYZ").json()["dates"] == []


def test_explicit_destination_date_filter_remains_available_to_api_clients(client, demo):
    record = FlightRecord.model_validate(demo[2]["flights"][0]).model_copy(
        update={
            "provider_id": "different-destination",
            "destination_iata": "YVR",
            "departure_scheduled": datetime.fromisoformat("2026-10-20T12:00:00Z"),
            "arrival_scheduled": datetime.fromisoformat("2026-10-20T17:00:00Z"),
        }
    )
    demo[0].upsert([record])
    params = {"origin": "YYZ", "clock": "UTC", "time_basis": "scheduled"}
    assert client.get("/v1/dates", params=params).json()["dates"] == [
        "2026-10-17",
        "2026-10-20",
    ]
    direct = client.get("/v1/dates", params={**params, "destination": "yvr"})
    assert direct.status_code == 200
    assert direct.json() == {
        "origin": "YYZ",
        "destination": "YVR",
        "timezone": "UTC",
        "dates": ["2026-10-20"],
    }
    assert client.get("/v1/dates", params={**params, "destination": "LHR"}).json()["dates"] == [
        "2026-10-17",
    ]
    assert client.get("/v1/dates", params={**params, "destination": "bad-code"}).status_code == 422


def test_calendar_includes_nearby_departures_to_any_destination(client, demo):
    original = FlightRecord.model_validate(demo[2]["flights"][0])
    demo[0].upsert(
        [
            original.model_copy(
                update={
                    "provider_id": "nearby-late",
                    "origin_iata": "LGW",
                    "destination_iata": "NRT",
                    "departure_scheduled": datetime.fromisoformat("2026-10-20T23:30:00Z"),
                    "arrival_scheduled": datetime.fromisoformat("2026-10-21T10:00:00Z"),
                }
            ),
            original.model_copy(
                update={
                    "provider_id": "distant",
                    "origin_iata": "AMS",
                    "destination_iata": "NRT",
                    "departure_scheduled": datetime.fromisoformat("2026-10-22T12:00:00Z"),
                    "arrival_scheduled": datetime.fromisoformat("2026-10-23T10:00:00Z"),
                }
            ),
        ]
    )
    params = {"origin": "LHR", "include_nearby": True}
    local = client.get("/v1/dates", params=params).json()
    assert local["dates"] == ["2026-10-18", "2026-10-19", "2026-10-21"]
    assert local["departure_airports"] == ["LHR", "LGW"]
    assert local["nearby_radius_km"] == 100
    assert local["timezone"] == "Europe/London"
    utc = client.get("/v1/dates", params={**params, "clock": "UTC"}).json()
    assert utc["dates"] == ["2026-10-18", "2026-10-19", "2026-10-20"]
    assert client.get("/v1/dates?origin=LHR").json()["dates"] == ["2026-10-18", "2026-10-19"]
    with demo[0].connect() as connection:
        connection.execute("UPDATE flights SET status='cancelled' WHERE origin_iata='LHR'")
    nearby_only = client.get("/v1/dates", params=params).json()
    assert nearby_only["dates"] == ["2026-10-18", "2026-10-19", "2026-10-21"]
    assert nearby_only["departure_airports"] == ["LGW"]


def test_nearby_dates_fall_back_to_exact_origin_without_coordinates(client, demo):
    with demo[0].connect() as connection:
        connection.execute("UPDATE airports SET latitude=NULL,longitude=NULL WHERE iata='YYZ'")
    data = client.get("/v1/dates?origin=YYZ&include_nearby=true").json()
    assert data["dates"] == ["2026-10-17"]
    assert data["departure_airports"] == ["YYZ"]
    unknown = client.get("/v1/dates?origin=XYZ&include_nearby=true&clock=UTC").json()
    assert unknown["dates"] == []
    assert unknown["departure_airports"] == []


def test_catalog_counts_distinct_legs_across_bases_and_preserves_timestamp_precision(client, demo):
    original = FlightRecord.model_validate(demo[2]["flights"][0])
    updated = original.model_copy(
        update={
            "departure_actual": original.departure_scheduled + timedelta(microseconds=123456),
            "arrival_actual": original.arrival_scheduled + timedelta(microseconds=654321),
            "fetched_at": original.fetched_at + timedelta(microseconds=123456),
        }
    )
    demo[0].upsert([updated])
    catalog = client.get("/v1/catalog").json()["dataset"]
    assert catalog["flight_count"] == catalog["total_record_count"] == 12
    assert catalog["time_bases"] == {"scheduled": 12, "observed": 0, "actual": 1}
    assert datetime.fromisoformat(catalog["newest_fact_refresh"]) == updated.fetched_at


def test_airport_coverage_counts_exact_airports_and_missing_directions(client, demo):
    response = client.get("/v1/airport-coverage?airport=yyz&airport=NRT&time_basis=scheduled")
    assert response.status_code == 200
    result = response.json()
    assert result["time_basis"] == "scheduled"
    for airport in result["airports"]:
        code = airport["airport"]
        inbound = sum(f["destination_iata"] == code for f in demo[2]["flights"])
        outbound = sum(f["origin_iata"] == code for f in demo[2]["flights"])
        assert airport == {
            "airport": code,
            "inbound": inbound,
            "outbound": outbound,
            "stored_inbound": inbound,
            "stored_outbound": outbound,
        }
    assert result["airports"][0]["inbound"] == 0
    assert result["airports"][1]["outbound"] == 0
    assert client.get("/v1/airport-coverage?airport=XYZ").json()["airports"] == [
        {
            "airport": "XYZ",
            "inbound": 0,
            "outbound": 0,
            "stored_inbound": 0,
            "stored_outbound": 0,
        }
    ]
    assert len(client.get("/v1/airport-coverage?airport=YYZ&airport=yyz").json()["airports"]) == 1
    assert client.get("/v1/airport-coverage?airport=bad-code").status_code == 422
    assert client.get("/v1/airport-coverage?airport=YYZ&time_basis=bad").status_code == 422
    assert client.get("/v1/airport-coverage?airport=YYZ&airport=NRT&airport=LHR").status_code == 422
    assert client.get("/v1/airport-coverage").status_code == 422


def test_airport_coverage_distinguishes_stored_from_searchable_without_counting_deleted(
    client, demo
):
    original = FlightRecord.model_validate(demo[2]["flights"][0])
    demo[0].upsert(
        [
            original.model_copy(update={"provider_id": "review-needed"}),
            original.model_copy(
                update={"provider_id": "cancelled", "status": FlightStatus.CANCELLED}
            ),
        ]
    )
    with demo[0].connect() as connection:
        connection.execute(
            "UPDATE flights SET review_reasons=? WHERE provider_id='review-needed'",
            (json.dumps(["test review"]),),
        )
    observed = client.get("/v1/airport-coverage?airport=YYZ&time_basis=observed").json()
    assert observed["airports"][0]["stored_outbound"] == 3
    assert observed["airports"][0]["outbound"] == 0
    scheduled = client.get("/v1/airport-coverage?airport=YYZ&time_basis=scheduled").json()
    assert scheduled["airports"][0]["stored_outbound"] == 3
    assert scheduled["airports"][0]["outbound"] == 1
    with demo[0].connect() as connection:
        connection.execute(
            "INSERT INTO flight_tombstones(source_id,source_record_key,deleted_us) "
            "SELECT source_id,source_record_key,last_seen_us FROM flight_legs "
            "WHERE source_record_key='cancelled'"
        )
    assert (
        client.get("/v1/airport-coverage?airport=YYZ").json()["airports"][0]["stored_outbound"] == 2
    )


def test_search_availability_uses_departure_day_and_destination_arrival_window(client, demo):
    params = {
        "origin": "YYZ",
        "destination": "CDG",
        "clock": "UTC",
        "time_basis": "scheduled",
        "departure_date": "2026-10-17",
    }
    response = client.get("/v1/search-availability", params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["dates"] == ["2026-10-17"]
    assert data["outbound"] == 1
    assert data["inbound"] == 5
    assert data["arrival_start"] == "2026-10-17T00:00:00Z"
    assert data["arrival_end"] == "2026-10-25T00:00:00Z"
    original = FlightRecord.model_validate(demo[2]["flights"][0])
    demo[0].upsert(
        [
            original.model_copy(
                update={
                    "provider_id": "other-date",
                    "departure_scheduled": datetime.fromisoformat("2026-12-01T12:00:00Z"),
                    "arrival_scheduled": datetime.fromisoformat("2026-12-01T18:00:00Z"),
                }
            ),
            original.model_copy(
                update={
                    "provider_id": "past-inbound",
                    "origin_iata": "AMS",
                    "destination_iata": "CDG",
                    "departure_scheduled": datetime.fromisoformat("2026-10-15T12:00:00Z"),
                    "arrival_scheduled": datetime.fromisoformat("2026-10-16T18:00:00Z"),
                }
            ),
            original.model_copy(
                update={
                    "provider_id": "far-inbound",
                    "origin_iata": "AMS",
                    "destination_iata": "CDG",
                    "departure_scheduled": datetime.fromisoformat("2026-11-01T12:00:00Z"),
                    "arrival_scheduled": datetime.fromisoformat("2026-11-01T18:00:00Z"),
                }
            ),
        ]
    )
    data = client.get("/v1/search-availability", params=params).json()
    assert data["dates"] == ["2026-10-17"]
    assert data["outbound"] == 1 and data["inbound"] == 5
    no_arrivals = client.get(
        "/v1/search-availability", params={**params, "destination": "XYZ"}
    ).json()
    assert (
        no_arrivals["dates"] == [] and no_arrivals["outbound"] == 1 and no_arrivals["inbound"] == 0
    )
    no_departures = client.get("/v1/search-availability", params={**params, "origin": "XYZ"}).json()
    assert no_departures["dates"] == [] and no_departures["outbound"] == 0


@pytest.mark.parametrize("day,hours", [("2026-03-08", 23), ("2026-11-01", 25)])
def test_search_availability_resolves_dst_and_counts_same_day_arrivals(client, demo, day, hours):
    original = FlightRecord.model_validate(demo[2]["flights"][0])
    first = datetime.fromisoformat(day + "T12:00:00Z")
    demo[0].upsert(
        [
            original.model_copy(
                update={
                    "provider_id": "same-day",
                    "destination_iata": "CDG",
                    "departure_scheduled": first,
                    "arrival_scheduled": first + timedelta(hours=2),
                }
            )
        ]
    )
    data = client.get(
        "/v1/search-availability",
        params={
            "origin": "YYZ",
            "destination": "CDG",
            "clock": "local",
            "departure_date": day,
        },
    ).json()
    assert data["selected_date"] == day and data["outbound"] == 1 and data["inbound"] >= 1
    assert datetime.fromisoformat(data["arrival_end"]) - datetime.fromisoformat(
        data["arrival_start"]
    ) == timedelta(days=7, hours=hours)


def test_bootstrap_does_not_wait_for_archive_counts(client, demo, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Bootstrap must not compute archive-wide counts")

    monkeypatch.setattr(demo[1], "web_catalog", fail)
    monkeypatch.setattr(demo[1], "_time_bases", fail)
    result = client.get("/v1/bootstrap")
    assert result.status_code == 200
    assert result.json()["available_time_bases"] == ["scheduled"]
    assert result.json()["default_time_basis"] == "scheduled"
    assert len(result.json()["airports"]) == 8


def test_aircraft_type_survives_storage_edit_and_serving(client, demo):
    record = FlightRecord.model_validate(demo[2]["flights"][0])
    record.aircraft_type_code = "A320"
    demo[0].upsert([record])
    saved = demo[1].flight(record.provider, record.provider_id)
    assert saved.aircraft_type_code == record.aircraft_type_code
    changed = saved.model_copy(update={"flight_number": "AC123"})
    demo[0].update(saved, changed)
    assert demo[1].flight(record.provider, record.provider_id) == changed
    result = client.post("/v1/itineraries/search/local", json=departure_request()).json()
    legs = [f for item in result["items"] for f in item["legs"]]
    assert any(f.get("aircraft_type_code") == "A320" for f in legs)


def test_trace_serving_is_not_part_of_the_application(client):
    assert client.get("/v1/observations?day=2025-01-01").status_code == 404
    assert client.get("/v1/observations/2025-01-01/ad4584/trace").status_code == 404


def test_departure_search_needs_no_arrival_input(client):
    response = client.post("/v1/itineraries/search/local", json=departure_request())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["items"]
    query = result["query"]
    assert query["departure_end"] == "2026-10-18T04:00:00Z"
    assert query["horizon_end"] == "2026-10-25T04:00:00Z"


@pytest.mark.parametrize("date,hours", [("2026-03-08", 23), ("2026-11-01", 25)])
def test_default_horizon_follows_resolved_departure_window_across_dst(client, date, hours):
    response = client.post(
        "/v1/itineraries/search/local", json=departure_request(departure_date=date)
    )
    assert response.status_code == 200, response.text
    query = response.json()["query"]
    start, end, horizon = (
        datetime.fromisoformat(query[field])
        for field in ("departure_start", "departure_end", "horizon_end")
    )
    assert end - start == timedelta(hours=hours)
    assert horizon - end == timedelta(days=7)


def test_default_horizon_does_not_require_destination_timezone(client, demo):
    record = FlightRecord.model_validate(demo[2]["flights"][0]).model_copy(
        update={"provider_id": "unknown-destination", "destination_iata": "XYZ"}
    )
    demo[0].upsert([record])
    response = client.post(
        "/v1/itineraries/search/local",
        json=departure_request(destination="XYZ", arrival_timezone="Europe/Paris", arrival_fold=1),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["items"] and result["arrival_timezone"] is None
    assert result["query"]["horizon_end"] == "2026-10-25T04:00:00Z"


@pytest.mark.parametrize("date", ["9999-12-28", "9999-12-31"])
def test_default_horizon_overflow_is_a_readable_422(client, date):
    response = client.post(
        "/v1/itineraries/search/local", json=departure_request(departure_date=date)
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_local_dates"


def test_wide_departure_range_remains_editable(client):
    response = client.post(
        "/v1/itineraries/search/local",
        json=departure_request(departure_last_date="2026-11-15", via_airport=["LHR", "AMS"]),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    legs = result["items"][0]["legs"]
    plan = client.post(
        "/v1/plans/rebuild",
        json={
            "prefix": [{"provider": legs[0]["provider"], "provider_id": legs[0]["provider_id"]}],
            "destination": "CDG",
            "horizon_end": result["query"]["horizon_end"],
            "max_legs": 3,
            "via_airport": ["LHR", "AMS"],
        },
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["complete"] and plan.json()["legs"] == legs


def test_departure_range_still_respects_elapsed_day_bound(client):
    response = client.post(
        "/v1/itineraries/search/local",
        json=departure_request(departure_date="2026-10-10", departure_last_date="2026-11-09"),
    )
    assert response.status_code == 422, response.text


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
        connection.execute("UPDATE airports SET timezone=NULL WHERE iata='YYZ'")
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


def test_required_via_is_enforced_before_top_result(client):
    unconstrained = client.post("/v1/itineraries/search/local", json=request(limit=1)).json()
    assert len(unconstrained["items"][0]["legs"]) == 2
    response = client.post(
        "/v1/itineraries/search/local", json=request(via_airport=" ams ", limit=1)
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["query"]["via_airport"] == "AMS"
    assert len(result["items"]) == 1
    assert [f["destination_iata"] for f in result["items"][0]["legs"]] == ["LHR", "AMS", "CDG"]
    assert result["diagnostics"]["exhaustive"]


@pytest.mark.parametrize("endpoint", ["/v1/itineraries/search/local", "/v1/itineraries/search"])
def test_ordered_via_list_search_and_rebuild(client, demo, endpoint):
    payload = request(via_airport=[" lhr ", "ams"], limit=1)
    if endpoint.endswith("/local"):
        query = payload
    else:
        query = (
            LocalRouteSearchRequest.model_validate(payload)
            .utc_request(demo[1].airports(limit=100))
            .model_dump(mode="json")
        )
    response = client.post(endpoint, json=query)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["query"]["via_airport"] == ["LHR", "AMS"]
    assert len(result["items"]) == 1
    legs = result["items"][0]["legs"]
    assert [f["destination_iata"] for f in legs] == ["LHR", "AMS", "CDG"]
    query["via_airport"] = ["AMS", "LHR"]
    reversed_route = client.post(endpoint, json=query).json()
    assert not reversed_route["items"] and reversed_route["diagnostics"]["exhaustive"]
    rebuild = demo[2]["request"] | {"via_airport": ["LHR", "AMS"], "next_airport": "AMS"}
    plan = client.post("/v1/plans/rebuild", json=rebuild).json()
    assert plan["complete"] and plan["legs"] == legs
    rebuild["prefix"] = [
        {"provider": f["provider"], "provider_id": f["provider_id"]} for f in legs[:2]
    ]
    rebuild["next_airport"] = "CDG"
    kept = client.post("/v1/plans/rebuild", json=rebuild).json()
    assert kept["complete"] and kept["legs"][:2] == legs[:2]


def test_via_list_counts_both_sides_of_permitted_ground_change(client):
    query = request(via_airport=["LHR", "LGW", "AMS"])
    response = client.post("/v1/itineraries/search/local", json=query)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["items"]
    assert all(item["connections"][0]["airport_change"] for item in result["items"])
    query["via_airport"] = ["LGW", "LHR", "AMS"]
    reversed_route = client.post("/v1/itineraries/search/local", json=query).json()
    assert not reversed_route["items"] and reversed_route["diagnostics"]["exhaustive"]


@pytest.mark.parametrize("vias", [["LHR", "lhr"], ["AMS", "YYZ"], ["LHR", "CDG"], ["AMS", 42]])
def test_bad_via_lists_return_readable_validation_errors(client, vias):
    response = client.post("/v1/itineraries/search/local", json=request(via_airport=vias))
    assert response.status_code == 422, response.text
    assert response.json()["detail"]


def test_unknown_via_list_needs_no_timezone_metadata(client):
    result = client.post("/v1/itineraries/search/local", json=request(via_airport=["LHR", "XYZ"]))
    assert result.status_code == 200, result.text
    assert result.json()["items"] == []
    assert result.json()["diagnostics"]["exhaustive"]


def test_via_can_require_departure_side_of_ground_transfer(client):
    for allowed in (False, True):
        result = client.post(
            "/v1/itineraries/search/local",
            json=request(via_airport="LGW", allow_airport_change=allowed),
        ).json()
        assert bool(result["items"]) is allowed
        for item in result["items"]:
            assert any(f["origin_iata"] == "LGW" for f in item["legs"][1:])


@pytest.mark.parametrize(
    "changes",
    [
        {"via_airport": "YYZ"},
        {"via_airport": "CDG"},
        {"via_airport": "AMS", "avoid_airports": ["AMS"]},
        {"via_airport": "AMS", "max_legs": 1},
        {"via_airport": "Amsterdam"},
    ],
)
def test_conflicting_via_request_is_422(client, changes):
    assert client.post("/v1/itineraries/search/local", json=request(**changes)).status_code == 422


def test_unlisted_airport_codes_have_honest_results(client):
    local = client.post("/v1/itineraries/search/local", json=request(origin="ABC"))
    assert local.status_code == 422
    assert local.json()["detail"]["code"] == "airport_metadata_missing"
    utc = client.post(
        "/v1/itineraries/search",
        json={
            "origin": "ABC",
            "destination": "CDG",
            "departure_start": "2026-10-17T00:00:00Z",
            "departure_end": "2026-10-18T00:00:00Z",
            "horizon_end": "2026-10-21T00:00:00Z",
        },
    )
    assert utc.status_code == 200
    assert utc.json()["items"] == [] and utc.json()["diagnostics"]["exhaustive"]
    via = client.post("/v1/itineraries/search/local", json=request(via_airport="ABC"))
    assert via.status_code == 200 and via.json()["items"] == []


def test_next_airport_filters_before_option_page_and_chosen_validation(client, demo):
    query = demo[2]["request"] | {"next_airport": " cdg ", "option_limit": 1}
    response = client.post("/v1/plans/rebuild", json=query)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["complete"] and len(plan["options"]) == 1
    assert plan["options"][0]["flight"]["destination_iata"] == "CDG"
    assert plan["options_total"] == 1 and not plan["options_truncated"]
    query["chosen_next"] = {"provider": "demo", "provider_id": "lhr-ams-early"}
    denied = client.post("/v1/plans/rebuild", json=query)
    assert denied.status_code == 422
    assert denied.json()["detail"]["code"] == "chosen_next_airport_mismatch"


def test_rebuild_retains_required_via_for_prefix_and_tail(client, demo):
    query = demo[2]["request"] | {"via_airport": "AMS", "next_airport": "AMS"}
    plan = client.post("/v1/plans/rebuild", json=query).json()
    assert plan["complete"]
    assert [f["destination_iata"] for f in plan["legs"]] == ["LHR", "AMS", "CDG"]
    query["prefix"] = [
        {"provider": f["provider"], "provider_id": f["provider_id"]} for f in plan["legs"][:2]
    ]
    query["next_airport"] = "CDG"
    continued = client.post("/v1/plans/rebuild", json=query).json()
    assert continued["complete"] and continued["legs"][:2] == plan["legs"][:2]
    query["prefix"] = demo[2]["request"]["prefix"]
    direct = client.post("/v1/plans/rebuild", json=query).json()
    assert not direct["complete"]
    assert all(not option["complete"] for option in direct["options"])


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


def test_family_fonts_are_real_self_hosted_assets_with_retained_licenses(client):
    stylesheet = client.get("/assets/app.css")
    assert stylesheet.status_code == 200
    faces = re.findall(r"@font-face\s*\{([^}]+)\}", stylesheet.text, re.DOTALL)
    assert len(faces) == 3
    urls, families = set(), set()
    for face in faces:
        family = re.search(r'font-family:\s*"([^"]+)"', face)
        assert family is not None
        families.add(family.group(1))
        assert re.search(r"font-display:\s*swap\s*;", face)
        sources = re.findall(r'url\(["\']?([^"\'()\s]+)', face)
        assert len(sources) == 1
        asset = urljoin("/assets/app.css", sources[0])
        assert not urlsplit(asset).netloc and asset.startswith("/assets/fonts/")
        urls.add(asset)
        response = client.get(asset)
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] == "font/woff2"
        assert response.headers["x-content-type-options"] == "nosniff"
        # A 200 HTML fallback is not a usable font. Check the WOFF2 signature
        # and declared binary length, plus the actual file in this runtime.
        assert response.content[:4] == b"wOF2" and len(response.content) >= 48
        assert int.from_bytes(response.content[8:12], "big") == len(response.content)
        assert response.content == (WEB_ROOT / asset.removeprefix("/assets/")).read_bytes()
        directives = {
            parts[0]: parts[1:]
            for rule in response.headers["content-security-policy"].split(";")
            if (parts := rule.split())
        }
        assert directives.get("font-src", directives["default-src"]) == ["'self'"]
    assert families == {"Manrope", "DM Mono"}
    assert urls == {
        "/assets/fonts/manrope-variable.woff2",
        "/assets/fonts/dm-mono-400.woff2",
        "/assets/fonts/dm-mono-500.woff2",
    }
    assert "@import" not in stylesheet.text
    assert re.search(r'--sans:\s*"Manrope",.*sans-serif', stylesheet.text)
    assert re.search(r'--mono:\s*"DM Mono",.*monospace', stylesheet.text)
    for name in ("Manrope", "DMMono"):
        license_response = client.get(f"/assets/fonts/OFL-{name}.txt")
        assert license_response.status_code == 200
        assert "SIL OPEN FONT LICENSE Version 1.1" in license_response.text
        assert license_response.content == (WEB_ROOT / "fonts" / f"OFL-{name}.txt").read_bytes()


def test_family_favicon_is_local_valid_svg_served_with_browser_headers(client):
    page = client.get("/")
    assert '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml"' in page.text
    response = client.get("/assets/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == "image/svg+xml"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == (WEB_ROOT / "favicon.svg").read_bytes()
    svg = ElementTree.fromstring(response.content)
    assert svg.tag == "{http://www.w3.org/2000/svg}svg"
    x, y, width, height = map(float, svg.attrib["viewBox"].split())
    assert width > 0 and height > 0
    assert list(svg)
    assert "img-src 'self'" in response.headers["content-security-policy"]


def test_family_shell_keeps_accessible_flight_workspace_and_system_theme(client):
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.nodes = []

        def handle_starttag(self, tag, attrs):
            self.nodes.append((tag, dict(attrs)))

    response = client.get("/")
    assert response.status_code == 200
    parsed = Elements()
    parsed.feed(response.text)
    nodes = parsed.nodes
    ids = [attrs["id"] for _, attrs in nodes if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert any(tag == "html" and attrs.get("lang") == "en" for tag, attrs in nodes)
    assert "<title>Flyji · boboji.fyi</title>" in response.text
    assert len([tag for tag, _ in nodes if tag == "main"]) == 1
    assert any(tag == "h1" and attrs.get("id") == "page-title" for tag, attrs in nodes)
    assert any(tag == "form" and attrs.get("id") == "search-form" for tag, attrs in nodes)
    assert any(
        tag == "a" and attrs.get("href") == "#results" and attrs.get("class") == "skip"
        for tag, attrs in nodes
    )
    assert any(
        attrs.get("id") == "results" and attrs.get("aria-labelledby") == "options-heading"
        for _, attrs in nodes
    )
    for label, href in (("Flyji home", "/"), ("boboji.fyi home", "https://boboji.fyi/")):
        assert any(
            tag == "a" and attrs.get("aria-label") == label and attrs.get("href") == href
            for tag, attrs in nodes
        )
    assert any(tag == "nav" and attrs.get("aria-label") == "Flyji tools" for tag, attrs in nodes)
    assert any(
        tag == "aside" and attrs.get("aria-label") == "boboji.fyi tools" for tag, attrs in nodes
    )
    assert any(
        tag == "meta"
        and attrs.get("name") == "color-scheme"
        and attrs.get("content") == "light dark"
        for tag, attrs in nodes
    )
    css = client.get("/assets/app.css").text
    assert "@media (prefers-color-scheme: dark)" in css
    assert "color-scheme: light dark" in css
    # These source checks preserve the agreed system-theme contract. Browser
    # QA still has to establish computed colors, contrast, layout and focus.
    assert "@media (prefers-reduced-motion: reduce)" in css


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
    ready = client.get("/health/ready").json()
    assert ready["status"] == "ready" and ready["flight_count"] == 0
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
    assert response.json()["type"] == "FeatureCollection"
    assert response.json()["features"]


def test_time_resolution_finds_exact_airport_beyond_suggestion_limit(client, demo):
    template = Airport.model_validate(demo[2]["airports"][0])
    suggestions = [
        template.model_copy(
            update={"iata": "".join(code), "name": "YYZ suggestion", "city_id": "000"}
        )
        for code in islice(product(ascii_uppercase, repeat=3), 1001)
    ]
    demo[0].update_airports(suggestions, [])
    response = client.post(
        "/v1/time/resolve", json={"airport": "YYZ", "local_time": "2026-10-18T12:00"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["timezone"] == "America/Toronto"
    assert response.json()["utc"] == "2026-10-18T16:00:00Z"


def test_readiness_does_not_build_catalog_or_quality_statistics(client, demo, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Readiness must not build the full web catalog")

    monkeypatch.setattr(FlightReader, "web_catalog", forbidden)
    result = client.get("/health/ready")
    assert result.status_code == 200
    assert result.json()["flight_count"] == len(demo[2]["flights"])


def test_readiness_rejects_wrong_schema_contract(client, demo):
    with demo[0].connect() as c:
        c.execute("UPDATE schema_metadata SET version=999")
    assert client.get("/health/ready").status_code == 503
