import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from admin import client as admin
from core.connections import Airport, AirportTransfer, RebuildRequest
from core.itinerary_cache import (
    ItineraryCache,
    ItinerarySourceError,
    cache_directory,
    cache_status,
    control,
    set_quota,
    set_source,
)
from core.models import FlightRecord
from core.routing.models import RouteSearchRequest
from ingestion import catalog as catalog_builder
from ingestion.catalog import write_itinerary_catalog
from ingestion.updater import FlightUpdater
from server.api import app, get_store
from server.reader import FlightReader
from server.web_models import LocalRouteSearchRequest


@pytest.fixture
def remote(tmp_path):
    data = json.loads((Path(__file__).parents[1] / "fixtures/connections-demo.json").read_text())
    records = [FlightRecord.model_validate(row) for row in data["flights"]]
    records[0].aircraft_type_code = "B738"
    records.append(
        records[0].model_copy(update={"provider_id": "unreachable", "origin_iata": "XYZ"})
    )
    airports = [Airport.model_validate(row) for row in data["airports"]]
    transfers = [AirportTransfer.model_validate(row) for row in data["transfers"]]
    directory = tmp_path / "source"
    catalog = write_itinerary_catalog(
        records, airports, transfers, directory, "https://itineraries.test"
    )
    requested = []
    state = {"offline": False, "corrupt": False}

    def respond(request):
        requested.append(request.url.path)
        if state["offline"]:
            return httpx.Response(503)
        file = directory / request.url.path.removeprefix("/")
        if not file.is_file():
            return httpx.Response(404)
        content = file.read_bytes()
        if state["corrupt"] and file.name != "catalog.json":
            content = b"x" * len(content)
        return httpx.Response(200, stream=httpx.ByteStream(content))

    transport = httpx.Client(transport=httpx.MockTransport(respond))
    path = tmp_path / "flights.db"
    FlightUpdater(path).initialize()
    set_source(path, "https://itineraries.test/catalog.json")
    cache = ItineraryCache(path, client=transport)
    reader = FlightReader(path, cache=cache)
    yield SimpleNamespace(
        path=path,
        catalog=catalog,
        cache=cache,
        reader=reader,
        records=records,
        airports=airports,
        transfers=transfers,
        requested=requested,
        state=state,
        directory=directory,
    )
    transport.close()


def loaded(remote):
    return {path.stem for path in cache_directory(remote.path).glob("*.sqlite")}


def touch(remote, chunk):
    with remote.cache.access(chunk) as connection:
        return [FlightReader._record(row) for row in connection.execute("SELECT * FROM flights")]


def search_request():
    return RouteSearchRequest(
        origin="YYZ",
        destination="CDG",
        departure_start="2026-10-17T00:00:00Z",
        departure_end="2026-10-18T00:00:00Z",
        horizon_end="2026-10-24T00:00:00Z",
        allow_airport_change=True,
    )


def test_source_builder_compacts_sparse_months_and_splits_busy_windows(
    remote, tmp_path, monkeypatch
):
    monkeypatch.setattr(catalog_builder, "MAX_CHUNK_RECORDS", 2)
    template = remote.records[0]
    flights = []
    for index, departure in enumerate(
        [datetime(2025, 1, day, 12, tzinfo=UTC) for day in (1, 2, 31)]
        + [datetime(2025, 2, 1, 12, tzinfo=UTC)]
    ):
        flights.append(
            template.model_copy(
                update={
                    "provider_id": str(index),
                    "departure_scheduled": departure,
                    "arrival_scheduled": departure + timedelta(hours=2),
                }
            )
        )
    catalog = write_itinerary_catalog(
        flights, remote.airports, remote.transfers, tmp_path / "months", "https://itineraries.test"
    )
    assert [chunk.flight_count for chunk in catalog.chunks] == [2, 1, 1]
    assert catalog.chunks[0].departure_end == catalog.chunks[1].departure_start
    assert catalog.chunks[1].departure_end == datetime(2025, 2, 1, tzinfo=UTC)
    assert catalog.chunks[2].departure_end == datetime(2025, 3, 1, tzinfo=UTC)
    assert [len(chunk.dates_utc) for chunk in catalog.chunks] == [2, 1, 1]


def test_backend_search_fetches_required_connections_and_preserves_results_after_eviction(
    remote, tmp_path
):
    baseline = FlightUpdater(tmp_path / "baseline.db")
    baseline.initialize()
    baseline.update_airports(remote.airports, remote.transfers)
    baseline.upsert(remote.records)
    expected = FlightReader(baseline.path).search_itineraries(search_request())
    result = remote.reader.search_itineraries(search_request())
    assert result == expected
    assert any(leg.aircraft_type_code == "B738" for item in result.items for leg in item.legs)
    assert remote.requested[0] == "/catalog.json"
    assert 0 < len(loaded(remote)) < len(remote.catalog.chunks)
    first_requests = len(remote.requested)
    assert remote.reader.search_itineraries(search_request()) == expected
    assert len(remote.requested) == first_requests
    set_quota(remote.path, 0)
    assert loaded(remote)  # Setter itself does not evict.
    # The next database read retires chunks; catalogue dates still work without fetching them.
    assert remote.reader.available_dates("YYZ").dates
    assert not loaded(remote)
    assert len(remote.requested) == first_requests
    assert remote.reader.search_itineraries(search_request()) == expected
    assert len(remote.requested) > first_requests
    assert not loaded(remote)


def test_quota_change_from_another_process_is_lazy_and_uses_lru(remote):
    remote.cache.catalog()
    set_quota(remote.path, None)
    chunks = remote.catalog.chunks[:3]
    for chunk in chunks:
        touch(remote, chunk)
    touch(remote, chunks[0])
    # Use the durable file floor; a maintenance checkpoint can release transient WAL/SHM.
    permanent = remote.path.stat().st_size
    sizes = [(cache_directory(remote.path) / (c.sha256 + ".sqlite")).stat().st_size for c in chunks]
    quota = permanent + sum(sizes) - sizes[1]
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from core.itinerary_cache import set_quota; set_quota(sys.argv[1],int(sys.argv[2]))",
            str(remote.path),
            str(quota),
        ],
        check=True,
        timeout=10,
    )
    assert len(loaded(remote)) == 3
    # Existing reader object observes the new on-disk policy without a restart.
    remote.reader.stats()
    assert loaded(remote) == {chunks[0].sha256, chunks[2].sha256}
    requests = len(remote.requested)
    touch(remote, chunks[1])
    assert len(remote.requested) == requests + 1
    assert chunks[1].sha256 in loaded(remote)
    assert chunks[2].sha256 not in loaded(remote)


def test_active_chunk_is_not_retired_and_manual_flights_are_protected(remote):
    remote.cache.catalog()
    manual = remote.records[0].model_copy(update={"provider": "manual", "provider_id": "protected"})
    FlightUpdater(remote.path).upsert([manual])
    with remote.cache.access(remote.catalog.chunks[0]) as connection:
        set_quota(remote.path, 0)
        assert cache_status(remote.path)["over_quota_bytes"] > 0
        assert remote.catalog.chunks[0].sha256 in loaded(remote)
        assert connection.execute("SELECT COUNT(*) FROM flights").fetchone()[0] > 0
    assert not loaded(remote)
    assert remote.reader.flight("manual", "protected") == manual
    assert cache_status(remote.path)["used_bytes"] == cache_status(remote.path)["database_bytes"]


def test_download_failure_is_not_an_exhaustive_empty_search_and_never_changes_permanent_rows(
    remote,
):
    remote.cache.catalog()
    remote.state["offline"] = True
    app.dependency_overrides[get_store] = lambda: remote.reader
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/itineraries/search", json=search_request().model_dump(mode="json")
            )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "itinerary_source_unavailable"
        with control(remote.path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM flights").fetchone()[0] == 0
        assert not loaded(remote)
    finally:
        app.dependency_overrides.clear()


def test_corrupt_source_is_not_cached_and_retry_can_succeed(remote):
    remote.cache.catalog()
    remote.state["corrupt"] = True
    with pytest.raises(ItinerarySourceError, match="SHA-256"):
        touch(remote, remote.catalog.chunks[0])
    assert not loaded(remote)
    assert not list(cache_directory(remote.path).glob("*.partial*"))
    remote.state["corrupt"] = False
    assert touch(remote, remote.catalog.chunks[0])


def test_cached_flight_edit_and_delete_survive_redownloads(remote):
    original = remote.reader.search_itineraries(search_request()).items[0].legs[0]
    updater = FlightUpdater(remote.path, cache=remote.cache)
    changed = original.model_copy(update={"flight_number": "UPDATED", "aircraft_type_code": "A320"})
    assert updater.update(original, changed) == 1
    set_quota(remote.path, 0)
    assert remote.reader.flight(changed.provider, changed.provider_id) == changed
    with pytest.raises(ValueError, match="changed"):
        updater.update(original, changed)
    assert updater.delete(changed.provider, changed.provider_id, expected=changed) == 1
    assert all(f.provider_id != changed.provider_id for f in remote.reader.all_records())
    with pytest.raises(ValueError, match="Flight not found"):
        remote.reader.flight(changed.provider, changed.provider_id)
    updater.upsert([changed])
    assert remote.reader.flight(changed.provider, changed.provider_id) == changed


def test_local_search_rebuild_export_and_calendar_work_under_zero_cache_quota(remote):
    set_quota(remote.path, 0)
    result = remote.reader.search_local(
        LocalRouteSearchRequest(
            origin="YYZ", destination="CDG", departure_date="2026-10-17", allow_airport_change=True
        )
    )
    assert result.items
    prefix = result.items[0].legs[:1]
    plan = remote.reader.rebuild(
        RebuildRequest(
            prefix=[{"provider": f.provider, "provider_id": f.provider_id} for f in prefix],
            destination="CDG",
            horizon_end="2026-10-24T00:00:00Z",
            allow_airport_change=True,
        )
    )
    assert plan.options
    assert len(remote.reader.all_records()) == len(remote.records)
    assert remote.reader.routes(search_request().departure_start, search_request().horizon_end)
    catalog = remote.reader.web_catalog()
    assert catalog.dataset.catalogue_flight_count == len(remote.records)
    assert catalog.dataset.flight_count == 0
    assert not loaded(remote)


def test_admin_quota_controls_do_not_download_and_apply_on_next_read(remote, capsys):
    remote.cache.catalog()
    touch(remote, remote.catalog.chunks[0])
    admin.run(["cache", "quota", "0"], database_path=remote.path)
    assert "next database IO" in capsys.readouterr().out
    assert loaded(remote)
    admin.run(["cache", "status"], database_path=remote.path)
    assert not loaded(remote)
    state = json.loads(capsys.readouterr().out)
    assert state["quota_bytes"] == 0 and state["over_quota_bytes"] > 0
    admin.run(["cache", "quota", "2.5GiB"], database_path=remote.path)
    assert json.loads(capsys.readouterr().out)["quota_bytes"] == int(2.5 * 2**30)
    admin.run(["cache", "quota", "off"], database_path=remote.path)
    assert json.loads(capsys.readouterr().out)["quota_bytes"] is None


def test_clear_data_disables_source_and_does_not_repopulate(remote):
    remote.reader.search_itineraries(search_request())
    updater = FlightUpdater(remote.path, cache=remote.cache)
    updater.reset()
    assert remote.reader.all_records() == []
    assert not loaded(remote)
    assert remote.reader.available_dates("YYZ", clock="UTC").dates == []


def test_parallel_access_downloads_the_same_chunk_once(remote):
    from concurrent.futures import ThreadPoolExecutor

    remote.cache.catalog()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(touch, remote, remote.catalog.chunks[0])
        second = executor.submit(touch, remote, remote.catalog.chunks[0])
        assert first.result(timeout=10) == second.result(timeout=10)
    assert remote.requested.count("/" + remote.catalog.chunks[0].sha256 + ".json.gz") == 1


def test_calendar_reconciles_manual_changes_and_deleted_remote_dates(remote):
    original = remote.reader.search_itineraries(search_request()).items[0].legs[0]
    # Delete every departure from YYZ, then force all payloads out of the cache.
    updater = FlightUpdater(remote.path, cache=remote.cache)
    for record in remote.reader.list_flights(origin="YYZ"):
        updater.delete(record.provider, record.provider_id, expected=record)
    set_quota(remote.path, 0)
    assert remote.reader.available_dates("YYZ").dates == []
    restored = original.model_copy(
        update={
            "departure_scheduled": original.departure_scheduled.replace(day=18),
            "arrival_scheduled": original.arrival_scheduled.replace(day=19),
        }
    )
    updater.upsert([restored])
    assert [str(day) for day in remote.reader.available_dates("YYZ").dates] == ["2026-10-18"]


def test_bad_catalogue_scope_is_rejected_before_cache_registration(remote):
    data = json.loads((remote.directory / "catalog.json").read_text())
    data["chunks"][0]["flight_count"] += 1
    (remote.directory / "catalog.json").write_text(json.dumps(data))
    catalog = remote.cache.catalog()
    with pytest.raises(ItinerarySourceError, match="row count"):
        touch(remote, catalog.chunks[0])
    assert not loaded(remote)


def test_catalogue_change_during_an_edit_cannot_promote_stale_remote_data(remote, monkeypatch):
    original = remote.reader.search_itineraries(search_request()).items[0].legs[0]
    updater = FlightUpdater(remote.path, cache=remote.cache)
    fetch = updater._remote_original

    def changed_source(provider, provider_id):
        result = fetch(provider, provider_id)
        set_source(remote.path, "https://new-itineraries.test/catalog.json")
        return result

    monkeypatch.setattr(updater, "_remote_original", changed_source)
    with pytest.raises(ValueError, match="changed"):
        updater.update(original, original.model_copy(update={"flight_number": "STALE"}))
    with control(remote.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM flights").fetchone()[0] == 0


def test_streamed_export_preserves_existing_file_on_remote_failure(remote, tmp_path):
    output = tmp_path / "export.json"
    set_quota(remote.path, 0)
    admin.write_flight_export(output, remote.reader.iter_records())
    data = json.loads(output.read_text())
    assert len(data) == len(remote.records)
    assert any(row.get("aircraft_type_code") == "B738" for row in data)
    original = output.read_bytes()
    remote.state["offline"] = True
    with pytest.raises(ItinerarySourceError):
        admin.write_flight_export(output, remote.reader.iter_records())
    assert output.read_bytes() == original
    assert not list(tmp_path.glob("*.partial"))
