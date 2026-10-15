import copy
import csv
from datetime import timedelta

import pytest
from test_adsblol import rules
from test_adsblol_diagnostics import diagnose
from test_adsblol_legs import AIRPORTS, candidates, leg_trace

from ingestion.adsblol.legs import airport_points
from ingestion.adsblol.movements import AirportLookup
from ingestion.adsblol.policy import ExtractionPolicy


@pytest.mark.parametrize("callsign", ["ZZZ123", "N123AB", "", None])
def test_callsign_is_optional_metadata_without_an_operator_whitelist(callsign):
    data = leg_trace()
    data["trace"][0][8] = {"flight": callsign}
    (leg,) = candidates(data, AIRPORTS, rules())
    assert leg.callsign == (callsign or None)
    assert leg.origin_iata == "AAA" and leg.destination_iata == "BBB"


def test_whitelist_configuration_is_rejected_instead_of_silently_restricting_ingestion():
    with pytest.raises(ValueError, match="Extra inputs"):
        ExtractionPolicy(fixed_wing_types=["A320"], callsign_prefixes=["UAL"])


def test_airborne_fallback_can_be_disabled_without_restoring_whitelists():
    data = leg_trace()
    data["trace"] = data["trace"][1:-1]
    assert candidates(data, AIRPORTS, rules(allow_airborne_endpoints=False)) == []
    data = leg_trace()
    data["t"] = None
    data["trace"][0][8] = {"flight": "ZZZ123"}
    (leg,) = candidates(data, AIRPORTS, rules(allow_airborne_endpoints=False))
    assert leg.aircraft_type_code is None and leg.callsign == "ZZZ123"


def test_known_rotorcraft_metadata_is_honored_even_on_a_stale_position():
    data = leg_trace()
    data["t"] = "R44"
    data["trace"][3][6] = 1
    data["trace"][3][8] = {"category": "A7"}
    (legs, _, outcome), counts = diagnose(data)
    assert legs == [] and outcome == "excluded_aircraft"
    assert counts["aircraft_exclusions"] == {"non_fixed_wing_category": 1}


@pytest.mark.parametrize(
    "damage,reason", [("altitude", "missing_altitude"), ("position", "invalid_position")]
)
def test_bad_samples_are_skipped_and_reported_without_destroying_a_valid_leg(damage, reason):
    data = leg_trace()
    if damage == "altitude":
        data["trace"][3][3] = None
    else:
        data["trace"][3][1] = 95
    (legs, _, _), counts = diagnose(data)
    assert len(legs) == 1
    assert counts["ignored_observations"] == {reason: 1}
    assert not counts["interruptions"]
    assert counts["coverage_gaps"] == {"120_to_300_seconds": 1}


def test_long_airborne_gap_does_not_discard_observed_terminal_evidence():
    data = leg_trace()
    for row in data["trace"][3:]:
        row[0] += 7200
    (leg,) = candidates(data, AIRPORTS, rules())
    assert leg.evidence == "ground_brackets_and_nearby_airports"
    assert leg.review_reasons == ["observation_gap"]
    assert leg.arrival_lower - leg.departure_upper > timedelta(hours=2)


@pytest.mark.parametrize(
    "gap,needs_review", [(120, False), (121, False), (1800, False), (1801, True)]
)
def test_reception_gaps_use_the_relaxed_review_threshold(gap, needs_review):
    data = leg_trace()
    for row in data["trace"][3:]:
        row[0] += gap - 100
    (legs, _, _), counts = diagnose(data)
    (leg,) = legs
    assert leg.review_reasons == (["observation_gap"] if needs_review else [])
    assert counts["metadata"].get("accepted_with_observation_gaps", 0) == (gap > 120)


def test_gap_review_threshold_is_configurable_independently_of_parsing_limit():
    data = leg_trace()
    data["trace"][3][0] += 50
    (leg,) = candidates(data, AIRPORTS, rules(review_gap_seconds=120))
    assert leg.review_reasons == ["observation_gap"]


def test_changing_callsigns_are_logged_without_blocking_an_observed_leg():
    data = leg_trace()
    data["trace"][3][8] = {"flight": "ZZZ456"}
    (legs, _, _), counts = diagnose(data)
    (leg,) = legs
    assert leg.callsign == "ZZZ456"
    assert leg.review_reasons == []
    assert counts["metadata"]["accepted_with_callsign_variations"] == 1


def test_relaxed_gap_and_callsign_rules_do_not_clear_airport_estimate_review():
    data = leg_trace()
    data["trace"][3][8] = {"flight": "ZZZ456"}
    for row in data["trace"][3:]:
        row[0] += 600
    data["trace"] = data["trace"][1:-1]
    (leg,) = candidates(data, AIRPORTS, rules())
    assert leg.review_reasons == ["airborne_airport_estimate"]


def test_no_ground_rows_can_produce_separately_labelled_endpoint_estimates():
    data = leg_trace()
    data["trace"] = data["trace"][1:-1]
    (leg,) = candidates(data, AIRPORTS, rules())
    assert leg.evidence == "airport_proximity_and_time_window"
    assert leg.departure_upper - leg.departure_lower == timedelta(minutes=30)
    assert leg.arrival_upper - leg.arrival_lower == timedelta(minutes=30)


def test_airborne_flyover_followed_by_departure_from_area_is_not_an_arrival():
    data = leg_trace()
    data["trace"] = data["trace"][:-1]
    data["trace"].append([500, 42, -72, 15000, 400, 0, 0, 0, None])
    assert candidates(data, AIRPORTS, rules()) == []


def test_first_low_descending_observation_does_not_invent_a_departure_airport():
    data = leg_trace()
    data["trace"] = data["trace"][1:-1]
    for row in data["trace"]:
        row[3] = 1500 - row[0]
    assert candidates(data, AIRPORTS, rules()) == []


def test_airborne_origin_matching_prefers_airport_behind_heading():
    lookup = AirportLookup([("AAA", 40, -70), ("CCC", 40.2, -70)])
    assert lookup.match_result(40.15, -70, track=0, departing=True) == ("AAA", "matched")
    assert lookup.match_result(40.15, -70, track=0, departing=False) == ("CCC", "matched")


def test_airborne_airport_inference_accounts_for_airport_elevation():
    data = leg_trace()
    data["trace"] = data["trace"][1:-1]
    data["trace"][0][3] = data["trace"][-1][3] = 6500
    points = [("AAA", 40, -70, 6000), ("BBB", 41, -71, 6000)]
    (leg,) = candidates(data, points, rules())
    assert leg.origin_iata == "AAA" and leg.destination_iata == "BBB"


def test_airport_match_selects_nearest_without_rejecting_every_multi_airport_radius():
    lookup = AirportLookup([*AIRPORTS, ("CCC", 40.15, -70)])
    assert lookup.match_result(40, -70) == ("AAA", "matched")
    assert lookup.match_result(40.075, -70) == (None, "ambiguous_airport")
    assert AirportLookup(AIRPORTS).match_result(40.1, -70) == ("AAA", "matched")


def test_airport_catalog_includes_cargo_airports_without_scheduled_service(tmp_path):
    path = tmp_path / "airports.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "iata_code",
                "latitude_deg",
                "longitude_deg",
                "scheduled_service",
                "type",
                "elevation_ft",
            ]
        )
        writer.writerow(["AAA", 40, -70, "no", "medium_airport", 6000])
        writer.writerow(["BBB", 41, -71, "yes", "large_airport", 0])
        writer.writerow(["HEL", 40, -70, "no", "heliport", 0])
    assert airport_points(path) == [("AAA", 40.0, -70.0, 6000.0), ("BBB", 41.0, -71.0, 0.0)]


def test_source_new_leg_marker_prevents_merging_two_flights_without_ground_samples():
    data = leg_trace()
    data["trace"] = data["trace"][:-1]
    second = copy.deepcopy(data["trace"][1:])
    for row in second:
        row[0] += 500
        row[1] += 1
        row[2] -= 1
    second[0][6] = 2
    second[0][8] = {"flight": "ZZZ456"}
    data["trace"].extend(second)
    legs = candidates(data, [*AIRPORTS, ("CCC", 42, -72)], rules())
    assert [(leg.origin_iata, leg.destination_iata) for leg in legs] == [
        ("AAA", "BBB"),
        ("BBB", "CCC"),
    ]

    assert legs[1].callsign == "ZZZ456"
