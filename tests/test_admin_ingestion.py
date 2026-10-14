import json
import sqlite3
from datetime import UTC, date, datetime

import httpx
import pytest
from test_adsblol import rules
from test_adsblol_extraction import daily_input, midnight_traces, rows
from test_adsblol_legs import leg_trace

from admin import client, ingestion
from core.config import Settings
from ingestion.adsblol.pipeline import extract_range


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    database = tmp_path / "selected.db"
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=database))
    assets, payloads, downloads = [], {}, []
    first, second = midnight_traces()
    for number in range(1, 6):
        day = date(2025, 1, number)
        data = first if number == 1 else second if number == 2 else leg_trace()
        data["timestamp"] = int(datetime.combine(day, datetime.min.time(), UTC).timestamp())
        asset, content = daily_input(day, [data])
        assets.append(asset)
        payloads[asset["url"]] = content
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"year": 2025, "assets": assets}))
    airports = tmp_path / "airports.csv"
    airports.write_text(
        "iata_code,latitude_deg,longitude_deg,scheduled_service,type\n"
        "AAA,40,-70,yes,large_airport\nBBB,41,-71,yes,large_airport\n"
    )
    selected_filter = tmp_path / "filter.json"
    selected_filter.write_text(rules().model_dump_json())
    root = tmp_path / "extracted"

    def transport(request):
        downloads.append(str(request.url))
        return httpx.Response(200, content=payloads[str(request.url)])

    def connected_range(*args, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(transport)) as http:
            yield from extract_range(*args, client=http, **kwargs)

    monkeypatch.setattr(ingestion, "extract_range", connected_range)
    flags = [
        "ingest",
        "configure",
        "--source",
        "adsblol",
        "--manifest",
        str(manifest),
        "--airport-catalog",
        str(airports),
        "--filter",
        str(selected_filter),
        "--data-root",
        str(root),
    ]
    return database, root, downloads, flags


def test_shell_jobs_persist_profiles_backfill_and_skip_overlap(pipeline, capsys, monkeypatch):
    database, root, downloads, flags = pipeline
    client.run(flags)
    capsys.readouterr()
    # A fresh shell restores the pipeline, even with no source flag.
    shell = client.LocalShell(database)
    shell.onecmd("ingest --start 2025-01-02 --end 2025-01-02")
    assert rows(root, "itinerary_legs") == []
    shell.onecmd("ingest --start 2025-01-04 --end 2025-01-05")
    assert len(rows(root, "itinerary_legs")) == 2
    shell = client.LocalShell(database)
    shell.onecmd("ingest --start 2025-01-01 --end 2025-01-03")
    assert len(rows(root, "itinerary_legs")) == 4
    assert len(downloads) == 5
    output = capsys.readouterr().out
    assert '"days_skipped": 1' in output
    assert '"records_inserted": 2' in output
    shell.onecmd("ingest --start 2025-01-01 --end 2025-01-05")
    assert len(downloads) == 5
    assert '"days_skipped": 5' in capsys.readouterr().out
    assert not list((root / "raw").rglob("*.tar"))
    before = (root / "itineraries.db").read_bytes(), database.read_bytes()
    shell.onecmd("ingest status")
    status = json.loads(capsys.readouterr().out)
    assert status["records"] == 4
    assert status["ranges"] == [{"start": "2025-01-01", "end": "2025-01-05", "days": 5}]
    assert before == ((root / "itineraries.db").read_bytes(), database.read_bytes())
    # Dates can be prompted on the next job without re-entering paths.
    answers = iter(["2025-01-03", "2025-01-04"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    shell.onecmd("ingest")
    assert '"days_skipped": 2' in capsys.readouterr().out


def test_guided_setup_cancel_and_database_isolation(pipeline, monkeypatch, capsys, tmp_path):
    database, root, _, flags = pipeline
    monkeypatch.setattr("builtins.input", lambda prompt: "cancel")
    shell = client.LocalShell(database)
    shell.onecmd("ingest")
    assert "Command cancelled" in capsys.readouterr().out
    assert not database.exists() and not root.exists()
    # Configure interactively with all paths explicit; the optional boundary is skipped.
    monkeypatch.setattr("builtins.input", lambda prompt: "-")
    shell.onecmd(" ".join(flags))
    assert ingestion.profiles(database)["adsblol"]["data_root"] == str(root)
    capsys.readouterr()
    other = tmp_path / "other.db"
    assert ingestion.profiles(other) == {}
    shell.onecmd(f"--db {other} ingest status")
    assert json.loads(capsys.readouterr().out)["records"] == 0
    assert not other.exists()


@pytest.mark.parametrize(
    "start,end",
    [("2025-01-01", "2025-02-15"), ("2025-01-05", "2025-01-06"), ("2025-01-03", "2025-01-01")],
)
def test_invalid_job_does_not_download_or_create_database(pipeline, start, end):
    database, root, downloads, flags = pipeline
    flags.remove("configure")
    with pytest.raises(ValueError):
        client.run(flags + ["--start", start, "--end", end])
    assert not downloads and not database.exists() and not root.exists()


def test_failed_second_day_preserves_first_and_can_retry(pipeline, monkeypatch, capsys):
    database, root, downloads, flags = pipeline
    client.run(flags)
    original = ingestion.extract_range

    def interrupted(*args, **kwargs):
        iterator = original(*args, **kwargs)
        yield next(iterator)
        iterator.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(ingestion, "extract_range", interrupted)
    client.LocalShell(database).onecmd("ingest --start 2025-01-03 --end 2025-01-04")
    assert "Command cancelled" in capsys.readouterr().out
    assert len(rows(root, "itinerary_legs")) == 1
    monkeypatch.setattr(ingestion, "extract_range", original)
    client.LocalShell(database).onecmd("ingest --start 2025-01-03 --end 2025-01-04")
    assert len(rows(root, "itinerary_legs")) == 2 and len(downloads) == 2


def test_guided_csv_import_upserts_across_airports_and_jobs(tmp_path, monkeypatch, capsys):
    database, feed = tmp_path / "flights.db", tmp_path / "licensed.csv"
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=database))
    feed.write_text(
        "provider_id,flight_number,origin_iata,destination_iata,departure_scheduled,arrival_scheduled,aircraft_type_code\n"
        "stable,AB123,AAA,BBB,2025-01-01T14:00:00Z,2025-01-01T15:00:00Z,A320\n"
    )
    answers = iter([str(feed), "AAA BBB", "2025-01-01", "2025-01-02"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    client.LocalShell(database).onecmd("ingest --source csv")
    assert '"upserted": 1' in capsys.readouterr().out
    # An exact repeat updates the same identity; it doesn't create another record.
    client.LocalShell(database).onecmd("ingest --start 2025-01-01 --end 2025-01-01")
    with sqlite3.connect(database) as connection:
        count, kind = connection.execute(
            "SELECT COUNT(*),aircraft_type_code FROM flights"
        ).fetchone()
    assert count == 1 and kind == "A320"
