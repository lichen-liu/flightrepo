import json
import sqlite3
from datetime import datetime, timezone

import pytest

from admin import client
from admin.management import query_database
from core.config import Settings
from core.models import FlightRecord
from ingestion.updater import FlightUpdater
from server.reader import FlightReader


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "flights.db"
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=path))
    updater = FlightUpdater(path)
    updater.initialize()
    record = FlightRecord(
        provider="manual",
        provider_id="first",
        flight_number="AC1",
        airline_iata="AC",
        origin_iata="YYZ",
        destination_iata="YUL",
        departure_scheduled=datetime(2026, 10, 17, 14, tzinfo=timezone.utc),
        arrival_scheduled=datetime(2026, 10, 17, 15, tzinfo=timezone.utc),
        departure_actual=datetime(2026, 10, 17, 14, 5, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        codeshares=["UA1"],
        status="scheduled",
    )
    updater.upsert([record])
    return path, record, updater


def answers(monkeypatch, values):
    values = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt: next(values))


def test_switch_database_and_per_command_override(database, tmp_path, capsys):
    path, _, _ = database
    second = tmp_path / "other db.sqlite"
    shell = client.LocalShell()
    shell.onecmd(f'db "{second}" --create')
    assert shell.database_path == second
    shell.onecmd("overview --json")
    assert '"flights": 0' in capsys.readouterr().out
    shell.onecmd(f'--db "{path}" list-flights')
    assert '"provider_id": "first"' in capsys.readouterr().out
    assert shell.database_path == second
    shell.onecmd(f'db "{tmp_path / "missing.db"}"')
    assert "Error:" in capsys.readouterr().out
    assert shell.database_path == second
    shell.onecmd(f'db "{path}"')
    shell.onecmd("overview --json")
    assert '"flights": 1' in capsys.readouterr().out


def test_db_flag_starts_shell(database, monkeypatch):
    path, _, _ = database
    seen = []
    monkeypatch.setattr(
        client.LocalShell, "cmdloop", lambda shell: seen.append(shell.database_path)
    )
    client.run(["--db", str(path)])
    assert seen == [path]


def test_browse_and_show(database, capsys):
    client.run(["list-flights", "--origin", "yyz", "--provider", "manual", "--status", "scheduled"])
    assert len(json.loads(capsys.readouterr().out)) == 1
    client.run(["list-flights", "--destination", "CDG"])
    assert json.loads(capsys.readouterr().out) == []
    client.run(["show", "manual", "first"])
    assert json.loads(capsys.readouterr().out)["codeshares"] == ["UA1"]
    with pytest.raises(ValueError, match="not found"):
        client.run(["show", "manual", "missing"])


def test_interactive_edit_preserves_identity_and_fetch(database, monkeypatch):
    path, original, _ = database
    answers(monkeypatch, ["AC2", "", "", "", "", "", "-", "", "landed", "UA2,UA3", "y"])
    client.LocalShell().onecmd("edit manual first")
    saved = FlightReader(path).flight("manual", "first")
    assert saved.flight_number == "AC2"
    assert saved.departure_actual is None
    assert saved.codeshares == ["UA2", "UA3"]
    assert saved.status == "landed"
    assert saved.fetched_at == original.fetched_at
    assert saved.arrival_scheduled == original.arrival_scheduled


@pytest.mark.parametrize("stop", ["cancel", "EOF", "interrupt", "decline"])
def test_edit_cancellation_preserves_database(database, monkeypatch, stop):
    path, original, _ = database
    if stop == "EOF":

        def interrupted(prompt):
            raise EOFError

        monkeypatch.setattr("builtins.input", interrupted)
    elif stop == "interrupt":

        def interrupted(prompt):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", interrupted)
    else:
        answers(monkeypatch, ["cancel"] if stop == "cancel" else [""] * 10 + ["n"])
    client.run(["edit", "manual", "first"])
    assert FlightReader(path).flight("manual", "first") == original


def test_invalid_edit_retries_before_save(database, monkeypatch, capsys):
    path, _, _ = database
    answers(
        monkeypatch,
        [
            "",
            "",
            "",
            "",
            "2026-10-17T18:00:00Z",
            "",
            "",
            "",
            "",
            "",
            "AC2",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "y",
        ],
    )
    client.run(["edit", "manual", "first"])
    assert "Invalid flight" in capsys.readouterr().out
    assert FlightReader(path).flight("manual", "first").flight_number == "AC2"


def test_json_update_validates_and_requires_existing_identity(database, tmp_path):
    path, original, _ = database
    source = tmp_path / "update.json"
    data = original.model_dump(mode="json")
    data["flight_number"] = "AC2"
    source.write_text(json.dumps(data))
    client.run(["update", "manual", "first", str(source)])
    assert FlightReader(path).flight("manual", "first").flight_number == "AC2"
    data["provider_id"] = "other"
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="cannot change"):
        client.run(["update", "manual", "first", str(source)])
    with pytest.raises(ValueError, match="not found"):
        client.run(["update", "manual", "absent", str(source)])
    data["provider_id"] = "first"
    data["arrival_scheduled"] = "2026-10-17T12:00:00Z"
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        client.run(["update", "manual", "first", str(source)])
    assert len(FlightReader(path).all_records()) == 1


def test_update_and_delete_reject_stale_review(database):
    path, original, updater = database
    newer = FlightRecord.model_validate({**original.model_dump(), "flight_number": "AC9"})
    updater.upsert([newer])
    with pytest.raises(ValueError, match="changed"):
        updater.update(original, original)
    with pytest.raises(ValueError, match="changed"):
        updater.delete("manual", "first", expected=original)
    assert FlightReader(path).flight("manual", "first") == newer
    updater.delete("manual", "first")
    with pytest.raises(ValueError, match="deleted"):
        updater.update(newer, newer)
    assert FlightReader(path).all_records() == []


def test_delete_preview_decline_and_confirm(database, monkeypatch, capsys):
    path, original, _ = database
    answers(monkeypatch, ["n", "y"])
    client.run(["delete", "manual", "first"])
    assert FlightReader(path).flight("manual", "first") == original
    assert '"flight_number": "AC1"' in capsys.readouterr().out
    client.LocalShell().onecmd("delete manual first")
    assert FlightReader(path).all_records() == []


def test_sql_works_on_any_sqlite_schema_and_preserves_duplicate_columns(tmp_path):
    path = tmp_path / "unrelated.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE notes (value TEXT)")
        connection.executemany("INSERT INTO notes VALUES (?)", [("one",), ("two",)])
    result = query_database(
        path, "WITH items AS (SELECT * FROM notes) SELECT value, value FROM items ORDER BY value", 1
    )
    assert result == {"columns": ["value", "value"], "rows": [["one", "one"]], "truncated": True}
    assert query_database(path, "SELECT * FROM notes WHERE 0", 1)["rows"] == []


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM flights",
        "UPDATE flights SET flight_number='bad'",
        "DROP TABLE flights",
        "CREATE TABLE bad (id)",
        "PRAGMA user_version=9",
        "ATTACH DATABASE ':memory:' AS other",
        "BEGIN",
        "WITH items AS (SELECT 1) DELETE FROM flights",
        "SELECT load_extension('bad')",
        "SELECT 1; DELETE FROM flights",
    ],
)
def test_sql_rejects_non_read_operations(database, sql):
    path, original, _ = database
    with pytest.raises(sqlite3.Error):
        query_database(path, sql, 20)
    assert FlightReader(path).flight("manual", "first") == original


@pytest.mark.parametrize(
    "command", ["show", "query", "tables", "flights", "airports", "export-json"]
)
def test_reads_do_not_initialize_missing_database(tmp_path, monkeypatch, command):
    path = tmp_path / "missing.db"
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=path))
    extra = {
        "show": ["manual", "first"],
        "query": ["SELECT 1"],
        "tables": [],
        "flights": ["--start", "2026-10-17T00:00:00Z", "--end", "2026-10-18T00:00:00Z"],
        "airports": [],
        "export-json": [str(tmp_path / "output.json")],
    }
    with pytest.raises(sqlite3.OperationalError):
        client.run([command, *extra[command]])
    assert not path.exists()


def test_routing_flags_and_guided_search_replay(database, tmp_path, monkeypatch, capsys):
    _, first, updater = database
    onward = FlightRecord.model_validate(
        {
            **first.model_dump(),
            "provider_id": "second",
            "origin_iata": "YUL",
            "destination_iata": "CDG",
            "departure_scheduled": datetime(2026, 10, 17, 17, tzinfo=timezone.utc),
            "arrival_scheduled": datetime(2026, 10, 18, 0, tzinfo=timezone.utc),
            "departure_actual": None,
        }
    )
    updater.upsert([onward])
    snapshot = tmp_path / "replay.json"
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
            "--via",
            "YUL",
            "--avoid",
            "JFK",
            "--ranking",
            "earliest_arrival",
            "--trace",
            "--snapshot-out",
            str(snapshot),
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert len(result["items"]) == 1
    client.run(["replay-routing", str(snapshot)])
    assert json.loads(capsys.readouterr().out)["matches_saved_result"]
    answers(
        monkeypatch,
        [
            "YYZ",
            "CDG",
            "2026-10-17T00:00:00Z",
            "2026-10-18T00:00:00Z",
            "2026-10-19T00:00:00Z",
            "YUL",
            "JFK",
            "",
            "",
            "",
            "",
            "",
            "n",
            "n",
        ],
    )
    client.run(["search-route", "--snapshot-out", str(snapshot)])
    assert json.loads(snapshot.read_text())["request"]["via_airport"] == "YUL"


def test_real_shell_create_view_edit_delete(tmp_path):
    import os
    import subprocess
    import sys

    path = tmp_path / "shell db.sqlite"
    result = subprocess.run(
        [sys.executable, "-m", "admin", "--db", str(path)],
        input="\n".join(
            [
                "create-route",
                "YYZ",
                "YUL",
                "AC1",
                "2026-10-17T14:00:00Z",
                "2026-10-17T15:00:00Z",
                "n",
                "y",
                "list-flights",
                "overview",
                "db",
                'query "SELECT COUNT(*) FROM flights"',
                "help edit",
                "quit",
                "",
            ]
        ),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Flights: 1" in result.stdout
    assert '"truncated": false' in result.stdout
    assert "usage:" in result.stdout
    assert "Error:" not in result.stdout
    records = FlightReader(path).all_records()
    assert len(records) == 1
    assert records[0].origin_iata == "YYZ"
