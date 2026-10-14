from test_adsblol import rules

from ingestion.adsblol.movements import AirportLookup, reduce_movement


def leg_trace():
    return {
        "icao": "a5ea5c",
        "t": "A320",
        "timestamp": 1735689600,
        "trace": [
            [0, 40, -70, "ground", 0, 0, 0, 0, {"flight": "UAL123 "}],
            [10, 40, -70, 500, 150, 0, 0, 0, None],
            [100, 40.5, -70.5, 20000, 400, 0, 0, 0, None],
            [200, 40.5, -70.5, 20000, 400, 0, 0, 0, None],
            [300, 40.5, -70.5, 10000, 300, 0, 0, 0, None],
            [400, 41, -71, 500, 150, 0, 0, 0, None],
            [410, 41, -71, "ground", 0, 0, 0, 0, None],
        ],
    }


AIRPORTS = [("AAA", 40, -70), ("BBB", 41, -71)]


def candidates(data, airports, selected):
    start = 1735689600000000
    return reduce_movement(
        data, None, AirportLookup(airports), selected, start, start + 86400000000
    )[0]


def test_complete_leg_keeps_type_and_inferred_time_bounds_separate_from_schedule():
    (candidate,) = candidates(leg_trace(), AIRPORTS, rules())
    assert candidate.origin_iata == "AAA" and candidate.destination_iata == "BBB"
    assert candidate.aircraft_type_code == "A320"
    assert candidate.callsign == "UAL123"
    assert (candidate.departure_upper - candidate.departure_lower).total_seconds() == 10
    assert (candidate.arrival_upper - candidate.arrival_lower).total_seconds() == 10
    assert candidate.status == "inferred_candidate"
    assert "departure_scheduled" not in candidate.model_dump()


def test_partial_and_ambiguous_legs_are_excluded():
    for removed in (0, -1):
        data = leg_trace()
        del data["trace"][removed]
        assert candidates(data, AIRPORTS, rules()) == []
    assert candidates(leg_trace(), AIRPORTS + [("CCC", 40.001, -70.001)], rules()) == []


def test_coverage_gaps_and_stale_points_cannot_complete_a_leg():
    data = leg_trace()
    data["trace"][3][6] = 1
    assert candidates(data, AIRPORTS, rules()) == []
    data = leg_trace()
    del data["trace"][3:5]
    assert candidates(data, AIRPORTS, rules()) == []


def test_no_type_no_airports_and_military_data_do_not_create_candidates():
    data = leg_trace()
    data["t"] = None
    assert candidates(data, AIRPORTS, rules()) == []
    data = leg_trace()
    data["dbFlags"] = 1
    assert candidates(data, AIRPORTS, rules()) == []
    assert candidates(leg_trace(), [], rules()) == []
