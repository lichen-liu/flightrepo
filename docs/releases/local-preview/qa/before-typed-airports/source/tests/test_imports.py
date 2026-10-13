"""Import fidelity matters before routes from local feeds can be trusted."""

import asyncio
import csv
import json
from datetime import date, datetime, timezone

import pytest

from admin import client
from core.config import Settings
from ingestion.providers.csv_feed import CsvProvider
from ingestion.service import IngestionService
from ingestion.updater import FlightUpdater
from server.reader import FlightReader


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def row(**changes):
    return {
        "provider_id": "source-123",
        "flight_number": "AC1",
        "origin_iata": "YYZ",
        "destination_iata": "YUL",
        "departure_scheduled": "2026-10-18T01:30:00+02:00",
        "arrival_scheduled": "2026-10-18T02:30:00+02:00",
        **changes,
    }


def test_csv_filters_utc_day_and_preserves_optional_facts(tmp_path):
    path = tmp_path / "feed.csv"
    write_csv(
        path,
        [
            row(
                departure_actual="2026-10-18T01:35:00+02:00",
                arrival_actual="2026-10-18T02:35:00+02:00",
                codeshares='["UA123"]',
                fetched_at="2026-10-16T00:00:00Z",
            )
        ],
    )
    provider = CsvProvider(path)
    records = asyncio.run(
        provider.flights_for_airport(" yyz ", date(2026, 10, 17), date(2026, 10, 17))
    )
    assert len(records) == 1
    flight = records[0]
    assert flight.departure_scheduled == datetime(2026, 10, 17, 23, 30, tzinfo=timezone.utc)
    assert flight.departure_actual == datetime(2026, 10, 17, 23, 35, tzinfo=timezone.utc)
    assert flight.arrival_actual == datetime(2026, 10, 18, 0, 35, tzinfo=timezone.utc)
    assert flight.codeshares == ["UA123"]
    assert flight.fetched_at == datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert not asyncio.run(
        provider.flights_for_airport("YYZ", date(2026, 10, 18), date(2026, 10, 18))
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"departure_scheduled": "2026-10-17T10:00:00"},
        {"flight_number": "   "},
        {"codeshares": "not-json"},
        {"arrival_actual": "2026-10-18T03:00:00"},
    ],
)
def test_bad_csv_batch_does_not_write_partial_flights(tmp_path, changes):
    path = tmp_path / "feed.csv"
    a = row(**{key: "" for key in changes})
    a.update({key: row().get(key, "") for key in changes})
    b = row(provider_id="bad", **changes)
    # Both rows need the optional column if this case introduces one.
    write_csv(path, [a, b])
    updater = FlightUpdater(tmp_path / "flights.db")
    updater.initialize()
    with pytest.raises(ValueError):
        asyncio.run(
            IngestionService(updater, CsvProvider(path), 31).ingest(
                ["YYZ"], date(2026, 10, 17), date(2026, 10, 18)
            )
        )
    assert FlightReader(updater.path).stats()["flights"] == 0


def test_csv_missing_headers_has_readable_error(tmp_path):
    path = tmp_path / "feed.csv"
    path.write_text("flight_number,origin_iata\nAC1,YYZ\n")
    with pytest.raises(ValueError, match="CSV is missing columns"):
        asyncio.run(
            CsvProvider(path).flights_for_airport("YYZ", date(2026, 10, 17), date(2026, 10, 17))
        )


@pytest.mark.parametrize(
    "airports,start,end",
    [
        (["YYZ"], date(2026, 10, 18), date(2026, 10, 17)),
        ([5], date(2026, 10, 17), date(2026, 10, 18)),
        (["ÅBC"], date(2026, 10, 17), date(2026, 10, 18)),
    ],
)
def test_service_validates_before_provider_or_database_access(tmp_path, airports, start, end):
    class NeverCalled:
        name = "never"

        async def flights_for_airport(self, *args):
            raise AssertionError("invalid input reached provider")

    updater = FlightUpdater(tmp_path / "missing.db")
    with pytest.raises(ValueError):
        asyncio.run(IngestionService(updater, NeverCalled(), 31).ingest(airports, start, end))
    assert not updater.path.exists()


def test_airport_catalog_cli_roundtrip(tmp_path, monkeypatch, capsys):
    settings = Settings(database_path=tmp_path / "flights.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    catalog = {
        "airports": [
            {
                "iata": "LHR",
                "name": "Heathrow",
                "city_id": "LON",
                "timezone": "Europe/London",
                "latitude": 51.47,
                "longitude": -0.45,
            },
            {
                "iata": "LGW",
                "name": "Gatwick",
                "city_id": "LON",
                "timezone": "Europe/London",
                "latitude": 51.15,
                "longitude": -0.19,
            },
        ],
        "transfers": [
            {
                "arrival_iata": "LHR",
                "departure_iata": "LGW",
                "minimum_minutes": 180,
                "source": "Test allowance",
            }
        ],
    }
    source = tmp_path / "catalog.json"
    source.write_text(json.dumps(catalog))
    client.run(["import-airports", str(source)])
    capsys.readouterr()
    client.run(["airports", "--query", "LHR"])
    airport = json.loads(capsys.readouterr().out)
    assert len(airport) == 1 and airport[0]["iata"] == "LHR"
    exported = tmp_path / "exported.json"
    client.run(["export-airports", str(exported)])
    data = json.loads(exported.read_text())
    assert data["transfers"] == catalog["transfers"]
    assert sorted(data["airports"], key=lambda a: a["iata"]) == sorted(
        catalog["airports"], key=lambda a: a["iata"]
    )
    source.write_text(json.dumps({"wrong": []}))
    with pytest.raises(ValueError, match="airports array"):
        client.run(["import-airports", str(source)])
    assert FlightReader(settings.database_path).airport_catalog() == data
