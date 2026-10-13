import json
import os
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
    client.run(["delete", "fixture", "one"])
    assert store.stats()["flights"] == 0
    client.run(["import-json", str(exported)])
    with pytest.raises(ValueError):
        client.run(["reset"])
    assert store.stats()["flights"] == 1
    client.run(["reset", "--yes"])
    assert store.stats()["flights"] == 0
    outside = tmp_path / "outside.json"
    outside.write_text("keep")
    with pytest.raises(ValueError):
        client.reset_database(outside)
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
        ("import-airports", None),
        ("import-airports", {"airports": None}),
        ("import-airports", {"airports": [], "transfers": None}),
        ("import-airports", {"airports": [], "transfers": {}}),
    ],
)
def test_invalid_import_does_not_create_database(tmp_path, monkeypatch, command, data):
    settings = Settings(database_path=tmp_path / "missing.db")
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        client.run([command, str(source)])
    assert not settings.database_path.exists()


@pytest.mark.parametrize(
    "command,data,message",
    [
        ("import-json", None, "JSON array"),
        ("import-airports", {"airports": [], "transfers": None}, "transfers array"),
    ],
)
def test_invalid_import_cli_has_readable_failure(tmp_path, command, data, message):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(data))
    result = subprocess.run(
        [sys.executable, "-m", "admin", command, str(source)],
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
    with pytest.raises(ValueError, match="between 1 and 100"):
        client.run(
            [
                "itineraries",
                "--origin",
                "YYZ",
                "--destination",
                "CDG",
                "--start",
                "2026-10-17T00:00:00Z",
                "--end",
                "2026-10-18T00:00:00Z",
                "--limit",
                "101",
            ]
        )
    assert not settings.database_path.exists()
