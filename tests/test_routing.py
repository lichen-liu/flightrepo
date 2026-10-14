"""Contract regressions and comparisons with an independent exhaustive oracle."""

import json
import random
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from admin import client as admin
from core.connections import Airport, AirportTransfer, ConnectionWindow, RebuildRequest
from core.itineraries import build_itineraries
from core.models import FlightRecord, FlightStatus
from core.planning import rebuild_from_snapshot
from core.routing.debug import replay, replay_document
from core.routing.models import (
    ENGINE_VERSION,
    IncompleteSearchError,
    RouteSearchRequest,
    RoutingError,
)
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot
from ingestion.updater import FlightUpdater
from server.api import app, get_store
from server.reader import FlightReader

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def flight(identifier, origin, destination, out, in_at, status=FlightStatus.SCHEDULED):
    return FlightRecord(
        provider="test",
        provider_id=str(identifier),
        flight_number="T" + str(identifier),
        origin_iata=origin,
        destination_iata=destination,
        departure_scheduled=BASE + timedelta(minutes=out),
        arrival_scheduled=BASE + timedelta(minutes=in_at),
        status=status,
        fetched_at=BASE,
    )


def query(**updates):
    return RouteSearchRequest.model_validate(
        {
            "origin": "AAA",
            "destination": "EEE",
            "departure_start": BASE,
            "departure_end": BASE + timedelta(days=1),
            "horizon_end": BASE + timedelta(days=2),
            "max_legs": 4,
            "limit": 100,
            "minimum_connection_minutes": 15,
            **updates,
        }
    )


def identity(legs):
    return tuple((f.provider, f.provider_id) for f in legs)


def airport(code, city):
    return Airport(iata=code, name=code, city_id=city, timezone="UTC", latitude=0, longitude=0)


def oracle_visits(legs):
    # Connections happen in this order: arrival, then departure after any transfer.
    # Origin/final destination are not intermediate visits. A same-airport
    # connection is one visit, independent of how production tracks progress.
    visits = []
    for previous, following in zip(legs, legs[1:]):
        visits.append(previous.destination_iata)
        if following.origin_iata != previous.destination_iata:
            visits.append(following.origin_iata)
    return visits


def oracle_via_satisfied(legs, via):
    required = [] if via is None else [via] if isinstance(via, str) else via
    visits = iter(oracle_visits(legs))
    return all(any(visit == code for visit in visits) for code in required)


def oracle_paths(records, request, airports=(), transfers=(), prefix=(), active_window=None):
    # Slow all-record traversal: no production policy, index, session or ranking.
    cities = {a.iata: a.city_id for a in airports}
    rules = {(t.arrival_iata, t.departure_iata): t.minimum_minutes for t in transfers}
    found = []
    avoided = set(getattr(request, "avoid_airports", ()))
    seen = (
        {f.origin_iata for f in prefix} | {f.destination_iata for f in prefix}
        if prefix
        else {request.origin}
    )

    def walk(legs, visited):
        if legs and legs[-1].destination_iata == request.destination:
            if oracle_via_satisfied(legs, request.via_airport):
                found.append(legs)
            return
        if len(legs) >= request.max_legs:
            return
        for f in records:
            if (
                prefix
                and len(legs) == len(prefix)
                and request.next_airport is not None
                and f.destination_iata != request.next_airport
            ):
                continue
            if f.status == FlightStatus.CANCELLED or f.arrival_scheduled > request.horizon_end:
                continue
            if (
                f.destination_iata in visited
                or f.origin_iata in avoided
                or f.destination_iata in avoided
            ):
                continue
            if not legs:
                if (
                    f.origin_iata != request.origin
                    or not request.departure_start <= f.departure_scheduled < request.departure_end
                ):
                    continue
            else:
                previous = legs[-1]
                if active_window is not None:
                    minimum = active_window.minimum_minutes
                    maximum = active_window.maximum_minutes if len(legs) == len(prefix) else None
                    not_before = active_window.not_before if len(legs) == len(prefix) else None
                else:
                    minimum, maximum, not_before = (
                        request.minimum_connection_minutes,
                        request.maximum_connection_minutes,
                        None,
                    )
                if f.origin_iata != previous.destination_iata:
                    if f.origin_iata in visited or not request.allow_airport_change:
                        continue
                    pair = previous.destination_iata, f.origin_iata
                    if (
                        pair not in rules
                        or cities.get(pair[0]) is None
                        or cities[pair[0]] != cities.get(pair[1])
                    ):
                        continue
                    minimum = max(minimum, rules[pair])
                gap = (f.departure_scheduled - previous.arrival_scheduled).total_seconds() / 60
                if (
                    gap < minimum
                    or maximum is not None
                    and gap > maximum
                    or not_before is not None
                    and f.departure_scheduled < not_before
                ):
                    continue
            walk([*legs, f], visited | {f.origin_iata, f.destination_iata})

    walk(list(prefix), seen)
    return found


def oracle_score(legs, ranking):
    count, elapsed = (
        len(legs),
        (legs[-1].arrival_scheduled - legs[0].departure_scheduled).total_seconds(),
    )
    if ranking == "fewest_legs":
        return (
            count,
            elapsed,
            legs[0].departure_scheduled,
            legs[-1].arrival_scheduled,
            identity(legs),
        )
    if ranking == "shortest_elapsed":
        return (
            elapsed,
            count,
            legs[-1].arrival_scheduled,
            legs[0].departure_scheduled,
            identity(legs),
        )
    return (
        legs[-1].arrival_scheduled,
        count,
        elapsed,
        legs[0].departure_scheduled,
        identity(legs),
    )


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize(
    "via",
    [
        None,
        "BBB",
        "CCC",
        "ZZZ",
        [],
        ["BBB", "CCC"],
        ["CCC", "BBB"],
        ["BBB", "CCC", "DDD"],
        ["BBB", "ZZZ"],
    ],
)
def test_search_matches_oracle_and_input_permutations(seed, via, tmp_path):
    rng = random.Random(seed)
    codes = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    airports = [airport(c, "shared" if c in ("BBB", "CCC") else c) for c in codes]
    transfers = [
        AirportTransfer(arrival_iata="BBB", departure_iata="CCC", minimum_minutes=30, source="test")
    ]
    # Guarantee an ordered three-stop route as well as random alternatives.
    records = [
        flight("chain-1", "AAA", "BBB", 40, 50),
        flight("chain-2", "BBB", "CCC", 80, 90),
        flight("chain-3", "CCC", "DDD", 120, 130),
        flight("chain-4", "DDD", "EEE", 160, 180),
    ]
    for i in range(45):
        a, b = rng.sample(codes, 2)
        out = rng.randrange(0, 600)
        records.append(
            flight(
                i,
                a,
                b,
                out,
                out + rng.randrange(1, 90),
                FlightStatus.CANCELLED if i % 11 == 0 else FlightStatus.SCHEDULED,
            )
        )
    updater = FlightUpdater(tmp_path / "f.db")
    updater.initialize()
    updater.upsert(records)
    updater.update_airports(airports, transfers)
    reader = FlightReader(updater.path)
    for ranking in ("fewest_legs", "shortest_elapsed", "earliest_arrival"):
        request = query(
            departure_end=BASE + timedelta(minutes=300),
            horizon_end=BASE + timedelta(minutes=650),
            ranking=ranking,
            limit=3,
            minimum_connection_minutes=0 if seed % 3 == 0 else 15,
            maximum_connection_minutes=None if seed % 2 else 120,
            allow_airport_change=bool(seed % 2),
            via_airport=via,
            avoid_airports=["DDD"] if seed % 4 == 0 and "DDD" not in (via or []) else [],
            trace=True,
            limits={"max_trace_events": 15},
        )
        expected = sorted(
            oracle_paths(records, request, airports, transfers),
            key=lambda p: oracle_score(p, ranking),
        )[:3]
        original = search_routes(
            request, RoutingSnapshot(tuple(records), tuple(airports), tuple(transfers))
        )
        assert [identity(r.legs) for r in original.items] == [identity(p) for p in expected]
        assert original.diagnostics.exhaustive
        stored = reader.search_itineraries(request)
        assert [identity(r.legs) for r in stored.items] == [identity(p) for p in expected]
        assert stored.diagnostics.exhaustive
        rng.shuffle(records)
        rng.shuffle(airports)
        assert (
            search_routes(
                request,
                RoutingSnapshot(tuple(records), tuple(airports), tuple(transfers)),
            )
            == original
        )


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize(
    "via",
    [
        None,
        "BBB",
        "CCC",
        "DDD",
        "ZZZ",
        [],
        ["BBB", "CCC"],
        ["CCC", "BBB"],
        ["BBB", "CCC", "DDD"],
        ["CCC", "DDD"],
        ["BBB", "ZZZ"],
    ],
)
def test_continuations_match_oracle(seed, via, tmp_path):
    rng = random.Random(seed)
    prefix = flight("prefix", "AAA", "BBB", 0, 30)
    codes = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    airports = [airport(c, "shared" if c in ("BBB", "CCC") else c) for c in codes]
    rules = [
        AirportTransfer(arrival_iata="BBB", departure_iata="CCC", minimum_minutes=30, source="test")
    ]
    records = [
        prefix,
        flight("chain-2", "BBB", "CCC", 80, 90),
        flight("chain-3", "CCC", "DDD", 120, 130),
        flight("chain-4", "DDD", "EEE", 160, 180),
    ]
    for i in range(70):
        a, b = rng.sample(codes, 2)
        out = rng.randrange(30, 700)
        records.append(flight(i, a, b, out, out + rng.randrange(1, 60)))
    request = RebuildRequest(
        prefix=[{"provider": "test", "provider_id": "prefix"}],
        destination="EEE",
        max_legs=4,
        horizon_end=BASE + timedelta(days=1),
        allow_airport_change=True,
        via_airport=via,
        next_airport="CCC" if seed % 2 else None,
        window={
            "minimum_minutes": 15,
            "maximum_minutes": 180,
            "not_before": BASE + timedelta(minutes=60),
        },
    )
    expected = oracle_paths(records, request, airports, rules, [prefix], request.window)
    result = rebuild_from_snapshot(
        request, RoutingSnapshot(tuple(records), tuple(airports), tuple(rules))
    )
    updater = FlightUpdater(tmp_path / "f.db")
    updater.initialize()
    updater.upsert(records)
    updater.update_airports(airports, rules)
    stored = FlightReader(updater.path).rebuild(request)
    assert identity(stored.legs) == identity(result.legs) and stored.complete == result.complete
    assert stored.diagnostics.exhaustive
    grouped = {}
    for path in expected:
        grouped.setdefault(identity(path)[1], []).append(path)
    assert {(o.flight.provider, o.flight.provider_id) for o in result.options if o.complete} == set(
        grouped
    )
    for option in result.options:
        tails = grouped.get((option.flight.provider, option.flight.provider_id), [])
        assert option.complete == bool(tails)
        if tails:
            best = min(tails, key=lambda p: (p[-1].arrival_scheduled, len(p), identity(p[2:])))
            assert identity([prefix, option.flight, *option.tail]) == identity(best)
    if expected:
        earliest = min(
            grouped,
            key=lambda key: (
                next(f.departure_scheduled for f in records if (f.provider, f.provider_id) == key),
                key,
            ),
        )
        best = min(
            grouped[earliest],
            key=lambda p: (p[-1].arrival_scheduled, len(p), identity(p[2:])),
        )
        assert identity(result.legs) == identity(best) and result.complete
    else:
        assert result.legs == [prefix] and not result.complete
    assert result.diagnostics.exhaustive


def test_limit_is_not_traversal_cutoff_and_rankings_explicit():
    records = [flight(i, "AAA", "EEE", i * 10, 900 + i * 10) for i in range(10)]
    records += [
        flight("fast", "AAA", "EEE", 150, 180),
        flight("a", "AAA", "BBB", 0, 20),
        flight("b", "BBB", "EEE", 35, 60),
    ]
    snapshot = RoutingSnapshot(tuple(records))
    results = {
        mode: search_routes(query(limit=1, ranking=mode), snapshot)
        for mode in ("fewest_legs", "shortest_elapsed", "earliest_arrival")
    }
    assert identity(results["fewest_legs"].items[0].legs) == (("test", "fast"),)
    assert identity(results["shortest_elapsed"].items[0].legs) == (("test", "fast"),)
    assert identity(results["earliest_arrival"].items[0].legs) == (
        ("test", "a"),
        ("test", "b"),
    )
    assert results["fewest_legs"].diagnostics.stats.routes_found == 12
    assert (
        results["fewest_legs"].diagnostics.result_limit_reached
        and results["fewest_legs"].diagnostics.exhaustive
    )


def test_departure_connection_and_arrival_boundaries():
    records = [
        flight("first", "AAA", "BBB", 0, 60),
        flight("at-end", "AAA", "EEE", 120, 140),
        flight("min", "BBB", "EEE", 75, 100),
        flight("below", "BBB", "EEE", 74.99, 99),
        flight("max", "BBB", "EEE", 90, 110),
        flight("above", "BBB", "EEE", 90.01, 111),
        flight("horizon", "BBB", "EEE", 80, 120),
        flight("past", "BBB", "EEE", 80, 120.01),
    ]
    request = query(
        departure_end=BASE + timedelta(minutes=120),
        horizon_end=BASE + timedelta(minutes=120),
        minimum_connection_minutes=15,
        maximum_connection_minutes=30,
    )
    result = search_routes(request, RoutingSnapshot(tuple(records)))
    assert {r.legs[-1].provider_id for r in result.items} == {"min", "max", "horizon"}


def test_long_flight_connection_outside_initial_window(tmp_path):
    records = [
        flight("long", "AAA", "BBB", 1430, 3600),
        flight("onward", "BBB", "EEE", 3645, 3700),
    ]
    updater = FlightUpdater(tmp_path / "f.db")
    updater.initialize()
    updater.upsert(records)
    reader = FlightReader(updater.path)
    result = build_itineraries(
        reader, "AAA", "EEE", BASE, BASE + timedelta(days=1), max_connection_minutes=60
    )
    assert identity(result[0].legs) == identity(records)
    request = query(
        departure_start="2025-12-31T19:00:00-05:00",
        departure_end="2026-01-01T19:00:00-05:00",
        horizon_end=BASE + timedelta(days=3),
    )
    assert reader.search_itineraries(request).items[0].legs == records


def test_airport_first_considered_for_short_window_can_be_used_later(tmp_path):
    records = [
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("to-d", "BBB", "DDD", 45, 60),
        flight("to-c", "DDD", "CCC", 75, 90),
        flight("final", "CCC", "EEE", 200, 230),
    ]
    airports = [
        airport(c, "city" if c in ("BBB", "CCC") else c)
        for c in ["AAA", "BBB", "CCC", "DDD", "EEE"]
    ]
    rules = [
        AirportTransfer(arrival_iata="BBB", departure_iata="CCC", minimum_minutes=15, source="test")
    ]
    updater = FlightUpdater(tmp_path / "f.db")
    updater.initialize()
    updater.upsert(records)
    updater.update_airports(airports, rules)
    request = RebuildRequest(
        prefix=[{"provider": "test", "provider_id": "prefix"}],
        destination="EEE",
        horizon_end=BASE + timedelta(days=1),
        max_legs=4,
        allow_airport_change=True,
        window={"minimum_minutes": 15, "maximum_minutes": 30},
    )
    result = FlightReader(updater.path).rebuild(request)
    assert result.legs == records and result.complete


def test_ground_transfer_cycle_rejected_in_search_and_prefix():
    records = [
        flight("one", "AAA", "BBB", 0, 30),
        flight("two", "BBB", "CCC", 45, 60),
        flight("three", "BBB", "EEE", 90, 120),
    ]
    airports = [
        airport(c, "city" if c in ("BBB", "CCC") else c) for c in ["AAA", "BBB", "CCC", "EEE"]
    ]
    rules = [
        AirportTransfer(arrival_iata="CCC", departure_iata="BBB", minimum_minutes=15, source="test")
    ]
    result = search_routes(
        query(allow_airport_change=True, trace=True),
        RoutingSnapshot(tuple(records), tuple(airports), tuple(rules)),
    )
    assert all(len(r.legs) == 2 for r in result.items)
    assert any(e.decision == "airport_cycle" for e in result.diagnostics.events)
    request = RebuildRequest(
        prefix=[{"provider": "test", "provider_id": f.provider_id} for f in records],
        destination="EEE",
        horizon_end=BASE + timedelta(days=1),
        allow_airport_change=True,
    )
    with pytest.raises(RoutingError, match="airport_cycle"):
        rebuild_from_snapshot(
            request, RoutingSnapshot(tuple(records), tuple(airports), tuple(rules))
        )


def test_budget_retains_found_route_and_choice_without_claiming_optimality():
    prefix = flight("prefix", "AAA", "BBB", 0, 30)
    first = flight("first", "BBB", "CCC", 45, 60)
    tail = flight("tail", "CCC", "EEE", 75, 100)
    other = flight("other", "CCC", "EEE", 90, 110)
    request = RebuildRequest(
        prefix=[{"provider": "test", "provider_id": "prefix"}],
        destination="EEE",
        max_legs=3,
        chosen_next={"provider": "test", "provider_id": "first"},
        horizon_end=BASE + timedelta(days=1),
        window={"minimum_minutes": 15},
        limits={"max_candidates": 2},
        trace=True,
    )
    result = rebuild_from_snapshot(request, RoutingSnapshot((prefix, first, tail, other)))
    assert result.legs == [prefix, first, tail] and result.complete and result.search_truncated
    assert not result.diagnostics.exhaustive and result.diagnostics.stats.candidates_checked == 2
    assert result.options_total is None


def test_direct_manual_choice_not_charged_twice_and_option_page_keeps_choice():
    prefix = flight("prefix", "AAA", "BBB", 0, 30)
    onward = flight("next", "BBB", "EEE", 75, 100)
    request = RebuildRequest(
        prefix=[{"provider": "test", "provider_id": "prefix"}],
        destination="EEE",
        chosen_next={"provider": "test", "provider_id": "next"},
        horizon_end=BASE + timedelta(days=1),
        limits={"max_candidates": 1},
    )
    result = rebuild_from_snapshot(request, RoutingSnapshot((prefix, onward)))
    assert result.complete and result.diagnostics.exhaustive and result.options_total == 1
    records = [prefix, *[flight(i, "BBB", "EEE", 90 + i, 120 + i) for i in range(8)]]
    request = RebuildRequest(
        prefix=request.prefix,
        destination="EEE",
        chosen_next={"provider": "test", "provider_id": "7"},
        horizon_end=request.horizon_end,
        option_limit=2,
    )
    result = rebuild_from_snapshot(request, RoutingSnapshot(tuple(records)))
    assert (
        result.complete
        and len(result.options) == 2
        and result.options[-1].flight.provider_id == "7"
    )
    assert result.options_total == 8 and result.options_truncated and result.diagnostics.exhaustive


def test_trace_bounded_identified_and_replay_detects_changed_facts():
    snapshot = RoutingSnapshot(tuple(flight(i, "AAA", "EEE", i, 100 + i) for i in range(8)))
    request = query(trace=True, limits={"max_candidates": 5, "max_trace_events": 2})
    result = search_routes(request, snapshot)
    assert not result.diagnostics.exhaustive and len(result.diagnostics.events) == 2
    assert (
        result.diagnostics.stats.trace_events_omitted == 3
        and result.diagnostics.stats.candidates_checked == 5
    )
    document = replay_document("search", request, snapshot, result)
    assert replay(json.loads(json.dumps(document)))["matches_saved_result"]
    document["snapshot"]["flights"][0]["flight_number"] = "CHANGED"
    assert not replay(document)["matches_saved_result"]
    assert search_routes(query(), snapshot).diagnostics.events == []


def test_conflicting_identities_rejected_and_identical_duplicates_deduplicated():
    f = flight("one", "AAA", "EEE", 0, 60)
    with pytest.raises(RoutingError, match="conflicting records"):
        search_routes(
            query(),
            RoutingSnapshot((f, f.model_copy(update={"flight_number": "different"}))),
        )
    with pytest.raises(RoutingError, match="conflicting metadata"):
        RoutingSnapshot((f,), (airport("AAA", "one"), airport("AAA", "two")))
    assert search_routes(query(), RoutingSnapshot((f, f))).items[0].legs == [f]


def test_catalog_and_flights_one_snapshot_during_update(tmp_path, monkeypatch):
    updater = FlightUpdater(tmp_path / "f.db")
    updater.initialize()
    f = flight("one", "AAA", "EEE", 0, 60)
    updater.upsert([f])
    updater.update_airports([airport("AAA", "old")], [])
    reader = FlightReader(updater.path)
    original = reader._catalog

    def update_after_catalog(connection):
        data = original(connection)
        updater.upsert([f.model_copy(update={"status": FlightStatus.CANCELLED})])
        updater.update_airports([airport("AAA", "new")], [])
        return data

    monkeypatch.setattr(reader, "_catalog", update_after_catalog)
    snapshot = reader.routing_snapshot(query())
    fresh = FlightReader(updater.path).routing_snapshot(query())
    assert snapshot.flights == (f,) and snapshot.airports[0].city_id == "old"
    assert (
        not fresh.flights
        and fresh.airports[0].city_id == "new"
        and fresh.snapshot_id != snapshot.snapshot_id
    )


def test_index_api_partial_results_and_naive_times(tmp_path):
    updater = FlightUpdater(tmp_path / "f.db")
    updater.initialize()
    updater.upsert([flight(i, "AAA", "EEE", i, 100 + i) for i in range(5)])
    reader = FlightReader(updater.path)
    with reader.connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM flights WHERE origin_iata=? AND departure_scheduled>=? AND departure_scheduled<? AND status != 'cancelled' ORDER BY departure_scheduled,provider,provider_id LIMIT 10",
            ("AAA", BASE.isoformat(), (BASE + timedelta(days=1)).isoformat()),
        ).fetchall()
        assert any("idx_flights_routing" in str(tuple(row)) for row in plan)
    app.dependency_overrides[get_store] = lambda: reader
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/itineraries/search",
                json=query(limits={"max_records": 1}, trace=True).model_dump(mode="json"),
            )
            assert response.status_code == 200 and response.json()["diagnostics"][
                "termination_reasons"
            ] == ["data_limit"]
            assert not response.json()["diagnostics"]["exhaustive"]
            for endpoint in ("flights", "routes", "itineraries"):
                response = client.get(
                    "/v1/" + endpoint,
                    params={
                        "origin": "AAA",
                        "destination": "EEE",
                        "start": "2026-01-01T00:00:00",
                        "end": "2026-01-02T00:00:00Z",
                    },
                )
                assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_legacy_list_api_refuses_unproven_rankings(tmp_path):
    class PartialReader(FlightReader):
        def search_itineraries(self, request):
            return search_routes(
                request,
                RoutingSnapshot((flight("one", "AAA", "EEE", 0, 60),), data_truncated=True),
            )

    reader = PartialReader(tmp_path / "unused.db")
    with pytest.raises(IncompleteSearchError):
        build_itineraries(reader, "AAA", "EEE", BASE, BASE + timedelta(days=1))
    app.dependency_overrides[get_store] = lambda: reader
    try:
        with TestClient(app) as client:
            schema = client.get("/openapi.json").json()
            assert "/v1/itineraries/search" in schema["paths"]
            assert "409" in schema["paths"]["/v1/itineraries"]["get"]["responses"]
            response = client.get(
                "/v1/itineraries",
                params={
                    "origin": "AAA",
                    "destination": "EEE",
                    "start": BASE.isoformat(),
                    "end": (BASE + timedelta(days=1)).isoformat(),
                },
            )
            assert (
                response.status_code == 409
                and response.json()["detail"]["code"] == "incomplete_search"
            )
    finally:
        app.dependency_overrides.clear()


def test_replay_cli_never_opens_database(tmp_path, monkeypatch, capsys):
    request = query()
    snapshot = RoutingSnapshot((flight("one", "AAA", "EEE", 0, 60),))
    result = search_routes(request, snapshot)
    saved = tmp_path / "replay.json"
    saved.write_text(json.dumps(replay_document("search", request, snapshot, result)))
    monkeypatch.setattr(
        admin,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("DB settings should not be read")),
    )
    admin.run(["replay-routing", str(saved)])
    assert json.loads(capsys.readouterr().out)["matches_saved_result"]


@pytest.mark.parametrize(
    "changes",
    [
        {"origin_iata": "?AA"},
        {"destination_iata": "12A"},
        {"origin_iata": 123},
        {"provider": ""},
        {"provider_id": ""},
    ],
)
def test_invalid_flight_facts_rejected(changes):
    with pytest.raises(ValueError):
        FlightRecord.model_validate({**flight("one", "AAA", "EEE", 0, 60).model_dump(), **changes})


def test_rejected_selection_reports_measured_gap(tmp_path):
    updater = FlightUpdater(tmp_path / "f.db")
    updater.initialize()
    updater.upsert([flight("prefix", "AAA", "BBB", 0, 30), flight("next", "BBB", "EEE", 60, 100)])
    reader = FlightReader(updater.path)
    request = RebuildRequest(
        prefix=[{"provider": "test", "provider_id": "prefix"}],
        destination="EEE",
        chosen_next={"provider": "test", "provider_id": "next"},
        horizon_end=BASE + timedelta(days=1),
    )
    app.dependency_overrides[get_store] = lambda: reader
    try:
        with TestClient(app) as client:
            response = client.post("/v1/plans/rebuild", json=request.model_dump(mode="json"))
            assert response.status_code == 422
            detail = response.json()["detail"]
            assert detail["code"] == "chosen_next_connection_too_short"
            assert (
                detail["context"]["wait_minutes"] == 30
                and detail["context"]["minimum_minutes"] == 45
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "updates",
    [
        {"departure_start": "2026-01-01T00:00:00"},
        {"origin": "?AA"},
        {"origin": "AAA", "destination": "aaa"},
        {"minimum_connection_minutes": 50, "maximum_connection_minutes": 30},
        {"avoid_airports": ["AAA"]},
        {"horizon_end": BASE + timedelta(days=100)},
        {"limits": {"max_candidates": 999999999}},
        {"departure_start": "0001-01-01T00:00:00+14:00"},
    ],
)
def test_invalid_query_rejected(updates):
    with pytest.raises(ValueError):
        query(**updates)


@pytest.mark.parametrize(
    "updates",
    [
        {"via_airport": "AAA"},
        {"via_airport": "EEE"},
        {"via_airport": "BBB", "avoid_airports": ["BBB"]},
        {"via_airport": "BBB", "max_legs": 1},
        {"via_airport": ""},
        {"via_airport": "LON1"},
        {"via_airport": ["BBB", "bbb"]},
        {"via_airport": 123},
    ],
)
def test_required_via_conflicts_and_invalid_codes_rejected(updates):
    with pytest.raises(ValueError):
        query(**updates)


@pytest.mark.parametrize("via", ["BBB", "CCC"])
def test_via_before_top_k_counts_arrival_and_directed_transfer_departure(via):
    records = (
        flight("direct", "AAA", "EEE", 0, 20),
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("tail", "CCC", "EEE", 60, 100),
    )
    airports = (airport("BBB", "city"), airport("CCC", "city"))
    rules = (
        AirportTransfer(
            arrival_iata="BBB", departure_iata="CCC", minimum_minutes=30, source="test"
        ),
    )
    request = query(via_airport=via, allow_airport_change=True, limit=1, trace=True)
    result = search_routes(request, RoutingSnapshot(records, airports, rules))
    expected = oracle_paths(records, request, airports, rules)
    assert [identity(r.legs) for r in result.items] == [identity(p) for p in expected]
    assert identity(result.items[0].legs) == (("test", "prefix"), ("test", "tail"))
    assert result.diagnostics.stats.routes_found == 1
    assert result.diagnostics.stats.rejections["required_via_not_visited"] == 1
    assert not result.diagnostics.result_limit_reached
    assert not search_routes(
        query(via_airport=via, allow_airport_change=False),
        RoutingSnapshot(records, airports, rules),
    ).items


def continuation_request(**updates):
    return RebuildRequest.model_validate(
        {
            "prefix": [{"provider": "test", "provider_id": "prefix"}],
            "destination": "EEE",
            "horizon_end": BASE + timedelta(days=1),
            "window": {"minimum_minutes": 15},
            **updates,
        }
    )


def test_next_airport_filters_before_option_page_and_does_not_filter_tail():
    prefix = flight("prefix", "AAA", "BBB", 0, 30)
    records = (
        prefix,
        flight("earlier", "BBB", "EEE", 45, 60),
        flight("next", "BBB", "CCC", 60, 90),
        flight("tail", "CCC", "EEE", 105, 150),
    )
    request = continuation_request(
        next_airport=" ccc ", via_airport="CCC", option_limit=1, trace=True
    )
    result = rebuild_from_snapshot(request, RoutingSnapshot(records))
    assert request.next_airport == "CCC"
    assert result.legs == [prefix, records[2], records[3]] and result.complete
    assert [o.flight.provider_id for o in result.options] == ["next"]
    assert result.options_total == 1 and not result.options_truncated
    assert result.diagnostics.stats.rejections["next_airport_mismatch"] == 1
    assert any(e.decision == "next_airport_mismatch" for e in result.diagnostics.events)
    assert result.diagnostics.exhaustive


@pytest.mark.parametrize("via", ["BBB", "CCC"])
def test_rebuild_via_can_be_satisfied_by_arrival_or_transfer_departure(via):
    prefix = flight("prefix", "AAA", "BBB", 0, 30)
    transfer = flight("transfer", "CCC", "EEE", 60, 100)
    airports = (airport("BBB", "city"), airport("CCC", "city"))
    rules = (
        AirportTransfer(
            arrival_iata="BBB", departure_iata="CCC", minimum_minutes=30, source="test"
        ),
    )
    request = continuation_request(via_airport=via, next_airport="EEE", allow_airport_change=True)
    result = rebuild_from_snapshot(request, RoutingSnapshot((prefix, transfer), airports, rules))
    assert result.legs == [prefix, transfer] and result.complete
    assert result.connections[0].airport_change


@pytest.mark.parametrize(
    "updates",
    [
        {"via_airport": "EEE"},
        {"via_airport": "BBB", "avoid_airports": ["BBB"]},
        {"via_airport": "BBB", "max_legs": 1},
        {"via_airport": ["BBB", "bbb"]},
        {"via_airport": ""},
        {"next_airport": ["CCC", "DDD"]},
        {"next_airport": 123},
        {"next_airport": ""},
    ],
)
def test_rebuild_rejects_invalid_exact_airport_constraints(updates):
    with pytest.raises(ValueError):
        continuation_request(**updates)


def test_rebuild_rejects_origin_as_via_once_prefix_is_resolved():
    prefix = flight("prefix", "AAA", "BBB", 0, 30)
    with pytest.raises(RoutingError) as error:
        rebuild_from_snapshot(continuation_request(via_airport="AAA"), RoutingSnapshot((prefix,)))
    assert error.value.code == "invalid_via_airport"


def test_rebuild_outstanding_via_overrides_faster_tail_and_prefix_satisfaction():
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("next", "BBB", "CCC", 45, 60),
        flight("fast", "CCC", "EEE", 75, 100),
        flight("via", "CCC", "DDD", 75, 100),
        flight("finish", "DDD", "EEE", 115, 200),
    )
    outstanding = continuation_request(via_airport="DDD", next_airport="CCC", trace=True)
    result = rebuild_from_snapshot(outstanding, RoutingSnapshot(records))
    assert result.legs == [records[0], records[1], records[3], records[4]] and result.complete
    assert result.diagnostics.stats.rejections["required_via_not_visited"] == 1
    satisfied = rebuild_from_snapshot(
        continuation_request(via_airport="BBB", next_airport="CCC"), RoutingSnapshot(records)
    )
    assert satisfied.legs == list(records[:3]) and satisfied.complete


def test_continuation_memo_separates_via_satisfaction_after_paths_merge():
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("missing-via", "BBB", "CCC", 45, 60),
        flight("with-via", "BBB", "DDD", 60, 75),
        flight("shared", "FFF", "GGG", 105, 120),
        flight("finish", "GGG", "EEE", 135, 150),
    )
    airports = tuple(airport(code, "city") for code in ("CCC", "DDD", "FFF"))
    transfers = tuple(
        AirportTransfer(arrival_iata=code, departure_iata="FFF", minimum_minutes=30, source="test")
        for code in ("CCC", "DDD")
    )
    request = continuation_request(via_airport="DDD", allow_airport_change=True)
    result = rebuild_from_snapshot(request, RoutingSnapshot(records, airports, transfers))
    expected = oracle_paths(records, request, airports, transfers, [records[0]], request.window)
    assert [identity(p) for p in expected] == [identity(result.legs)]
    assert result.complete and result.legs == [records[0], records[2], *records[3:]]
    assert [(o.flight.provider_id, o.complete) for o in result.options] == [
        ("missing-via", False),
        ("with-via", True),
    ]


def test_data_limited_snapshot_keeps_via_valid_results_but_not_exhaustive_claim():
    records = (flight("prefix", "AAA", "BBB", 0, 30), flight("next", "BBB", "EEE", 45, 60))
    snapshot = RoutingSnapshot(records, data_truncated=True)
    searched = search_routes(query(via_airport="BBB"), snapshot)
    assert len(searched.items) == 1 and not searched.diagnostics.exhaustive
    assert searched.diagnostics.termination_reasons == ["data_limit"]
    rebuilt = rebuild_from_snapshot(
        continuation_request(via_airport="BBB", next_airport="EEE"), snapshot
    )
    assert rebuilt.complete and rebuilt.legs == list(records)
    assert rebuilt.data_truncated and not rebuilt.diagnostics.exhaustive


def test_unknown_next_and_via_preserve_prefix_and_do_not_claim_complete():
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("next", "BBB", "EEE", 45, 60),
    )
    unknown = rebuild_from_snapshot(
        continuation_request(next_airport="ZZZ"), RoutingSnapshot(records)
    )
    assert unknown.legs == [records[0]] and not unknown.complete and not unknown.options
    assert unknown.options_total == 0 and unknown.diagnostics.exhaustive
    via = rebuild_from_snapshot(
        continuation_request(
            via_airport="ZZZ", chosen_next={"provider": "test", "provider_id": "next"}
        ),
        RoutingSnapshot(records),
    )
    assert via.legs == list(records) and not via.complete
    assert len(via.options) == 1 and not via.options[0].complete
    assert via.diagnostics.stats.rejections["required_via_not_visited"] == 1
    already_final = rebuild_from_snapshot(
        continuation_request(
            prefix=[
                {"provider": "test", "provider_id": "prefix"},
                {"provider": "test", "provider_id": "next"},
            ],
            via_airport="ZZZ",
        ),
        RoutingSnapshot(records),
    )
    assert (
        already_final.legs == list(records)
        and not already_final.complete
        and not already_final.options
    )


def test_chosen_next_destination_mismatch_has_stable_error_and_http_422(tmp_path):
    updater = FlightUpdater(tmp_path / "f.db")
    updater.initialize()
    records = (flight("prefix", "AAA", "BBB", 0, 30), flight("next", "BBB", "EEE", 45, 60))
    updater.upsert(records)
    request = continuation_request(
        next_airport="CCC", chosen_next={"provider": "test", "provider_id": "next"}
    )
    with pytest.raises(RoutingError) as error:
        rebuild_from_snapshot(request, RoutingSnapshot(records))
    assert error.value.code == "chosen_next_airport_mismatch"
    app.dependency_overrides[get_store] = lambda: FlightReader(updater.path)
    try:
        with TestClient(app) as client:
            response = client.post("/v1/plans/rebuild", json=request.model_dump(mode="json"))
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "chosen_next_airport_mismatch"
        assert response.json()["detail"]["context"]["next_airport"] == "CCC"
    finally:
        app.dependency_overrides.clear()


def test_via_and_next_constraints_remain_honest_under_candidate_limit():
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("irrelevant", "BBB", "EEE", 45, 60),
        flight("next", "BBB", "CCC", 60, 90),
        flight("tail", "CCC", "EEE", 105, 150),
    )
    result = rebuild_from_snapshot(
        continuation_request(next_airport="CCC", via_airport="CCC", limits={"max_candidates": 1}),
        RoutingSnapshot(records),
    )
    assert result.legs == [records[0]] and not result.complete and not result.options
    assert (
        result.options_total is None
        and result.search_truncated
        and not result.diagnostics.exhaustive
    )
    chosen = rebuild_from_snapshot(
        continuation_request(
            next_airport="CCC",
            via_airport="CCC",
            limits={"max_candidates": 1},
            chosen_next={"provider": "test", "provider_id": "next"},
        ),
        RoutingSnapshot(records),
    )
    assert chosen.legs == [records[0], records[2]] and not chosen.complete
    assert chosen.options[0].flight == records[2] and chosen.search_truncated
    searched = search_routes(
        query(via_airport="CCC", limits={"max_candidates": 1}), RoutingSnapshot(records)
    )
    assert not searched.items and not searched.diagnostics.exhaustive


def test_replay_keeps_exact_constraints_and_rejects_other_engine():
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("next", "BBB", "EEE", 45, 60),
    )
    snapshot = RoutingSnapshot(records)
    request = query(via_airport=" bbb ")
    result = search_routes(request, snapshot)
    saved = replay_document("search", request, snapshot, result)
    assert saved["engine_version"] == ENGINE_VERSION
    assert replay(saved)["matches_saved_result"]
    with pytest.raises(RoutingError) as error:
        replay({**saved, "engine_version": "unsupported-engine"})
    assert error.value.code == "incompatible_replay"
    rebuild_request = continuation_request(via_airport="BBB", next_airport="EEE")
    rebuilt = rebuild_from_snapshot(rebuild_request, snapshot)
    assert replay(replay_document("rebuild", rebuild_request, snapshot, rebuilt))[
        "matches_saved_result"
    ]


@pytest.mark.parametrize("value", [None, 42, {}, "BBB"])
def test_invalid_avoidance_shape_returns_validation_error(value, tmp_path):
    search = {**query().model_dump(mode="json"), "avoid_airports": value}
    rebuild = {
        "prefix": [{"provider": "test", "provider_id": "prefix"}],
        "destination": "EEE",
        "horizon_end": BASE.isoformat(),
        "avoid_airports": value,
    }
    for model, payload in ((RouteSearchRequest, search), (RebuildRequest, rebuild)):
        with pytest.raises(ValueError, match="list of IATA codes"):
            model.model_validate(payload)
    # Invalid request bodies must be rejected before the absent database is read.
    app.dependency_overrides[get_store] = lambda: FlightReader(tmp_path / "absent.db")
    try:
        with TestClient(app) as client:
            for endpoint, payload in (
                ("/v1/itineraries/search", search),
                ("/v1/plans/rebuild", rebuild),
            ):
                assert client.post(endpoint, json=payload).status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_continuation_utc_overflow_is_validation_error(value, tmp_path):
    with pytest.raises(ValueError, match="represented in UTC"):
        ConnectionWindow(not_before=value)
    payload = {
        "prefix": [{"provider": "test", "provider_id": "prefix"}],
        "destination": "EEE",
        "horizon_end": BASE.isoformat(),
    }
    app.dependency_overrides[get_store] = lambda: FlightReader(tmp_path / "absent.db")
    try:
        with TestClient(app) as client:
            for changes in ({"horizon_end": value}, {"window": {"not_before": value}}):
                with pytest.raises(ValueError, match="represented in UTC"):
                    RebuildRequest.model_validate({**payload, **changes})
                assert (
                    client.post("/v1/plans/rebuild", json={**payload, **changes}).status_code == 422
                )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "field",
    [
        "departure_scheduled",
        "arrival_scheduled",
        "departure_actual",
        "arrival_actual",
        "fetched_at",
    ],
)
@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_flight_utc_overflow_is_validation_error(field, value):
    data = flight("one", "AAA", "EEE", 0, 60).model_dump()
    with pytest.raises(ValueError, match="represented in UTC"):
        FlightRecord.model_validate({**data, field: value})


def test_existing_flight_instance_times_are_revalidated():
    local = BASE.astimezone(timezone(timedelta(hours=-5)))
    record = flight("one", "AAA", "EEE", 0, 60).model_copy(update={"departure_actual": local})
    assert FlightRecord.model_validate(record).departure_actual.tzinfo is UTC
    with pytest.raises(ValueError, match="include a UTC offset"):
        FlightRecord.model_validate(
            record.model_copy(update={"fetched_at": BASE.replace(tzinfo=None)})
        )


@pytest.mark.parametrize(
    "data,code",
    [
        (None, "invalid_replay"),
        ([], "invalid_replay"),
        (
            {"format_version": 1, "engine_version": ENGINE_VERSION, "kind": "search"},
            "invalid_replay",
        ),
        (
            {"format_version": 1, "engine_version": ENGINE_VERSION, "kind": "other"},
            "invalid_replay_kind",
        ),
    ],
)
def test_malformed_replay_has_a_stable_error(data, code):
    with pytest.raises(RoutingError) as caught:
        replay(data)
    assert caught.value.code == code


def test_ordered_oracle_matches_subsequence_not_membership():
    legs = [
        flight("one", "AAA", "BBB", 0, 30),
        flight("two", "CCC", "DDD", 60, 90),
        flight("three", "DDD", "EEE", 105, 120),
    ]
    assert oracle_visits(legs) == ["BBB", "CCC", "DDD"]
    assert oracle_via_satisfied(legs, ["BBB", "DDD"])
    assert not oracle_via_satisfied(legs, ["DDD", "BBB"])
    assert not oracle_via_satisfied(legs, ["BBB", "BBB"])
    assert not oracle_via_satisfied(legs, ["AAA"])
    assert not oracle_via_satisfied(legs, ["EEE"])


@pytest.mark.parametrize("via", [None, [], " bbb ", [" bbb "], [" bbb ", "ccc"]])
def test_via_shapes_normalize_without_changing_scalar_response(via):
    normalized = (
        None
        if via is None
        else "BBB"
        if isinstance(via, str)
        else [code.strip().upper() for code in via]
    )
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("middle", "BBB", "CCC", 45, 60),
        flight("last", "CCC", "EEE", 75, 90),
    )
    snapshot = RoutingSnapshot(records)
    searched = search_routes(query(via_airport=via), snapshot)
    assert searched.query.via_airport == normalized
    assert searched.query.model_dump(mode="json")["via_airport"] == normalized
    assert searched.items[0].legs == list(records)
    request = continuation_request(via_airport=via)
    assert request.model_dump(mode="json")["via_airport"] == normalized
    assert rebuild_from_snapshot(request, snapshot).complete


def test_empty_via_list_allows_single_flight_and_single_leg_final_prefix():
    record = flight("prefix", "AAA", "EEE", 0, 30)
    snapshot = RoutingSnapshot((record,))
    empty = search_routes(query(via_airport=[], max_legs=1), snapshot)
    unconstrained = search_routes(query(max_legs=1), snapshot)
    assert empty.items == unconstrained.items and empty.diagnostics.exhaustive
    rebuilt = rebuild_from_snapshot(continuation_request(via_airport=[], max_legs=1), snapshot)
    assert rebuilt.legs == [record] and rebuilt.complete and not rebuilt.options


@pytest.mark.parametrize(
    "updates",
    [
        {"via_airport": ["BBB", " bbb "]},
        {"via_airport": ["BBB", "EEE"]},
        {"via_airport": ["BBB", "CCC"], "avoid_airports": ["CCC"]},
        {"via_airport": ["BBB", ""]},
        {"via_airport": ["BBB", None]},
        {"via_airport": ["BBB", 123]},
        {"via_airport": ["BBB", ["CCC"]]},
        {"via_airport": ["BBB", "AB1"]},
        {"via_airport": {"first": "BBB"}},
        {"via_airport": ["BBB"], "max_legs": 1},
        {"via_airport": ["BBB", "CCC"], "max_legs": 2},
        {"via_airport": ["BBB", "CCC", "DDD"], "max_legs": 2, "allow_airport_change": True},
        {
            "via_airport": ["BBB", "CCC", "DDD", "FFF", "GGG", "HHH", "III"],
            "allow_airport_change": True,
        },
    ],
)
def test_ordered_via_invalid_elements_conflicts_and_impossible_capacity(updates, tmp_path):
    for factory in (query, continuation_request):
        with pytest.raises(ValueError):
            factory(**updates)
    # The union/list boundary must produce HTTP validation errors, not attempt
    # storage access or leak a Python TypeError into a server error.
    absent = tmp_path / "absent.db"
    app.dependency_overrides[get_store] = lambda: FlightReader(absent)
    try:
        with TestClient(app) as client:
            for endpoint, request in (
                ("/v1/itineraries/search", query()),
                ("/v1/plans/rebuild", continuation_request()),
            ):
                payload = {**request.model_dump(mode="json"), **updates}
                assert client.post(endpoint, json=payload).status_code == 422
        assert not absent.exists()
    finally:
        app.dependency_overrides.clear()


def test_ordered_via_origin_rejected_after_prefix_resolution():
    with pytest.raises(ValueError):
        query(via_airport=["BBB", "AAA"])
    prefix = flight("prefix", "AAA", "BBB", 0, 30)
    request = continuation_request(via_airport=["BBB", "AAA"])
    with pytest.raises(RoutingError) as caught:
        rebuild_from_snapshot(request, RoutingSnapshot((prefix,)))
    assert caught.value.code == "invalid_via_airport"


@pytest.mark.parametrize("ranking", ["fewest_legs", "shortest_elapsed", "earliest_arrival"])
@pytest.mark.parametrize("via", [["BBB", "CCC"], ["CCC", "BBB"]])
def test_ordered_via_filter_precedes_top_k_even_when_reverse_is_faster(ranking, via):
    records = (
        flight("direct", "AAA", "EEE", 0, 10),
        flight("reverse-1", "AAA", "CCC", 0, 20),
        flight("reverse-2", "CCC", "BBB", 35, 50),
        flight("reverse-3", "BBB", "EEE", 65, 80),
        flight("forward-1", "AAA", "BBB", 100, 130),
        flight("forward-2", "BBB", "CCC", 145, 180),
        flight("forward-3", "CCC", "EEE", 195, 240),
    )
    result = search_routes(
        query(via_airport=via, ranking=ranking, limit=1), RoutingSnapshot(records)
    )
    expected = records[4:] if via == ["BBB", "CCC"] else records[1:4]
    assert result.items[0].legs == list(expected)
    assert result.diagnostics.exhaustive and result.diagnostics.stats.routes_found == 1


@pytest.mark.parametrize("via", [["BBB", "CCC"], ["CCC", "BBB"]])
def test_ordered_transfer_visits_arrival_before_departure(via):
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("last", "CCC", "EEE", 60, 100),
    )
    airports = (airport("BBB", "city"), airport("CCC", "city"))
    transfers = (
        AirportTransfer(
            arrival_iata="BBB", departure_iata="CCC", minimum_minutes=30, source="test"
        ),
    )
    snapshot = RoutingSnapshot(records, airports, transfers)
    searched = search_routes(
        query(via_airport=via, max_legs=2, allow_airport_change=True), snapshot
    )
    request = continuation_request(
        via_airport=via,
        max_legs=2,
        allow_airport_change=True,
        chosen_next={"provider": "test", "provider_id": "last"},
    )
    rebuilt = rebuild_from_snapshot(request, snapshot)
    correct_order = via == ["BBB", "CCC"]
    assert bool(searched.items) == correct_order
    assert rebuilt.legs == list(records) and rebuilt.complete == correct_order
    assert rebuilt.options[0].complete == correct_order
    assert oracle_via_satisfied(rebuilt.legs, via) == correct_order
    assert searched.diagnostics.exhaustive and rebuilt.diagnostics.exhaustive


@pytest.mark.parametrize("missing", ["permission", "metadata", "direction", "shared_city"])
def test_ordered_transfer_visits_require_allowed_supported_directed_connection(missing):
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("last", "CCC", "EEE", 60, 100),
    )
    airports = (
        (
            airport("BBB", "city"),
            airport("CCC", "other" if missing == "shared_city" else "city"),
        )
        if missing != "metadata"
        else ()
    )
    arrival, departure = ("CCC", "BBB") if missing == "direction" else ("BBB", "CCC")
    transfers = (
        AirportTransfer(
            arrival_iata=arrival, departure_iata=departure, minimum_minutes=30, source="test"
        ),
    )
    allowed = missing != "permission"
    snapshot = RoutingSnapshot(records, airports, transfers)
    request = query(via_airport=["BBB", "CCC"], max_legs=3, allow_airport_change=allowed)
    assert not oracle_paths(records, request, airports, transfers)
    searched = search_routes(request, snapshot)
    rebuilt = rebuild_from_snapshot(
        continuation_request(via_airport=["BBB", "CCC"], allow_airport_change=allowed), snapshot
    )
    assert not searched.items and searched.diagnostics.exhaustive
    assert rebuilt.legs == [records[0]] and not rebuilt.options and not rebuilt.complete
    assert rebuilt.diagnostics.exhaustive


def test_six_ordered_vias_fit_four_flights_with_three_directed_transfers():
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("second", "CCC", "DDD", 60, 90),
        flight("third", "FFF", "GGG", 120, 150),
        flight("last", "HHH", "EEE", 180, 210),
    )
    pairs = [("BBB", "CCC"), ("DDD", "FFF"), ("GGG", "HHH")]
    airports = tuple(airport(code, str(i)) for i, pair in enumerate(pairs) for code in pair)
    transfers = tuple(
        AirportTransfer(arrival_iata=a, departure_iata=b, minimum_minutes=30, source="test")
        for a, b in pairs
    )
    vias = ["BBB", "CCC", "DDD", "FFF", "GGG", "HHH"]
    snapshot = RoutingSnapshot(records, airports, transfers)
    searched = search_routes(query(via_airport=vias, allow_airport_change=True), snapshot)
    assert searched.items[0].legs == list(records)
    request = continuation_request(
        prefix=[{"provider": "test", "provider_id": f.provider_id} for f in records[:2]],
        via_airport=vias,
        next_airport="GGG",
        allow_airport_change=True,
    )
    rebuilt = rebuild_from_snapshot(request, snapshot)
    assert rebuilt.legs == list(records) and rebuilt.complete
    assert [o.flight.provider_id for o in rebuilt.options] == ["third"]
    assert oracle_visits(rebuilt.legs) == vias


def test_retained_prefix_and_next_choice_advance_ordered_vias_before_tail():
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("retained", "BBB", "CCC", 45, 60),
        flight("fast", "CCC", "EEE", 75, 90),
        flight("next", "CCC", "DDD", 90, 105),
        flight("last", "DDD", "EEE", 120, 150),
    )
    request = continuation_request(
        prefix=[{"provider": "test", "provider_id": f.provider_id} for f in records[:2]],
        via_airport=["BBB", "CCC", "DDD"],
        next_airport="DDD",
        option_limit=1,
        chosen_next={"provider": "test", "provider_id": "next"},
    )
    result = rebuild_from_snapshot(request, RoutingSnapshot(records))
    assert result.legs == [*records[:2], *records[3:]] and result.complete
    assert [o.flight.provider_id for o in result.options] == ["next"]
    assert result.options_total == 1 and result.diagnostics.exhaustive


@pytest.mark.parametrize("prefix_count", [2, 3])
def test_wrong_order_retained_prefix_preserved_without_completion(prefix_count):
    records = (
        flight("prefix", "AAA", "CCC", 0, 30),
        flight("retained", "CCC", "BBB", 45, 60),
        flight("last", "BBB", "EEE", 75, 90),
    )
    request = continuation_request(
        prefix=[{"provider": "test", "provider_id": f.provider_id} for f in records[:prefix_count]],
        via_airport=["BBB", "CCC"],
    )
    result = rebuild_from_snapshot(request, RoutingSnapshot(records))
    assert result.legs == list(records[:prefix_count]) and not result.complete
    assert all(not option.complete for option in result.options)
    assert result.diagnostics.exhaustive


def test_chosen_future_via_keeps_a_dead_end_but_auto_selection_finds_ordered_route():
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("wrong-order", "BBB", "DDD", 45, 60),
        flight("right-order", "BBB", "CCC", 60, 75),
        flight("via", "CCC", "DDD", 90, 105),
        flight("last", "DDD", "EEE", 120, 150),
    )
    snapshot = RoutingSnapshot(records)
    request = continuation_request(via_airport=["BBB", "CCC", "DDD"])
    result = rebuild_from_snapshot(request, snapshot)
    assert result.legs == [records[0], *records[2:]] and result.complete
    assert [(o.flight.provider_id, o.complete) for o in result.options] == [
        ("wrong-order", False),
        ("right-order", True),
    ]
    chosen = rebuild_from_snapshot(
        continuation_request(
            via_airport=request.via_airport,
            chosen_next={"provider": "test", "provider_id": "wrong-order"},
        ),
        snapshot,
    )
    assert chosen.legs == list(records[:2]) and not chosen.complete
    assert chosen.diagnostics.exhaustive


def test_tail_memo_separates_opposite_order_with_identical_seen_airports():
    records = (
        flight("prefix", "AAA", "HHH", 0, 30),
        flight("wrong-order", "CCC", "BBB", 45, 60),
        flight("right-order", "BBB", "CCC", 60, 75),
        flight("shared", "DDD", "FFF", 105, 120),
        flight("last", "FFF", "EEE", 135, 150),
    )
    airports = tuple(airport(code, "city") for code in ["HHH", "BBB", "CCC", "DDD"])
    transfers = tuple(
        AirportTransfer(arrival_iata=a, departure_iata=b, minimum_minutes=15, source="test")
        for a, b in [("HHH", "CCC"), ("HHH", "BBB"), ("BBB", "DDD"), ("CCC", "DDD")]
    )
    request = continuation_request(via_airport=["BBB", "CCC"], allow_airport_change=True)
    expected = oracle_paths(records, request, airports, transfers, [records[0]], request.window)
    result = rebuild_from_snapshot(request, RoutingSnapshot(records, airports, transfers))
    assert [identity(p) for p in expected] == [identity(result.legs)]
    assert result.legs == [records[0], *records[2:]] and result.complete
    assert [(o.flight.provider_id, o.complete) for o in result.options] == [
        ("wrong-order", False),
        ("right-order", True),
    ]


@pytest.mark.parametrize("budget", [1, 2, 3, 4, 5])
def test_ordered_via_candidate_budget_never_returns_unordered_route(budget):
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("direct", "BBB", "EEE", 45, 60),
        flight("next", "BBB", "CCC", 60, 90),
        flight("last", "CCC", "EEE", 105, 150),
        flight("other", "CCC", "EEE", 120, 160),
    )
    request = query(via_airport=["BBB", "CCC"], limits={"max_candidates": budget})
    expected = {identity(p) for p in oracle_paths(records, request)}
    result = search_routes(request, RoutingSnapshot(records))
    assert {identity(item.legs) for item in result.items} <= expected
    assert all(oracle_via_satisfied(item.legs, request.via_airport) for item in result.items)
    if budget < 5:
        assert not result.diagnostics.exhaustive
        assert result.diagnostics.termination_reasons == ["candidate_limit"]
        assert result.diagnostics.stats.candidates_checked == budget
    else:
        assert {identity(item.legs) for item in result.items} == expected
        assert result.diagnostics.exhaustive


@pytest.mark.parametrize("budget,complete", [(2, False), (3, True)])
def test_ordered_chosen_next_budget_retains_choice_and_only_valid_complete_tail(budget, complete):
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("next", "BBB", "CCC", 45, 60),
        flight("via", "CCC", "DDD", 75, 90),
        flight("last", "DDD", "EEE", 105, 120),
        flight("other", "DDD", "EEE", 120, 130),
    )
    request = continuation_request(
        via_airport=["BBB", "CCC", "DDD"],
        limits={"max_candidates": budget},
        chosen_next={"provider": "test", "provider_id": "next"},
    )
    result = rebuild_from_snapshot(request, RoutingSnapshot(records))
    assert result.legs[:2] == list(records[:2]) and result.complete == complete
    assert result.options[0].flight == records[1] and result.options[0].complete == complete
    assert result.search_truncated and not result.diagnostics.exhaustive
    assert result.options_total is None
    if complete:
        assert result.legs == list(records[:4])
        assert oracle_via_satisfied(result.legs, request.via_airport)


def test_ordered_vias_in_data_limited_snapshot_keep_only_valid_partial_claim():
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("next", "BBB", "CCC", 45, 60),
        flight("last", "CCC", "EEE", 75, 90),
    )
    snapshot = RoutingSnapshot(records, data_truncated=True)
    searched = search_routes(query(via_airport=["BBB", "CCC"]), snapshot)
    rebuilt = rebuild_from_snapshot(
        continuation_request(via_airport=["BBB", "CCC"], next_airport="CCC"), snapshot
    )
    assert searched.items[0].legs == list(records)
    assert rebuilt.legs == list(records) and rebuilt.complete and rebuilt.data_truncated
    for result in (searched, rebuilt):
        assert not result.diagnostics.exhaustive
        assert result.diagnostics.termination_reasons == ["data_limit"]


@pytest.mark.parametrize("scope", ["data", "candidates"])
@pytest.mark.parametrize("protected", ["prefix", "chosen_next"])
def test_proven_order_failure_message_survives_partial_search_flags(scope, protected):
    if protected == "prefix":
        records = (
            flight("prefix", "AAA", "CCC", 0, 30),
            flight("direct", "CCC", "EEE", 45, 60),
            flight("other", "CCC", "DDD", 60, 90),
        )
        updates = {"via_airport": ["BBB", "CCC"]}
        retained = [records[0]]
    else:
        records = (
            flight("prefix", "AAA", "BBB", 0, 30),
            flight("wrong-order", "BBB", "DDD", 45, 60),
            flight("other", "BBB", "CCC", 60, 75),
            flight("last", "DDD", "EEE", 75, 90),
        )
        updates = {
            "via_airport": ["BBB", "CCC", "DDD"],
            "chosen_next": {"provider": "test", "provider_id": "wrong-order"},
        }
        retained = list(records[:2])
    if scope == "candidates":
        updates["limits"] = {"max_candidates": 1}
    result = rebuild_from_snapshot(
        continuation_request(**updates), RoutingSnapshot(records, data_truncated=scope == "data")
    )
    assert result.legs == retained and not result.complete
    assert not result.diagnostics.exhaustive
    assert result.diagnostics.termination_reasons == [
        "data_limit" if scope == "data" else "candidate_limit"
    ]
    message = result.message.lower()
    assert "out of order" in message and "earlier" in message and "partial" in message
    assert "complete continuation may" not in message


def test_ordered_via_replay_preserves_list_and_detects_changed_order(tmp_path, monkeypatch, capsys):
    records = (
        flight("prefix", "AAA", "BBB", 0, 30),
        flight("next", "BBB", "CCC", 45, 60),
        flight("last", "CCC", "EEE", 75, 90),
    )
    snapshot = RoutingSnapshot(records)
    requests = [
        query(via_airport=[" bbb ", "ccc"]),
        continuation_request(via_airport=["BBB", "CCC"]),
    ]
    documents = []
    for kind, request, operation in zip(
        ["search", "rebuild"], requests, [search_routes, rebuild_from_snapshot]
    ):
        result = operation(request, snapshot)
        saved = json.loads(json.dumps(replay_document(kind, request, snapshot, result)))
        assert saved["request"]["via_airport"] == ["BBB", "CCC"]
        assert replay(saved)["matches_saved_result"]
        documents.append(saved)
        changed = {**saved, "request": {**saved["request"], "via_airport": ["CCC", "BBB"]}}
        replayed = replay(changed)
        assert not replayed["matches_saved_result"]
        if kind == "search":
            assert not replayed["result"]["items"]
        else:
            assert not replayed["result"]["complete"]
        duplicate = {**saved, "request": {**saved["request"], "via_airport": ["BBB", "bbb"]}}
        with pytest.raises(ValueError):
            replay(duplicate)
    monkeypatch.setattr(
        admin,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("offline replay must not read DB settings")),
    )
    for i, saved in enumerate(documents):
        path = tmp_path / f"ordered-replay-{i}.json"
        path.write_text(json.dumps(saved))
        admin.run(["replay-routing", str(path)])
        assert json.loads(capsys.readouterr().out)["matches_saved_result"]
