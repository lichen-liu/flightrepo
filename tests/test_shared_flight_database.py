"""Explicit publication must make committed source legs usable by every serving consumer."""

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
from ingestion.publish import publish_day
from ingestion.store import IngestionStore
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
    assert reader.available_dates("AAA", "UTC").dates == [date(2025, 1, 1)]
    assert len(reader.search_itineraries(query()).items) == 1
    metadata = receipt["diagnostics"]["counts"]["metadata"]
    assert metadata["accepted_with_callsign_variations"] == 1
    assert metadata.get("accepted_under_relaxed_review", 0) == (0 if blocked else 1)


@pytest.mark.parametrize("case", ["ground", "estimate", "estimate_gap"])
def test_daily_commit_serves_counts_calendar_routes_lookup_export_and_admin(tmp_path, capsys, case):
    database = tmp_path / "hosted.db"
    data = leg_trace()
    data["timestamp"] += 3600
    airborne_only = case != "ground"
    if case == "estimate_gap":
        for row in data["trace"][3:]:
            row[0] += 7200
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
    if case == "estimate":
        assert not record.blocked_for_search and not record.itinerary_reviewed
        assert record.review_reasons == ["airborne_airport_estimate"]
    if case == "estimate_gap":
        assert record.needs_review and not record.blocked_for_search
        assert reader.list_flights(needs_review=True) == [record]
        app.dependency_overrides[get_store] = lambda: reader
        try:
            with TestClient(app) as client:
                facts = client.get("/v1/catalog").json()["dataset"]
                assert facts["flight_count"] == facts["needs_review_count"] == 1
                assert client.get("/v1/dates", params={"origin": "AAA", "clock": "UTC"}).json()[
                    "dates"
                ] == ["2025-01-01"]
                found = client.post(
                    "/v1/itineraries/search", json=query().model_dump(mode="json")
                ).json()["items"]
                assert len(found) == 1 and found[0]["legs"][0]["quality"]["level"] == "uncertain"
        finally:
            app.dependency_overrides.clear()
        capsys.readouterr()
        admin.run(["--db", str(database), "flights", "--needs-review"])
        assert json.loads(capsys.readouterr().out)[0]["provider_id"] == record.provider_id
        with pytest.raises(ValueError, match="read/query-only"):
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
    staging = database
    database = tmp_path / "serving.db"
    assert publish_day(staging, database, date(2025, 1, 1))["inserted"] == 1
    reader = FlightReader(database, serving_only=True)
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
    with FlightReader(staging).connect() as connection:
        assert connection.execute(
            "SELECT state,inserted_count FROM ingestion_batches WHERE id=?", (receipt["batch_id"],)
        ).fetchone()[:] == ("committed", 1)
    with reader.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ingestion_batches").fetchone()[0] == 0
        assert (
            connection.execute("SELECT 1 FROM sqlite_schema WHERE name='itinerary_legs'").fetchone()
            is None
        )
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
    staging = tmp_path / "itineraries.db"
    serving = tmp_path / "serving.db"
    publish_day(staging, serving, date(2025, 1, 1))
    reader = FlightReader(serving)
    updater = FlightUpdater(serving)
    original = reader.all_records()[0]
    changed = original.model_copy(update={"aircraft_type_code": "B738"})
    updater.update(original, changed)
    run(tmp_path, date(2025, 1, 1), [leg_trace()], refresh_diagnostics=True)
    assert publish_day(staging, serving, date(2025, 1, 1))["protected"] == 1
    assert reader.all_records() == [changed]
    updater.delete(changed.provider, changed.provider_id, expected=changed)
    run(tmp_path, date(2025, 1, 1), [leg_trace()], refresh_diagnostics=True)
    assert publish_day(staging, serving, date(2025, 1, 1))["protected"] == 1
    assert reader.all_records() == []
    assert len(FlightReader(staging).all_records()) == 1
    updater.upsert([original])
    assert publish_day(staging, serving, date(2025, 1, 1))["duplicate"] == 1
    restored = reader.all_records()[0]
    assert restored == original.model_copy(update={"fetched_at": restored.fetched_at})


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
def test_producer_policy_updates_during_staged_parsing(tmp_path, monkeypatch, quota, committed):
    from ingestion.adsblol import extraction

    database = tmp_path / "hosted.db"
    IngestionStore(database).initialize()
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
def test_uncertain_candidates_remain_searchable_and_reviewable(tmp_path, capsys, reason):
    updater = FlightUpdater(tmp_path / "review.db")
    updater.initialize()
    record = observed("review", "AAA", "BBB", 10, 70).model_copy(
        update={"review_reasons": [reason]}
    )
    updater.upsert([record])
    reader = FlightReader(updater.path)
    assert reader.flight("adsblol", "review") == record
    assert reader.available_dates("AAA", "UTC").dates == [date(2025, 1, 1)]
    assert len(reader.search_itineraries(query()).items) == 1
    assert len(search_routes(query(), RoutingSnapshot((record,))).items) == 1
    assert reader.overview()["needs_review_count"] == 1
    admin.run(["--db", str(updater.path), "flight", "approve", "adsblol", "review", "--yes"])
    assert json.loads(capsys.readouterr().out)["approved"] == 1
    approved = reader.flight("adsblol", "review")
    assert approved.review_reasons == [reason] and approved.itinerary_reviewed
    assert reader.search_itineraries(query()).items[0].legs == [approved]
    assert reader.available_dates("AAA", "UTC").dates == [date(2025, 1, 1)]


def test_uncertainty_labels_apply_across_time_bases_without_blocking(tmp_path):
    updater = FlightUpdater(tmp_path / "review.db")
    updater.initialize()
    record = observed("review", "AAA", "BBB", 10, 70).model_copy(
        update={
            "evidence_kind": "inferred_airport_proximity",
            "review_reasons": ["airborne_airport_estimate", "observation_gap"],
            "departure_scheduled": query().departure_start + timedelta(hours=1),
            "arrival_scheduled": query().departure_start + timedelta(hours=2),
        }
    )
    updater.upsert([record])
    reader = FlightReader(updater.path)
    assert reader.web_catalog().dataset.time_bases == {"scheduled": 1, "observed": 1, "actual": 0}
    assert reader.available_dates("AAA", "UTC", "scheduled").dates == [date(2025, 1, 1)]
    assert len(reader.search_itineraries(query(time_basis="scheduled")).items) == 1


def test_implausibly_fast_airport_hop_is_served_with_conflict_warning(tmp_path):
    database = tmp_path / "hosted.db"
    data = leg_trace()
    for row in data["trace"]:
        row[0] /= 4
    receipt, _ = run(tmp_path / "inputs", date(2025, 1, 1), [data], output_db=database)
    reader = FlightReader(database)
    (record,) = reader.all_records()
    assert record.evidence_kind == "inferred_ground_brackets"
    assert record.review_reasons == ["implausible_airport_timing"]
    assert record.quality["level"] == "conflict"
    assert record.aircraft_type_code == "A320" and record.callsign == "UAL123"
    assert reader.available_dates("AAA", "UTC").dates == [date(2025, 1, 1)]
    assert len(reader.search_itineraries(query()).items) == 1
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
    assert not record.blocked_for_search and not record.itinerary_reviewed
