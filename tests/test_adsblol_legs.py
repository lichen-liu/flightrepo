import copy

import pytest
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
        (leg,) = candidates(data, AIRPORTS, rules())
        assert leg.evidence == "airport_proximity_and_time_window"
    assert candidates(leg_trace(), AIRPORTS + [("CCC", 40.001, -70.001)], rules()) == []


def test_configured_gap_limit_still_rejects_missing_continuity():
    data = leg_trace()
    data = leg_trace()
    del data["trace"][3:5]
    assert candidates(data, AIRPORTS, rules(max_gap_seconds=120)) == []


def test_no_type_no_airports_and_military_data_do_not_create_candidates():
    data = leg_trace()
    data["t"] = None
    assert candidates(data, AIRPORTS, rules())[0].aircraft_type_code is None
    data = leg_trace()
    data["dbFlags"] = 1
    assert candidates(data, AIRPORTS, rules()) == []
    assert candidates(leg_trace(), [], rules()) == []


@pytest.mark.parametrize("index", range(7))
def test_reception_gap_marker_does_not_invalidate_newly_received_position(index):
    data = leg_trace()
    data["trace"][index][6] |= 1
    assert candidates(data, AIRPORTS, rules()) == candidates(leg_trace(), AIRPORTS, rules())


def test_reception_gap_markers_do_not_destroy_observed_ground_endpoints():
    data = leg_trace()
    data["trace"][0][6] = data["trace"][-1][6] = 1
    (leg,) = candidates(data, AIRPORTS, rules())
    assert leg.evidence == "ground_brackets_and_nearby_airports"


def test_flagged_trace_still_contains_usable_observations():
    data = leg_trace()
    for row in data["trace"]:
        row[6] |= 1
    start = 1735689600000000
    legs, tail, _ = reduce_movement(
        data, None, AirportLookup(AIRPORTS), rules(), start, start + 86400000000
    )
    assert len(legs) == 1 and tail is None


def test_stale_duplicate_does_not_hide_a_valid_sample_at_the_same_timestamp():
    data = leg_trace()
    stale = copy.deepcopy(data["trace"][3])
    stale[6] |= 1
    data["trace"].insert(3, stale)
    assert candidates(data, AIRPORTS, rules()) == candidates(leg_trace(), AIRPORTS, rules())


@pytest.mark.parametrize("kind", ["E75L", "E75S", "MD11", "A306", "SF34", "C172", None])
def test_no_type_whitelist_and_missing_type_is_retained(kind):
    data = leg_trace()
    data["t"] = kind
    (leg,) = candidates(data, AIRPORTS, rules())
    assert leg.aircraft_type_code == kind
