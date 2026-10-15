"""One committed source leg must be usable by every consumer of the same file."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_adsblol_extraction import run
from test_adsblol_legs import leg_trace

from admin import client as admin
from core.connections import RebuildRequest
from core.disk_quota import set_quota
from core.models import FlightRecord
from core.routing.debug import replay, replay_document
from core.routing.models import RouteSearchRequest
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot
from ingestion.updater import FlightUpdater
from server.api import app, get_store
from server.reader import FlightReader


def query(**changes):
    return RouteSearchRequest.model_validate(
        {
            "origin": "AAA",
            "destination": "BBB",
            "departure_start": "2025-01-01T00:00:00Z",
            "departure_end": "2025-01-02T00:00:00Z",
            "horizon_end": "2025-01-03T00:00:00Z",
            **changes,
        }
    )


@pytest.mark.parametrize("gap", [300, 1801])
def test_relaxed_adsb_gap_and_callsign_policy_reaches_search_and_calendar(tmp_path, gap):
    database = tmp_path / "hosted.db"
    data = leg_trace()
    data["trace"][3][8] = {"flight": "ZZZ456"}
    for row in data["trace"][3:]:
        row[0] += gap - 100
    receipt, _ = run(tmp_path / "inputs", date(2025, 1, 1), [data], output_db=database)
    reader = FlightReader(database)
    (record,) = reader.all_records()
    blocked = gap > 1800
    assert record.callsign == "ZZZ456" and record.aircraft_type_code == "A320"
    assert record.needs_review == blocked
    assert reader.available_dates("AAA", "UTC").dates == ([] if blocked else [date(2025, 1, 1)])
    assert len(reader.search_itineraries(query()).items) == (0 if blocked else 1)
    metadata = receipt["diagnostics"]["counts"]["metadata"]
    assert metadata["accepted_with_callsign_variations"] == 1
    assert metadata.get("accepted_under_relaxed_review", 0) == (0 if blocked else 1)


@pytest.mark.parametrize("airborne_only", [False, True])
def test_daily_commit_serves_counts_calendar_routes_lookup_export_and_admin(
    tmp_path, capsys, airborne_only
):
    database = tmp_path / "hosted.db"
    data = leg_trace()
    data["timestamp"] += 3600
    if airborne_only:
        data["trace"] = data["trace"][1:-1]
        data["trace"][0][8] = {"flight": "UAL123"}
    receipt, _ = run(tmp_path / "inputs", date(2025, 1, 1), [data], output_db=database)
    reader = FlightReader(database)
    record = reader.all_records()[0]
    assert reader.stats()["flights"] == reader.overview()["flights"] == 1
    assert record.provider == "adsblol" and record.callsign == "UAL123"
    assert record.aircraft_type_code == "A320"
    assert record.evidence_kind == (
        "inferred_airport_proximity" if airborne_only else "inferred_ground_brackets"
    )
    assert record.flight_number is None and record.departure_scheduled is None
    assert reader.flight(record.provider, record.provider_id) == record
    if airborne_only:
        assert reader.list_flights(needs_review=True) == [record]
        assert reader.available_dates("AAA", "UTC").dates == []
        assert reader.search_itineraries(query()).items == []
        assert reader.search(query().departure_start, query().departure_end) == []
        assert reader.routes(query().departure_start, query().departure_end) == []
        assert search_routes(query(), RoutingSnapshot((record,))).items == []
        assert reader.web_catalog().dataset.flight_count == 0
        assert reader.web_catalog().dataset.total_record_count == 1
        assert reader.web_catalog().dataset.needs_review_count == 1
        assert reader.overview()["needs_review_count"] == 1
        request = RebuildRequest(
            prefix=[{"provider": record.provider, "provider_id": record.provider_id}],
            destination="BBB",
            horizon_end=query().horizon_end,
            time_basis="observed",
        )
        with pytest.raises(ValueError, match="airports_need_review"):
            reader.rebuild(request)
        app.dependency_overrides[get_store] = lambda: reader
        try:
            with TestClient(app) as client:
                facts = client.get("/v1/catalog").json()["dataset"]
                assert facts["flight_count"] == 0 and facts["needs_review_count"] == 1
                assert (
                    client.get("/v1/dates", params={"origin": "AAA", "clock": "UTC"}).json()[
                        "dates"
                    ]
                    == []
                )
                assert (
                    client.post(
                        "/v1/itineraries/search", json=query().model_dump(mode="json")
                    ).json()["items"]
                    == []
                )
        finally:
            app.dependency_overrides.clear()
        capsys.readouterr()
        admin.run(["--db", str(database), "flights", "--needs-review"])
        assert json.loads(capsys.readouterr().out)[0]["provider_id"] == record.provider_id
        admin.run(
            [
                "--db",
                str(database),
                "flight",
                "approve",
                record.provider,
                record.provider_id,
                "--yes",
            ]
        )
        record = reader.flight(record.provider, record.provider_id)
        assert record.itinerary_reviewed and not record.needs_review
        assert record.evidence_kind == "inferred_airport_proximity"
        assert reader.list_flights(needs_review=True) == []
        assert reader.web_catalog().dataset.needs_review_count == 0
        repeated, _ = run(
            tmp_path / "inputs",
            date(2025, 1, 1),
            [data],
            output_db=database,
            refresh_diagnostics=True,
        )
        assert repeated["counts"]["records_protected"] == 1
        assert reader.flight(record.provider, record.provider_id) == record
    assert reader.available_dates("AAA", "UTC").dates == [date(2025, 1, 1)]
    catalog = reader.web_catalog()
    assert catalog.dataset.default_time_basis == "observed"
    assert catalog.dataset.time_bases == {"scheduled": 0, "observed": 1, "actual": 0}
    assert any(s["license_expression"] == "ODbL-1.0" for s in catalog.dataset.sources)
    assert len(reader.search(query().departure_start, query().departure_end)) == 1
    assert len(reader.routes(query().departure_start, query().departure_end)) == 1
    result = reader.search_itineraries(query())
    assert len(result.items) == 1 and result.items[0].legs == [record]
    assert not reader.search_itineraries(query(time_basis="scheduled")).items
    request = RebuildRequest(
        prefix=[{"provider": record.provider, "provider_id": record.provider_id}],
        destination="BBB",
        horizon_end=query().horizon_end,
        time_basis="observed",
    )
    assert reader.rebuild(request).complete
    snapshot = reader.routing_snapshot(query())
    assert replay(replay_document("search", query(), snapshot, result))["matches_saved_result"]
    with reader.connect() as connection:
        assert (
            connection.execute(
                "SELECT type FROM sqlite_master WHERE name='itinerary_legs'"
            ).fetchone()[0]
            == "view"
        )
        assert connection.execute(
            "SELECT state,inserted_count FROM ingestion_batches WHERE id=?", (receipt["batch_id"],)
        ).fetchone()[:] == ("committed", 1)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    app.dependency_overrides[get_store] = lambda: reader
    try:
        with TestClient(app) as client:
            assert client.get("/v1/catalog").json()["dataset"]["flight_count"] == 1
            assert client.get("/v1/dates", params={"origin": "AAA", "clock": "UTC"}).json()[
                "dates"
            ] == ["2025-01-01"]
            assert (
                client.post("/v1/itineraries/search", json=query().model_dump(mode="json")).json()[
                    "items"
                ][0]["legs"][0]["aircraft_type_code"]
                == "A320"
            )
    finally:
        app.dependency_overrides.clear()
    capsys.readouterr()
    admin.run(["--db", str(database), "overview", "--json"])
    assert json.loads(capsys.readouterr().out)["flights"] == 1
    exported = tmp_path / "export.json"
    admin.run(["--db", str(database), "export-json", str(exported)])
    assert json.loads(exported.read_text())[0]["callsign"] == "UAL123"


def test_reprocessing_respects_edits_deletes_and_stable_identity(tmp_path):
    run(tmp_path, date(2025, 1, 1), [leg_trace()])
    reader = FlightReader(tmp_path / "itineraries.db")
    updater = FlightUpdater(reader.path)
    original = reader.all_records()[0]
    changed = original.model_copy(update={"aircraft_type_code": "B738"})
    updater.update(original, changed)
    receipt, _ = run(tmp_path, date(2025, 1, 1), [leg_trace()], refresh_diagnostics=True)
    assert receipt["counts"]["records_protected"] == 1
    assert reader.all_records() == [changed]
    assert updater.delete(changed.provider, changed.provider_id, expected=changed) == 1
    run(tmp_path, date(2025, 1, 1), [leg_trace()], refresh_diagnostics=True)
    assert reader.all_records() == []
    updater.upsert([original])
    assert reader.all_records() == [original]
    run(tmp_path, date(2025, 1, 1), [leg_trace()], refresh_diagnostics=True)
    with reader.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM flight_legs").fetchone()[0] == 1
        assert connection.execute("SELECT revision FROM flight_legs").fetchone()[0] == 1


def observed(key, origin, destination, dep, arr):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return FlightRecord(
        provider="adsblol",
        provider_id=key,
        callsign="UAL123",
        origin_iata=origin,
        destination_iata=destination,
        aircraft_type_code="A320",
        evidence_kind="inferred_ground_brackets",
        departure_observed_lower=start + timedelta(minutes=dep),
        departure_observed_upper=start + timedelta(minutes=dep + 2),
        arrival_observed_lower=start + timedelta(minutes=arr),
        arrival_observed_upper=start + timedelta(minutes=arr + 2),
        fetched_at=start,
    )


@pytest.mark.parametrize(
    "departure,accepted", [(106, False), (107, True), (118, True), (119, False)]
)
def test_connection_uses_both_time_bounds(departure, accepted):
    first = observed("one", "AAA", "BBB", 0, 60)
    second = observed("two", "BBB", "CCC", departure, 200)
    request = query(
        destination="CCC",
        minimum_connection_minutes=45,
        maximum_connection_minutes=60,
        time_basis="observed",
    )
    result = search_routes(request, RoutingSnapshot((first, second)))
    assert bool(result.items) == accepted


def test_incomplete_facts_remain_exportable_but_not_searchable(tmp_path):
    updater = FlightUpdater(tmp_path / "partial.db")
    updater.initialize()
    record = FlightRecord(
        provider="licensed",
        provider_id="partial",
        origin_iata="AAA",
        destination_iata="BBB",
        arrival_scheduled=datetime(2025, 1, 1, tzinfo=UTC),
        fetched_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    updater.upsert([record])
    reader = FlightReader(updater.path)
    assert reader.flight("licensed", "partial") == record
    assert reader.all_records() == [record]
    assert reader.available_dates("AAA", "UTC").dates == []
    assert reader.search_itineraries(query()).items == []


def test_failed_day_has_durable_diagnostics_without_partial_facts(tmp_path):
    bad = leg_trace()
    bad["trace"].reverse()
    with pytest.raises(ValueError):
        run(tmp_path, date(2025, 1, 1), [bad])
    reader = FlightReader(tmp_path / "itineraries.db")
    assert reader.stats()["flights"] == 0
    with reader.connect() as connection:
        state, applied, error, raw = connection.execute(
            "SELECT state,inserted_count,error_summary,diagnostics_json FROM ingestion_batches"
        ).fetchone()
        assert state == "failed" and applied == 0 and error
        assert json.loads(raw)["version"] == 1


@pytest.mark.parametrize("quota,committed", [(100 * 1024**2, True), (0, False)])
def test_admin_quota_updates_during_staged_parsing(tmp_path, monkeypatch, quota, committed):
    from ingestion.adsblol import extraction

    database = tmp_path / "hosted.db"
    FlightUpdater(database).initialize()
    reduce = extraction.reduce_movement
    observations = []

    def update_quota(*args, **kwargs):
        # This uses an independent writer connection while the parser is active.
        set_quota(database, quota)
        observations.append(FlightReader(database).stats()["flights"])
        return reduce(*args, **kwargs)

    monkeypatch.setattr(extraction, "reduce_movement", update_quota)
    if committed:
        run(
            tmp_path / "inputs",
            date(2025, 1, 1),
            [leg_trace()],
            output_db=database,
            policy_db=database,
        )
    else:
        with pytest.raises(OSError, match="quota"):
            run(
                tmp_path / "inputs",
                date(2025, 1, 1),
                [leg_trace()],
                output_db=database,
                policy_db=database,
            )
    assert observations == [0]
    assert FlightReader(database).stats()["flights"] == int(committed)
    assert not list((tmp_path / "inputs").glob(".day-stage-*"))


@pytest.mark.parametrize(
    "reason", ["observation_gap", "callsign_variation", "implausible_airport_timing"]
)
def test_ground_candidates_with_uncertain_continuity_require_review(tmp_path, capsys, reason):
    updater = FlightUpdater(tmp_path / "review.db")
    updater.initialize()
    record = observed("review", "AAA", "BBB", 10, 70).model_copy(
        update={"review_reasons": [reason]}
    )
    updater.upsert([record])
    reader = FlightReader(updater.path)
    assert reader.flight("adsblol", "review") == record
    assert reader.available_dates("AAA", "UTC").dates == []
    assert reader.search_itineraries(query()).items == []
    assert search_routes(query(), RoutingSnapshot((record,))).items == []
    assert reader.overview()["needs_review_count"] == 1
    admin.run(["--db", str(updater.path), "flight", "approve", "adsblol", "review", "--yes"])
    assert json.loads(capsys.readouterr().out)["approved"] == 1
    approved = reader.flight("adsblol", "review")
    assert approved.review_reasons == [reason] and approved.itinerary_reviewed
    assert reader.search_itineraries(query()).items[0].legs == [approved]
    assert reader.available_dates("AAA", "UTC").dates == [date(2025, 1, 1)]


def test_review_gate_applies_even_when_estimate_also_has_schedules(tmp_path):
    updater = FlightUpdater(tmp_path / "review.db")
    updater.initialize()
    record = observed("review", "AAA", "BBB", 10, 70).model_copy(
        update={
            "evidence_kind": "inferred_airport_proximity",
            "departure_scheduled": query().departure_start + timedelta(hours=1),
            "arrival_scheduled": query().departure_start + timedelta(hours=2),
        }
    )
    updater.upsert([record])
    reader = FlightReader(updater.path)
    assert reader.web_catalog().dataset.time_bases == {"scheduled": 0, "observed": 0, "actual": 0}
    assert reader.available_dates("AAA", "UTC", "scheduled").dates == []
    assert reader.search_itineraries(query(time_basis="scheduled")).items == []


def test_implausibly_fast_airport_hop_is_retained_for_review_not_served(tmp_path):
    database = tmp_path / "hosted.db"
    data = leg_trace()
    for row in data["trace"]:
        row[0] /= 4
    receipt, _ = run(tmp_path / "inputs", date(2025, 1, 1), [data], output_db=database)
    reader = FlightReader(database)
    (record,) = reader.all_records()
    assert record.evidence_kind == "inferred_ground_brackets"
    assert record.review_reasons == ["implausible_airport_timing"]
    assert record.aircraft_type_code == "A320" and record.callsign == "UAL123"
    assert reader.available_dates("AAA", "UTC").dates == []
    assert reader.search_itineraries(query()).items == []
    assert receipt["diagnostics"]["counts"]["review_reasons"]["implausible_airport_timing"] == 1
    repeated, _ = run(tmp_path / "inputs", date(2025, 1, 1), [data], output_db=database)
    assert repeated["result"] == "already_committed"
    assert len(reader.all_records()) == 1


def test_airport_timing_check_respects_uncertain_event_windows(tmp_path):
    database = tmp_path / "hosted.db"
    data = leg_trace()
    for row in data["trace"]:
        row[0] /= 4
    data["trace"] = data["trace"][1:-1]
    data["trace"][0][8] = {"flight": "UAL123"}
    run(tmp_path / "inputs", date(2025, 1, 1), [data], output_db=database)
    (record,) = FlightReader(database).all_records()
    assert record.review_reasons == ["airborne_airport_estimate"]
    assert record.needs_review  # Airport estimate still needs review independently.
