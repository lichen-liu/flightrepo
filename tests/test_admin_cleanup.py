import json

import pytest

from admin import client
from core.config import Settings
from core.models import FlightRecord
from ingestion.updater import FlightUpdater


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    path = tmp_path / "facts.db"
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=path))
    updater = FlightUpdater(path)
    updater.initialize()
    records = []
    for index, (origin, destination, hour, status) in enumerate(
        [
            ("AAA", "BBB", 14, "scheduled"),
            ("BBB", "CCC", 16, "scheduled"),
            ("CCC", "DDD", 18, "scheduled"),
            ("AAA", "BBB", 14, "cancelled"),
        ]
    ):
        records.append(
            FlightRecord(
                provider="fixture",
                provider_id=str(index),
                flight_number=f"AB{index}",
                origin_iata=origin,
                destination_iata=destination,
                departure_scheduled=f"2026-10-17T{hour}:00:00Z",
                arrival_scheduled=f"2026-10-17T{hour + 1}:00:00Z",
                fetched_at="2026-10-14T00:00:00Z",
                status=status,
            )
        )
    updater.upsert(records)
    return path


def test_flight_browser_dates_statuses_and_bounds(local_db, capsys):
    client.run(["flights", "--origin", "AAA"])
    all_statuses = json.loads(capsys.readouterr().out)
    assert {row["status"] for row in all_statuses} == {"scheduled", "cancelled"}
    client.run(
        [
            "flights",
            "--origin",
            "AAA",
            "--start",
            "2026-10-17T10:00:00-04:00",
            "--end",
            "2026-10-17T15:00:00Z",
        ]
    )
    assert json.loads(capsys.readouterr().out) == all_statuses
    client.run(["flights", "--end", "2026-10-17T14:00:00Z"])
    assert json.loads(capsys.readouterr().out) == []
    client.run(["flights", "--start", "2026-10-17T16:00:00Z", "--status", "scheduled"])
    assert [r["origin_iata"] for r in json.loads(capsys.readouterr().out)] == ["BBB", "CCC"]
    with pytest.raises(ValueError, match="end must follow"):
        client.run(["flights", "--start", "2026-10-17T16:00:00Z", "--end", "2026-10-17T14:00:00Z"])


def test_search_flags_and_request_file_preserve_ranking_and_order(local_db, tmp_path, capsys):
    client.run(
        [
            "search",
            "--origin",
            "AAA",
            "--destination",
            "DDD",
            "--start",
            "2026-10-17T00:00:00Z",
            "--end",
            "2026-10-18T00:00:00Z",
            "--horizon",
            "2026-10-19T00:00:00Z",
            "--via",
            "BBB",
            "CCC",
            "--trace",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert len(result["items"]) == 1
    assert result["query"]["via_airport"] == ["BBB", "CCC"]
    assert result["diagnostics"]["exhaustive"]
    source = tmp_path / "request.json"
    source.write_text(json.dumps(result["query"]))
    client.run(["search", "--request", str(source)])
    repeated = json.loads(capsys.readouterr().out)
    assert result.pop("search_time_seconds") >= 0
    assert repeated.pop("search_time_seconds") >= 0
    assert repeated == result
    data = result["query"]
    data["limits"]["max_candidates"] = 1
    source.write_text(json.dumps(data))
    client.run(["search", "--request", str(source)])
    limited = json.loads(capsys.readouterr().out)
    assert not limited["diagnostics"]["exhaustive"]
    assert "candidate_limit" in limited["diagnostics"]["termination_reasons"]
    with pytest.raises(ValueError, match="cannot be combined"):
        client.run(["search", "--request", str(source), "--origin", "AAA"])


def test_guided_search_supports_ordered_vias_and_offline_replay(
    local_db, tmp_path, monkeypatch, capsys
):
    values = iter(
        [
            "AAA",
            "DDD",
            "2026-10-17T00:00:00Z",
            "2026-10-18T00:00:00Z",
            "2026-10-19T00:00:00Z",
            "BBB CCC",
            "-",
            "",
            "",
            "",
            "",
            "",
            "n",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(values))
    snapshot = tmp_path / "snapshot.json"
    client.run(["search", "--snapshot-out", str(snapshot)])
    capsys.readouterr()
    data = json.loads(snapshot.read_text())
    assert data["request"]["via_airport"] == ["BBB", "CCC"]
    assert len(data["result"]["items"]) == 1
    monkeypatch.setattr(client, "get_settings", lambda: pytest.fail("replay read DB settings"))
    client.run(["debug", "replay", str(snapshot)])
    assert json.loads(capsys.readouterr().out)["matches_saved_result"]


def test_guided_search_cancel_does_not_initialize(tmp_path, monkeypatch, capsys):
    path = tmp_path / "missing.db"
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=path))
    monkeypatch.setattr("builtins.input", lambda prompt: "cancel")
    client.run(["search"])
    assert "Search cancelled" in capsys.readouterr().out
    assert not path.exists()


def test_database_creation_is_explicit_and_overview_does_not_add_tables(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "new.db"
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=path))
    client.run(["db"])
    assert str(path) in capsys.readouterr().out
    assert not path.exists()
    client.run(["maintenance", "init"])
    capsys.readouterr()
    before = path.read_bytes()
    client.run(["overview", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["flights"] == 0
    assert result["journal_mode"] == "wal"
    assert path.read_bytes() == before


def test_grouped_help_is_available_without_database_settings(monkeypatch, capsys):
    monkeypatch.setattr(client, "get_settings", lambda: pytest.fail("help read settings"))
    for args in (
        ["--help"],
        ["flight", "--help"],
        ["flight", "edit", "--help"],
        ["maintenance", "--help"],
        ["debug", "--help"],
        ["inspect", "--help"],
    ):
        with pytest.raises(SystemExit) as error:
            client.run(args)
        assert error.value.code == 0
        assert "usage:" in capsys.readouterr().out


def test_old_initializer_is_not_an_implicit_write(tmp_path, monkeypatch):
    path = tmp_path / "missing.db"
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=path))
    with pytest.raises(SystemExit) as error:
        client.run(["stats"])
    assert error.value.code == 2
    assert not path.exists()


def test_oneoff_database_inspection_does_not_change_shell_selection(local_db, tmp_path, capsys):
    other = tmp_path / "second.sqlite"
    client.run(["--db", str(other), "maintenance", "init"])
    shell = client.LocalShell(local_db)
    shell.onecmd(f'--db "{other}" overview --json')
    assert shell.database_path == local_db
    shell.onecmd(f'db "{other}"')
    assert shell.database_path == other
    capsys.readouterr()
    shell.onecmd("help flight edit")
    assert "--file" in capsys.readouterr().out


def test_debug_continuation_preserves_facts_and_can_be_replayed(local_db, tmp_path, capsys):
    request = tmp_path / "continue.json"
    snapshot = tmp_path / "continue-replay.json"
    request.write_text(
        json.dumps(
            {
                "prefix": [{"provider": "fixture", "provider_id": "0"}],
                "destination": "DDD",
                "horizon_end": "2026-10-19T00:00:00Z",
                "via_airport": ["BBB", "CCC"],
            }
        )
    )
    before = local_db.read_bytes()
    client.run(["debug", "continue-route", str(request), "--snapshot-out", str(snapshot)])
    result = json.loads(capsys.readouterr().out)
    assert result["complete"]
    assert [leg["provider_id"] for leg in result["legs"]] == ["0", "1", "2"]
    assert local_db.read_bytes() == before
    client.run(["debug", "replay", str(snapshot)])
    assert json.loads(capsys.readouterr().out)["matches_saved_result"]
