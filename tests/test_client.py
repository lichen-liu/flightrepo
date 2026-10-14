import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from admin import client
from core.config import Settings
from core.models import FlightRecord
from server.reader import FlightReader


def test_local_management_roundtrip_and_reset(tmp_path, monkeypatch):
    local = tmp_path / "var"
    settings = Settings(database_path=local / "flights.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    monkeypatch.setattr(client, "LOCAL_DATA_DIR", local)
    record = FlightRecord(
        provider="fixture",
        provider_id="one",
        flight_number="AC1",
        origin_iata="YYZ",
        destination_iata="YUL",
        departure_scheduled=datetime(2026, 10, 10, 10, tzinfo=timezone.utc),
        arrival_scheduled=datetime(2026, 10, 10, 11, tzinfo=timezone.utc),
        fetched_at=datetime.now(timezone.utc),
    )
    source = tmp_path / "input.json"
    source.write_text(json.dumps([record.model_dump(mode="json")]))
    client.run(["import-json", str(source)])
    exported = tmp_path / "export.json"
    client.run(["export-json", str(exported)])
    assert json.loads(exported.read_text())[0]["provider_id"] == "one"
    store = FlightReader(settings.database_path)
    assert store.stats()["flights"] == 1
    source.write_text('[{"invalid": true}]')
    with pytest.raises(ValueError):
        client.run(["import-json", str(source)])
    assert store.stats()["flights"] == 1
    client.run(["flight", "delete", "fixture", "one", "--yes"])
    assert store.stats()["flights"] == 0
    client.run(["import-json", str(exported)])
    with pytest.raises(ValueError):
        client.run(["maintenance", "clear-data"])
    assert store.stats()["flights"] == 1
    client.run(["maintenance", "clear-data", "--yes"])
    assert store.stats()["flights"] == 0
    outside = tmp_path / "outside.json"
    outside.write_text("keep")
    with pytest.raises(ValueError):
        client.clear_database(outside)
    assert outside.read_text() == "keep"


def test_client_timestamp_normalizes_offsets():
    assert client.timestamp("2026-10-10T05:00:00-04:00").hour == 9
    with pytest.raises(ValueError):
        client.timestamp("2026-10-10T05:00:00")


@pytest.mark.parametrize(
    "command,data",
    [
        ("import-json", None),
        ("import-json", {}),
        ("import-json", [{"invalid": True}]),
        ("airports import", None),
        ("airports import", {"airports": None}),
        ("airports import", {"airports": [], "transfers": None}),
        ("airports import", {"airports": [], "transfers": {}}),
    ],
)
def test_invalid_import_does_not_create_database(tmp_path, monkeypatch, command, data):
    settings = Settings(database_path=tmp_path / "missing.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        client.run([*command.split(), str(source)])
    assert not settings.database_path.exists()


@pytest.mark.parametrize(
    "command,data,message",
    [
        ("import-json", None, "JSON array"),
        ("airports import", {"airports": [], "transfers": None}, "transfers array"),
    ],
)
def test_invalid_import_cli_has_readable_failure(tmp_path, command, data, message):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(data))
    result = subprocess.run(
        [sys.executable, "-m", "admin", *command.split(), str(source)],
        env={**os.environ, "FLIGHT_DATABASE_PATH": str(tmp_path / "missing.db")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert message in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "missing.db").exists()


@pytest.mark.parametrize(
    "arguments,message",
    [
        ([], "supply --yes"),
        (["--yes"], "project's var directory"),
    ],
)
def test_reset_helper_refuses_safely(tmp_path, arguments, message):
    sentinel = tmp_path / "outside.db"
    sentinel.write_bytes(b"keep this file")
    script = Path(__file__).resolve().parents[1] / "scripts/reset_local_data.py"
    result = subprocess.run(
        [sys.executable, str(script), *arguments],
        cwd=tmp_path,
        env={**os.environ, "FLIGHT_DATABASE_PATH": str(sentinel)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert message in result.stderr
    assert "Traceback" not in result.stderr
    assert sentinel.read_bytes() == b"keep this file"


def test_query_limits_rejected_before_database_creation(tmp_path, monkeypatch):
    settings = Settings(database_path=tmp_path / "missing.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    with pytest.raises(SystemExit):
        client.run(["airports", "--limit", "1001"])
    with pytest.raises(ValueError, match="100"):
        client.run(
            [
                "search",
                "--origin",
                "YYZ",
                "--destination",
                "CDG",
                "--start",
                "2026-10-17T00:00:00Z",
                "--end",
                "2026-10-18T00:00:00Z",
                "--horizon",
                "2026-10-19T00:00:00Z",
                "--limit",
                "101",
            ]
        )
    assert not settings.database_path.exists()


def route_inputs(monkeypatch, values):
    answers = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))


def test_create_route_saves_connected_flights(tmp_path, monkeypatch):
    settings = Settings(database_path=tmp_path / "route.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    route_inputs(
        monkeypatch,
        [
            "yyz",
            "yul",
            "AC1",
            "2026-10-17T10:00:00-04:00",
            "2026-10-17T11:00:00-04:00",
            "y",
            "cdg",
            "AC2",
            "2026-10-17T17:00:00Z",
            "2026-10-18T00:00:00Z",
            "n",
            "y",
        ],
    )
    client.LocalShell().onecmd("create-route")
    records = FlightReader(settings.database_path).all_records()
    assert len(records) == 2
    assert {(r.origin_iata, r.destination_iata) for r in records} == {
        ("YYZ", "YUL"),
        ("YUL", "CDG"),
    }
    assert len({r.provider_id for r in records}) == 2
    assert all(r.provider == "manual" for r in records)


@pytest.mark.parametrize("ending", ["cancel", "n"])
def test_create_route_discard_does_not_create_database(tmp_path, monkeypatch, ending):
    settings = Settings(database_path=tmp_path / "missing.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    route_inputs(
        monkeypatch,
        ["YYZ", "YUL", "AC1", "2026-10-17T14:00:00Z", "2026-10-17T15:00:00Z", "n", ending],
    )
    client.run(["create-route"])
    assert not settings.database_path.exists()


def test_create_route_retries_invalid_connection(tmp_path, monkeypatch, capsys):
    settings = Settings(database_path=tmp_path / "route.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    route_inputs(
        monkeypatch,
        [
            "YYZ",
            "YUL",
            "AC1",
            "2026-10-17T14:00:00Z",
            "2026-10-17T15:00:00Z",
            "y",
            "CDG",
            "AC2",
            "2026-10-17T14:30:00Z",
            "2026-10-18T00:00:00Z",
            "CDG",
            "AC2",
            "2026-10-17T17:00:00Z",
            "2026-10-18T00:00:00Z",
            "n",
            "y",
        ],
    )
    client.run(["create-route"])
    assert "previous arrival" in capsys.readouterr().out
    assert len(FlightReader(settings.database_path).all_records()) == 2


def test_overview_empty_database_and_missing_file(tmp_path, monkeypatch, capsys):
    settings = Settings(database_path=tmp_path / "overview.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    with pytest.raises(sqlite3.OperationalError, match="unable to open database"):
        client.run(["overview"])
    assert not settings.database_path.exists()
    client.run(["maintenance", "init"])
    capsys.readouterr()
    client.run(["overview"])
    text = capsys.readouterr().out
    assert "Flights: 0" in text
    assert "No flights yet" in text
    client.run(["overview", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["routes"] == 0
    assert result["latest_fetch"] is None
    assert result["top_routes"] == []


def test_overview_aggregates_coverage(tmp_path, monkeypatch, capsys):
    settings = Settings(database_path=tmp_path / "overview.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    source = tmp_path / "flights.json"
    source.write_text(
        json.dumps(
            [
                dict(
                    provider="manual",
                    provider_id=str(index),
                    flight_number="AC1",
                    airline_iata="AC",
                    origin_iata=origin,
                    destination_iata=destination,
                    departure_scheduled=f"2026-10-{17 + index}T14:00:00Z",
                    arrival_scheduled=f"2026-10-{17 + index}T15:00:00Z",
                    fetched_at="2026-10-14T12:00:00Z",
                    status=status,
                )
                for index, (origin, destination, status) in enumerate(
                    [
                        ("YYZ", "YUL", "scheduled"),
                        ("YYZ", "YUL", "cancelled"),
                        ("YUL", "CDG", "scheduled"),
                    ]
                )
            ]
        )
    )
    client.run(["import-json", str(source)])
    capsys.readouterr()
    client.run(["overview", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["flights"] == 3
    assert result["routes"] == 2
    assert result["flight_airports"] == result["missing_airports"] == 3
    assert result["airlines"] == 1
    assert result["providers"] == {"manual": 3}
    assert result["statuses"] == {"cancelled": 1, "scheduled": 2}
    assert result["top_routes"][0] == {
        "origin_iata": "YYZ",
        "destination_iata": "YUL",
        "flights": 2,
    }
    assert result["first_departure"].startswith("2026-10-17")
    assert result["last_departure"].startswith("2026-10-19")
