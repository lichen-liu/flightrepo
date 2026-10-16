from core.connections import Airport, AirportTransfer, ConnectionWindow
from core.routing.policy import ConnectionPolicy
from server.reachability import TimeEdge, reverse_reachable_flights

MINUTE = 60_000_000


def edge(key, origin, destination, departure, arrival, uncertainty=0):
    return TimeEdge(
        "test",
        key,
        origin,
        destination,
        departure * MINUTE,
        (departure + uncertainty) * MINUTE,
        arrival * MINUTE,
        (arrival + uncertainty) * MINUTE,
    )


def test_a_predecessor_rejected_for_one_interval_can_connect_to_another():
    previous = edge("previous", "AAA", "BBB", 10, 100, uncertainty=20)
    first = edge("first", "BBB", "CCC", 160, 190)
    second = edge("second", "BBB", "CCC", 140, 240)
    found = reverse_reachable_flights(
        [previous, first, second],
        "CCC",
        ConnectionPolicy([], []),
        ConnectionWindow(minimum_minutes=10, maximum_minutes=50),
        0,
    )
    assert found["AAA"] == {("test", "previous")}


def test_reverse_edges_respect_transfer_allowances_and_unbounded_waits():
    airports = [Airport(iata=code, city_id="London") for code in ("LHR", "LGW")]
    transfer = AirportTransfer(
        arrival_iata="LHR", departure_iata="LGW", minimum_minutes=120, source="test"
    )
    flights = [edge("first", "YYZ", "LHR", 10, 100), edge("last", "LGW", "NRT", 220, 300)]
    window = ConnectionWindow(minimum_minutes=45, maximum_minutes=None)
    blocked = reverse_reachable_flights(
        flights, "NRT", ConnectionPolicy(airports, [transfer]), window, 0
    )
    assert "YYZ" not in blocked
    policy = ConnectionPolicy(airports, [transfer], allow_airport_change=True)
    found = reverse_reachable_flights(flights, "NRT", policy, window, 0)
    assert found["YYZ"] == {("test", "first")}
    too_short = [edge("first", "YYZ", "LHR", 10, 101), flights[1]]
    assert "YYZ" not in reverse_reachable_flights(too_short, "NRT", policy, window, 0)


def test_preferred_paths_preserve_departure_bounds_and_exclude_airport_cycles():
    flights = [
        edge("cycle-root", "YYZ", "AAA", 10, 20),
        edge("cycle-back", "AAA", "YYZ", 30, 40),
        edge("late-direct", "YYZ", "NRT", 50, 60),
        edge("good-root", "YYZ", "BBB", 11, 21),
        edge("good-tail", "BBB", "NRT", 31, 41),
    ]
    found = reverse_reachable_flights(
        flights,
        "NRT",
        ConnectionPolicy([], []),
        ConnectionWindow(minimum_minutes=10, maximum_minutes=30),
        0,
    )
    paths = found.preferred_paths("YYZ", 0, 15 * MINUTE, None)
    assert [[f.provider_id for f in path] for path in paths] == [["good-root", "good-tail"]]
    assert not found.preferred_paths("YYZ", 0, 15 * MINUTE, 1)
    assert not found.preferred_paths("YYZ", 0, 15 * MINUTE, None, ("AAA",))


def test_earliest_arrival_bound_can_use_more_legs_than_shortest_suffix():
    flights = [
        edge("first", "YYZ", "AAA", 0, 20),
        edge("direct", "AAA", "NRT", 30, 200),
        edge("via", "AAA", "BBB", 30, 50),
        edge("last", "BBB", "NRT", 60, 100),
    ]
    window = ConnectionWindow(minimum_minutes=10)
    policy = ConnectionPolicy([], [])
    shortest = reverse_reachable_flights(flights, "NRT", policy, window, 0)
    earliest = reverse_reachable_flights(flights, "NRT", policy, window, 0, earliest=True)
    assert shortest.leg_distance["test", "first"] == 2
    assert earliest.arrival_bound["test", "first"] == 100 * MINUTE
