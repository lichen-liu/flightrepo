import copy
import json
import logging
import sqlite3
from datetime import date

import pytest
from test_adsblol import rules
from test_adsblol_extraction import midnight_traces, rows, run
from test_adsblol_legs import AIRPORTS, leg_trace

from ingestion.adsblol import extraction
from ingestion.adsblol.diagnostics import Diagnostics
from ingestion.adsblol.movements import AirportLookup, LeadingMovement, reduce_movement
from ingestion.adsblol.pipeline import extraction_status


def diagnose(data, airports=AIRPORTS):
    diagnostics = Diagnostics()
    start = 1735689600000000
    result = reduce_movement(
        data,
        None,
        AirportLookup(airports),
        rules(),
        start,
        start + 86400000000,
        leading=LeadingMovement(aircraft_type_code="A320"),
        diagnostics=diagnostics,
    )
    return result, diagnostics.summary()["counts"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"icao": "not-icao"}, "invalid_identity"),
        ({"dbFlags": 1}, "military"),
    ],
)
def test_aircraft_exclusions_have_one_primary_reason_and_no_invented_flight_counts(change, reason):
    data = leg_trace()
    data.update(change)
    (legs, _, outcome), counts = diagnose(data)
    assert not legs and outcome == "excluded_aircraft"
    assert counts["aircraft"] == {"examined": 1, "excluded": 1}
    assert counts["aircraft_exclusions"] == {reason: 1}
    assert not counts["segments"] and not counts["observations"]


def test_idle_ground_samples_are_not_rejected_flights():
    data = leg_trace()
    for observation in data["trace"]:
        observation[3] = "ground"
    (legs, _, _), counts = diagnose(data)
    assert not legs and not counts["segments"] and not counts["local_rejections"]
    assert counts["observations"]["ground"] == 7
    assert counts["aircraft"]["without_accepted_local_leg"] == 1


@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing_origin", "origin_unmatched_airport"),
        ("ambiguous_origin", "origin_ambiguous_airport"),
        ("missing_destination", "destination_unmatched_airport"),
        ("same_airport", "same_airport"),
        ("short", "insufficient_airborne_duration"),
    ],
)
def test_landing_rejections_are_disjoint_per_evaluated_segment(case, reason):
    data, airports = leg_trace(), list(AIRPORTS)
    if case == "missing_origin":
        airports = airports[1:]
    elif case == "ambiguous_origin":
        airports.append(("CCC", 40.001, -70))
    elif case == "missing_destination":
        airports = airports[:1]
    elif case == "same_airport":
        data["trace"][-1][1:3] = [40, -70]
    elif case == "missing_callsign":
        data["trace"][0][8] = {}
    elif case == "changing_callsign":
        data["trace"][3][8] = {"flight": "UAL999"}
    elif case == "noncommercial":
        data["trace"][0][8] = {"flight": "ZZZ123"}
    elif case == "short":
        for observation in data["trace"]:
            observation[0] /= 10
    (legs, _, _), counts = diagnose(data, airports)
    assert not legs
    assert counts["segments"] == {"local_evaluated": 1, "local_rejected": 1}
    assert counts["local_rejections"] == {reason: 1}


@pytest.mark.parametrize(
    "case,reason",
    [
        ("gap", "coverage_gap"),
    ],
)
def test_quality_interruptions_count_abandoned_fragments_separately(case, reason):
    data = leg_trace()
    if case == "gap":
        del data["trace"][3:5]
    diagnostics = Diagnostics()
    legs, _, _ = reduce_movement(
        data,
        None,
        AirportLookup(AIRPORTS),
        rules(max_gap_seconds=120),
        1735689600000000,
        1735689600000000 + 86400000000,
        diagnostics=diagnostics,
    )
    counts = diagnostics.summary()["counts"]
    assert not legs
    assert counts["interruptions"][reason] == 1
    assert counts["abandoned_segments"][reason] == 1
    # A following partial landing is another observed fragment, not another real flight.
    assert counts["local_rejections"] == {"missing_origin_ground": 1}
    if reason == "coverage_gap":
        assert counts["coverage_gaps"] == {"120_to_300_seconds": 1}


def test_reception_gap_marker_is_counted_without_abandoning_valid_evidence():
    data = leg_trace()
    data["trace"].insert(3, [150, 40.5, -70.5, 20000, 400, 0, 1, 0, None])
    (legs, _, _), counts = diagnose(data)
    assert len(legs) == 1
    assert not counts["ignored_observations"]
    assert counts["observations"]["reception_gap_markers"] == 1
    assert not counts["interruptions"] and not counts["abandoned_segments"]
    assert not counts["coverage_gaps"]


def test_duplicate_and_out_of_day_observations_are_explicitly_ignored():
    data = leg_trace()
    data["trace"].insert(0, [-10, 40, -70, "ground", 0, 0, 0, 0, None])
    data["trace"].insert(3, copy.deepcopy(data["trace"][2]))
    (legs, _, _), counts = diagnose(data)
    assert len(legs) == 1
    assert counts["ignored_observations"] == {"duplicate_timestamp": 1, "outside_day": 1}
    assert counts["observations"]["examined"] == 9
    assert counts["segments"] == {"local_accepted": 1, "local_evaluated": 1}


def test_overnight_deferral_and_later_completion_are_separate_receipt_events(tmp_path):
    first, second = midnight_traces()
    arrival, _ = run(tmp_path, date(2025, 1, 2), [second])
    assert arrival["diagnostics"]["counts"]["segments"] == {
        "local_deferred_previous_day": 1,
        "local_evaluated": 1,
    }
    assert arrival["diagnostics"]["counts"]["boundary"] == {"missing_previous_tail": 1}
    assert arrival["diagnostics"]["counts"]["local_rejections"] == {}
    departure, _ = run(tmp_path, date(2025, 1, 1), [first])
    counts = departure["diagnostics"]["counts"]
    assert counts["segments"]["local_deferred_next_day_with_origin"] == 1
    assert counts["segments"]["boundary_accepted"] == 1
    assert departure["counts"]["records_inserted"] == 1
    status = extraction_status(tmp_path)
    assert status["records"] == 1
    assert status["diagnostics"]["days_reported"] == 2
    assert status["diagnostics"]["totals"]["counts"]["segments"]["boundary_accepted"] == 1
    assert len(rows(tmp_path, "itinerary_legs")) == 1


def test_failed_day_logs_invalid_record_and_provisional_counts_without_saving_facts(
    tmp_path, caplog
):
    data = leg_trace()
    data["icao"] = "b5ea5c"
    data["trace"][3][0] = -100
    with (
        caplog.at_level(logging.INFO, logger="ingestion.adsblol"),
        pytest.raises(ValueError, match="ordered"),
    ):
        run(tmp_path, date(2025, 1, 1), [leg_trace(), data])
    event = json.loads(caplog.records[-1].message)
    assert event["event"] == "day_failed" and not event["committed"]
    assert event["records_applied"] == 0 and event["counts"]["records_inserted"] == 1
    assert event["counts_provisional"]
    assert event["aircraft_id"] == "b5ea5c" and event["observation_index"] == 3
    assert event["diagnostics"]["counts"]["invalid_records"] == {"unordered_observations": 1}
    assert not rows(tmp_path, "itinerary_legs") and not rows(tmp_path, "extraction_days")
    assert list((tmp_path / "raw").rglob("*.tar"))


def test_failed_cleanup_logs_committed_facts_and_repeat_is_historical(
    tmp_path, monkeypatch, caplog
):
    cleanup = extraction.cleanup_raw

    def fail(*args):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(extraction, "cleanup_raw", fail)
    with (
        caplog.at_level(logging.INFO, logger="ingestion.adsblol"),
        pytest.raises(OSError, match="cleanup failure"),
    ):
        run(tmp_path, date(2025, 1, 1), [leg_trace()])
    event = json.loads(caplog.records[-1].message)
    assert event["committed"] and event["records_applied"] == 1
    assert not event["counts_provisional"] and event["phase"] == "cleanup"
    assert len(rows(tmp_path, "itinerary_legs")) == 1
    monkeypatch.setattr(extraction, "cleanup_raw", cleanup)
    with caplog.at_level(logging.INFO, logger="ingestion.adsblol"):
        receipt, _ = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    event = json.loads(caplog.records[-1].message)
    assert event["counts_historical"] and event["records_applied"] == 0
    assert receipt["diagnostics"]["counts"]["segments"]["local_accepted"] == 1


def test_receipts_survive_raw_cleanup_and_refresh_without_duplicate_legs(tmp_path):
    first, _ = run(tmp_path, date(2025, 1, 1), [leg_trace()])
    assert first["diagnostics"]["counts"]["archive_members"] == {
        "aircraft_trace": 1,
        "examined": 2,
        "ignored_non_trace": 1,
    }
    with sqlite3.connect(tmp_path / "itineraries.db") as connection:
        receipt = json.loads(
            connection.execute("SELECT receipt FROM extraction_days").fetchone()[0]
        )
        receipt.pop("diagnostics")
        connection.execute("UPDATE extraction_days SET receipt=?", (json.dumps(receipt),))
    assert extraction_status(tmp_path)["diagnostics"]["days_missing_diagnostics"] == ["2025-01-01"]
    second, _ = run(tmp_path, date(2025, 1, 1), [leg_trace()], refresh_diagnostics=True)
    assert second["counts"]["records_inserted"] == 0
    assert second["counts"]["records_already_present"] == 1
    assert extraction_status(tmp_path)["diagnostics"]["days_reported"] == 1
    assert not list((tmp_path / "raw").rglob("*.tar"))


def test_invalid_member_shape_aborts_with_a_named_diagnostic(tmp_path, caplog):
    with (
        caplog.at_level(logging.INFO, logger="ingestion.adsblol"),
        pytest.raises(ValueError, match="JSON object"),
    ):
        run(tmp_path, date(2025, 1, 1), [[]])
    event = json.loads(caplog.records[-1].message)
    assert event["phase"] == "archive_scan" and event["records_applied"] == 0
    assert event["diagnostics"]["counts"]["invalid_records"] == {"invalid_member_shape": 1}


def test_large_day_logs_progress_with_provisional_segment_counts(tmp_path, caplog):
    traces = []
    for number in range(1001):
        data = leg_trace()
        data["icao"] = f"{number:06x}"
        traces.append(data)
    with caplog.at_level(logging.INFO, logger="ingestion.adsblol"):
        run(tmp_path, date(2025, 1, 1), traces)
    events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "ingestion.adsblol"
    ]
    progress = next(event for event in events if event["event"] == "day_progress")
    assert not progress["committed"] and progress["counts"]["aircraft_members"] == 1000
    assert progress["counts_provisional"]
    assert events[-1]["event"] == "day_finished" and events[-1]["records_applied"] == 1001
