"""Admin preparation fetches metadata only and preserves immutable extraction inputs."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from admin import client, ingestion
from core.config import Settings
from ingestion.adsblol.prepare import AIRPORT_URL, prepare_inputs

AIRPORTS = (
    b"iata_code,latitude_deg,longitude_deg,scheduled_service,type\nAAA,40,-70,yes,large_airport\n"
)


def transport_for(days, requests):
    assets = []
    for day, repository_year in days:
        name = f"v{day:%Y.%m.%d}-planes-readsb-prod-0.tar"
        url = f"https://github.com/adsblol/globe_history_{repository_year}/releases/download/v{day:%Y.%m.%d}-planes-readsb-prod-0/{name}"
        assets.append(
            {"name": name, "size": 100, "browser_download_url": url, "digest": "sha256:" + "a" * 64}
        )

    def respond(request):
        url = str(request.url)
        requests.append(url)
        if url == AIRPORT_URL:
            return httpx.Response(200, content=AIRPORTS)
        for year in {item[1] for item in days}:
            if (
                url
                == f"https://raw.githubusercontent.com/adsblol/globe_history_{year}/main/PREFERRED_RELEASES.txt"
            ):
                return httpx.Response(
                    200,
                    text="\n".join(
                        a["browser_download_url"]
                        for a in assets
                        if f"globe_history_{year}/" in a["browser_download_url"]
                    ),
                )
            if url.startswith(
                f"https://api.github.com/repos/adsblol/globe_history_{year}/releases?"
            ):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "assets": [
                                a
                                for a in assets
                                if f"globe_history_{year}/" in a["browser_download_url"]
                            ]
                        }
                    ],
                )
        return httpx.Response(404)

    return httpx.MockTransport(respond)


def test_prepare_is_metadata_only_repeatable_and_preserves_custom_inputs(tmp_path):
    first, last = date(2023, 7, 1), date(2023, 7, 2)
    requests = []
    with httpx.Client(transport=transport_for([(first, 2023), (last, 2023)], requests)) as http:
        result = prepare_inputs(tmp_path, first, last, client=http)
        manifest = json.loads(Path(result["manifest"]).read_text())
        assert {item["date"] for item in manifest["assets"]} == {str(first), str(last)}
        assert all(item["sha256"] == "a" * 64 for item in manifest["assets"])
        airport_bytes = Path(result["airport_catalog"]).read_bytes()
        requests.clear()
        custom = tmp_path / "custom-filter.json"
        custom.write_text('{"fixed_wing_types":["A320"],"callsign_prefixes":["UAL"]}')
        repeated = prepare_inputs(tmp_path, first, last, filter_path=custom, client=http)
        assert requests == []
        assert repeated["filter_path"] == str(custom.resolve())
        assert Path(result["airport_catalog"]).read_bytes() == airport_bytes
    assert not (tmp_path / "raw").exists() and not list(tmp_path.glob("*.db"))


def test_preparation_handles_previous_repository_january_first(tmp_path):
    first, last = date(2023, 12, 31), date(2024, 1, 1)
    requests = []
    with httpx.Client(transport=transport_for([(first, 2024), (last, 2023)], requests)) as http:
        result = prepare_inputs(tmp_path, first, last, client=http)
    assert {
        item["date"] for item in json.loads(Path(result["manifest"]).read_text())["assets"]
    } == {str(first), str(last)}
    assert not any("/releases/download/" in url for url in requests)


def test_separate_january_first_job_discovers_preceding_repository(tmp_path):
    day = date(2024, 1, 1)
    requests = []
    with httpx.Client(transport=transport_for([(day, 2023)], requests)) as http:
        result = prepare_inputs(tmp_path, day, day, client=http)
    assets = json.loads(Path(result["manifest"]).read_text())["assets"]
    assert len(assets) == 1 and assets[0]["date"] == str(day)
    assert "globe_history_2023/" in assets[0]["url"]


def test_admin_prepare_saves_source_profile_without_ingesting_flights(
    tmp_path, monkeypatch, capsys
):
    database, root = tmp_path / "flights.db", tmp_path / "inputs"
    first, last = date(2023, 7, 1), date(2023, 7, 2)
    requests = []

    def prepared(*args, **kwargs):
        with httpx.Client(transport=transport_for([(first, 2023), (last, 2023)], requests)) as http:
            return prepare_inputs(*args, client=http, **kwargs)

    monkeypatch.setattr(ingestion, "prepare_inputs", prepared)
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=database))
    client.run(
        [
            "ingest",
            "adsblol",
            "prepare",
            "--start",
            str(first),
            "--end",
            str(last),
            "--data-root",
            str(root),
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert result["prepared"] == "adsblol" and result["next"].startswith("ingest adsblol run")
    assert ingestion.profiles(database)["adsblol"]["manifest"] == str(root / "manifest.json")
    from server.reader import FlightReader

    assert FlightReader(database).stats()["flights"] == 0


def test_unavailable_period_does_not_create_artifacts_or_database(tmp_path, monkeypatch):
    database, root = tmp_path / "flights.db", tmp_path / "inputs"

    def prepared(*args, **kwargs):
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(404))
        ) as http:
            return prepare_inputs(*args, client=http, **kwargs)

    monkeypatch.setattr(ingestion, "prepare_inputs", prepared)
    monkeypatch.setattr(client, "get_settings", lambda: Settings(database_path=database))
    with pytest.raises(ValueError, match="unavailable"):
        client.run(
            [
                "ingest",
                "adsblol",
                "prepare",
                "--start",
                "2023-07-01",
                "--end",
                "2023-07-02",
                "--data-root",
                str(root),
            ]
        )
    assert not root.exists() and not database.exists()
