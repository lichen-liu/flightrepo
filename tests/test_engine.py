from datetime import datetime, timezone
from pathlib import Path

from core.models import FlightRecord, FlightStatus
from core.itineraries import build_itineraries
from server.reader import FlightReader
from ingestion.updater import FlightUpdater


def flight(identifier: str, number: str, origin: str, destination: str, departure: str, arrival: str):
    return FlightRecord(provider="test", provider_id=identifier, flight_number=number,
                        origin_iata=origin, destination_iata=destination,
                        departure_scheduled=datetime.fromisoformat(departure),
                        arrival_scheduled=datetime.fromisoformat(arrival),
                        status=FlightStatus.SCHEDULED, fetched_at=datetime.now(timezone.utc))


def test_routes_and_connections(tmp_path: Path):
    updater = FlightUpdater(tmp_path / "flights.db")
    updater.initialize()
    store = FlightReader(updater.path)
    updater.upsert([
        flight("1", "AC101", "YYZ", "YUL", "2026-10-10T10:00:00+00:00", "2026-10-10T11:00:00+00:00"),
        flight("2", "AC202", "YUL", "CDG", "2026-10-10T12:00:00+00:00", "2026-10-10T19:00:00+00:00"),
        flight("3", "AC872", "YYZ", "CDG", "2026-10-10T14:00:00+00:00", "2026-10-10T21:00:00+00:00"),
    ])
    start = datetime.fromisoformat("2026-10-10T00:00:00+00:00")
    end = datetime.fromisoformat("2026-10-11T00:00:00+00:00")
    assert len(store.routes(start, end, origin="YYZ")) == 2
    results = build_itineraries(store, "YYZ", "CDG", start, end)
    assert [len(result.legs) for result in results] == [1, 2]


def test_upsert_refreshes_status(tmp_path: Path):
    updater = FlightUpdater(tmp_path / "flights.db")
    updater.initialize()
    store = FlightReader(updater.path)
    record = flight("1", "AC101", "YYZ", "YUL", "2026-10-10T10:00:00+00:00", "2026-10-10T11:00:00+00:00")
    updater.upsert([record])
    updater.upsert([record.model_copy(update={"status": FlightStatus.CANCELLED})])
    results = store.search(datetime.fromisoformat("2026-10-10T00:00:00+00:00"),
                           datetime.fromisoformat("2026-10-11T00:00:00+00:00"), include_cancelled=True)
    assert results[0].status == FlightStatus.CANCELLED

