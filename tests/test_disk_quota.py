"""Live database policy persists independently of disposable archive inputs."""

import json
import sqlite3
import subprocess
import sys

import pytest

from admin import client
from core.disk_quota import database_bytes, policy, set_quota, storage_status
from ingestion.updater import FlightUpdater


def test_policy_survives_reset_without_catalogue_tables(tmp_path):
    path = tmp_path / "flights.db"
    updater = FlightUpdater(path)
    updater.initialize()
    assert policy(path)["quota_bytes"] == 20 * 1024**3
    set_quota(path, 5 * 1024**3)
    updater.reset()
    assert policy(path)["quota_bytes"] == 5 * 1024**3
    with sqlite3.connect(path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert "database_policy" in names
        assert not any("cache" in name or "catalog" in name for name in names)


def test_another_admin_process_changes_quota_without_counting_raw(tmp_path):
    path = tmp_path / "flights.db"
    FlightUpdater(path).initialize()
    raw = tmp_path / "raw.tar"
    raw.write_bytes(b"\0" * 1024**2)
    subprocess.run(
        [sys.executable, "-m", "admin", "--db", str(path), "storage", "quota", "0"],
        check=True,
        capture_output=True,
    )
    status = storage_status(path)
    assert status["quota_bytes"] == 0
    assert status["used_bytes"] == status["over_quota_bytes"] == database_bytes(path)
    assert path.exists() and raw.exists()
    set_quota(path, None)
    assert storage_status(path)["over_quota_bytes"] == 0


def test_storage_commands_report_only_database_usage(tmp_path, capsys):
    path = tmp_path / "flights.db"
    FlightUpdater(path).initialize()
    client.run(["--db", str(path), "storage", "quota", "1.5GiB"])
    assert json.loads(capsys.readouterr().out)["quota_bytes"] == 1610612736
    client.run(["--db", str(path), "storage", "status"])
    assert json.loads(capsys.readouterr().out)["database"] == str(path.resolve())


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_invalid_quota_never_creates_database(tmp_path, value):
    path = tmp_path / "missing.db"
    with pytest.raises(ValueError):
        set_quota(path, value)
    assert not path.exists()


def test_policy_and_status_do_not_create_missing_database(tmp_path):
    path = tmp_path / "missing.db"
    assert policy(path) is None
    with pytest.raises(ValueError, match="Initialize"):
        storage_status(path)
    assert not path.exists()
