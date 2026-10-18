"""Search scope follows data and user filters; response pages remain traversable."""

from datetime import date, timedelta
from itertools import islice, product
from string import ascii_uppercase

import pytest
from fastapi.testclient import TestClient
from public_helpers import rebuild_payload
from test_routing import BASE, continuation_request, flight, identity, query

from core.connections import Airport, AirportTransfer
from core.planning import rebuild_from_snapshot
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot
from ingestion.updater import FlightUpdater
from server.api import app, get_store
from server.reader import FlightReader
from server.web_models import LocalRouteSearchRequest


@pytest.fixture
def database(tmp_path):
    writer = FlightUpdater(tmp_path / "scope.db")
    writer.initialize()
    reader = FlightReader(writer.path)
    app.dependency_overrides[get_store] = lambda: reader
    try:
        with TestClient(app) as client:
            yield writer, reader, client
    finally:
        app.dependency_overrides.clear()


def test_long_journey_calendar_and_default_search_share_available_data(database):
    writer, reader, client = database
    writer.upsert(
        [
            flight("first", "AAA", "BBB", 60, 120),
            flight("last", "BBB", "EEE", 400 * 1440, 400 * 1440 + 120),
        ]
    )
    availability = client.get(
        "/v1/search-availability",
        params={
            "origin": "AAA",
            "destination": "EEE",
            "clock": "UTC",
            "departure_date": str(BASE.date()),
        },
    ).json()
    assert availability["dates"] == [str(BASE.date())]
    body = query().model_dump(mode="json")
    body.pop("horizon_end")
    response = client.post("/v1/itineraries/search", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["items"]) == 1 and data["diagnostics"]["exhaustive"]
    assert data["query"]["horizon_end"] == availability["arrival_end"]
    assert data["query"]["maximum_connection_minutes"] is None
    bounded = client.get(
        "/v1/search-availability",
        params={
            "origin": "AAA",
            "destination": "EEE",
            "clock": "UTC",
            "onward_days": 7,
        },
    ).json()
    assert bounded["dates"] == []
    body["horizon_end"] = (BASE + timedelta(days=7)).isoformat()
    assert client.post("/v1/itineraries/search", json=body).json()["items"] == []
    assert reader.routing_snapshot(query(horizon_end=None)).flights


def test_local_calendar_dates_across_dst_and_long_ranges():
    airports = (Airport(iata="AAA", timezone="America/Toronto"),)
    for last in (date(2026, 11, 19), date(2028, 11, 19)):
        request = LocalRouteSearchRequest(
            origin="AAA",
            destination="EEE",
            departure_date=date(2026, 10, 20),
            departure_last_date=last,
            onward_days=400,
        )
        result = request.utc_request(airports)
        assert result.departure_end > result.departure_start + timedelta(days=31)
        assert result.horizon_end == result.departure_end + timedelta(days=400)


def test_seven_vias_and_many_avoided_airports_route_and_continue():
    codes = ["AA" + c for c in "ABCDEFGHI"]
    records = tuple(
        flight(str(i), a, b, i * 60, i * 60 + 30) for i, (a, b) in enumerate(zip(codes, codes[1:]))
    )
    avoided = ["B" + "".join(c) for c in islice(product(ascii_uppercase, repeat=2), 100)]
    request = query(
        origin=codes[0],
        destination=codes[-1],
        via_airport=codes[1:-1],
        avoid_airports=avoided,
        max_legs=None,
        minimum_connection_minutes=15,
    )
    result = search_routes(request, RoutingSnapshot(records))
    assert identity(result.items[0].legs) == identity(records)
    rebuilt = rebuild_from_snapshot(
        continuation_request(
            prefix=[{"provider": "test", "provider_id": "0"}],
            destination=codes[-1],
            via_airport=codes[1:-1],
            avoid_airports=avoided,
        ),
        RoutingSnapshot(records),
    )
    assert rebuilt.complete and identity(rebuilt.legs) == identity(records)


@pytest.mark.parametrize("ranking", ["fewest_legs", "shortest_elapsed", "earliest_arrival"])
def test_ranked_pages_have_no_gaps_or_total_result_ceiling(database, ranking):
    writer, reader, client = database
    records = tuple(flight(str(i), "AAA", "EEE", i, i + 60 + i % 9) for i in range(125))
    writer.upsert(records)
    expected = search_routes(
        query(ranking=ranking, limit=100).model_copy(update={"limit": 125}),
        RoutingSnapshot(records),
    )
    body = query(ranking=ranking, limit=20).model_dump(mode="json")
    seen, revision = [], None
    while True:
        response = client.post("/v1/itineraries/search", json=body)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["diagnostics"]["exhaustive"]
        revision = revision or page["data_revision"]
        assert revision == page["data_revision"]
        seen.extend(item["itinerary_id"] for item in page["items"])
        if page["next_offset"] is None:
            break
        body.update(offset=page["next_offset"], data_revision=revision)
    assert seen == [item.itinerary_id for item in expected.items]
    assert len(set(seen)) == 125
    writer.upsert([flight("new", "AAA", "EEE", 0, 30)])
    stale = client.post("/v1/itineraries/search", json=body)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "data_changed"


def test_continuation_pages_preserve_selection_without_displacing_page_entries(database):
    writer, reader, client = database
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        *(flight(str(i), "BBB", "EEE", 100 + i, 200 + i) for i in range(12)),
    )
    writer.upsert(records)
    request = continuation_request(
        option_limit=3, chosen_next={"provider": "test", "provider_id": "11"}
    )
    body = rebuild_payload(reader, request.model_dump(mode="json"))
    seen = set()
    while True:
        response = client.post("/v1/plans/rebuild", json=body)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["complete"] and page["legs"][-1]["flight_number"] == "T11"
        seen.update(option["flight"]["flight_number"] for option in page["options"])
        if page["next_option_offset"] is None:
            break
        body.update(option_offset=page["next_option_offset"], data_revision=page["data_revision"])
    assert seen == {"T" + str(i) for i in range(12)}


@pytest.mark.parametrize("endpoint", ["airports", "flights", "routes", "itineraries"])
def test_list_api_pages_are_discoverable_and_revision_bound(database, endpoint):
    writer, reader, client = database
    records = [flight(str(i), "AAA", "AA" + c, i, i + 60) for i, c in enumerate("BCDEFGHIJ")]
    writer.upsert(records)
    params = dict(limit=2)
    if endpoint != "airports":
        params.update(start=BASE.isoformat(), end=(BASE + timedelta(days=1)).isoformat())
    if endpoint == "itineraries":
        params.update(origin="AAA", destination="AAB", limit=1)
        writer.upsert([flight("second", "AAA", "AAB", 30, 80)])
    response = client.get("/v1/" + endpoint, params=params)
    assert response.status_code == 200, response.text
    first = response.json()
    assert "next" in response.links
    second = client.get(response.links["next"]["url"])
    assert second.status_code == 200 and second.json() != first
    writer.upsert([flight("changed", "AAA", "ZZZ", 1, 30)])
    assert client.get(response.links["next"]["url"]).status_code == 409


def test_airport_pagination_reaches_beyond_previous_1000_ceiling(database):
    writer, _, client = database
    codes = ["".join(code) for code in islice(product(ascii_uppercase, repeat=3), 1105)]
    with writer.connect() as connection:
        connection.executemany("INSERT INTO airports(iata) VALUES(?)", [(code,) for code in codes])
    first = client.get("/v1/airports?limit=1000")
    second = client.get(first.links["next"]["url"])
    assert len(first.json()) == 1000 and len(second.json()) == 105
    assert {a["iata"] for a in first.json() + second.json()} == set(codes)
    assert "next" not in second.links
    with writer.connect() as connection:
        connection.execute("UPDATE airports SET name=?", ("Airport " + codes[-1],))
    lookup = client.get("/v1/airports", params={"q": codes[-1], "limit": 1})
    assert lookup.json()[0]["iata"] == codes[-1]
    assert client.get("/v1/airports?offset=" + str(10**30)).json() == []


def test_connection_allowances_have_no_numeric_ceiling_or_timedelta_overflow(database):
    transfer = AirportTransfer(
        arrival_iata="AAA", departure_iata="BBB", minimum_minutes=5000, source="test"
    )
    assert transfer.minimum_minutes == 5000
    writer, _, client = database
    writer.upsert(
        [
            flight("first", "AAA", "BBB", 0, 30),
            flight("last", "BBB", "EEE", 100_000, 100_060),
        ]
    )
    for minimum, maximum, expected in (
        (5000, 200_000, True),
        (10**30, None, False),
        (0, 10**30, True),
    ):
        body = query(
            horizon_end=None, minimum_connection_minutes=minimum, maximum_connection_minutes=maximum
        ).model_dump(mode="json")
        response = client.post("/v1/itineraries/search", json=body)
        assert response.status_code == 200, response.text
        assert bool(response.json()["items"]) == expected
    legacy = client.get(
        "/v1/itineraries",
        params={
            "origin": "AAA",
            "destination": "EEE",
            "start": BASE.isoformat(),
            "end": (BASE + timedelta(days=1)).isoformat(),
            "min_connection_minutes": 5000,
            "max_connection_minutes": 200_000,
        },
    )
    assert legacy.status_code == 200 and legacy.json()


def test_coverage_accepts_more_than_two_airports(database):
    writer, _, client = database
    writer.upsert([flight("one", "AAA", "BBB", 0, 60), flight("two", "BBB", "EEE", 90, 150)])
    response = client.get(
        "/v1/airport-coverage", params=[("airport", code) for code in ("AAA", "BBB", "EEE")]
    )
    assert response.status_code == 200, response.text
    traffic = {item["airport"]: item for item in response.json()["airports"]}
    assert traffic["AAA"]["outbound"] == traffic["EEE"]["inbound"] == 1
    assert traffic["BBB"]["inbound"] == traffic["BBB"]["outbound"] == 1
