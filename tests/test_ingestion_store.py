"""Producer isolation, read-only administration and repeatable publication."""

import asyncio
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from admin import client as admin
from core.config import Settings
from core.database_roles import ingestion_path
from core.disk_quota import policy, set_quota
from core.file_lock import file_mutex
from core.models import FlightRecord
from ingestion import cli
from ingestion.publish import publish_day
from ingestion.service import IngestionService
from ingestion.store import IngestionStore
from ingestion.updater import FlightUpdater
from server import api
from server.reader import FlightReader

DAY = date(2024, 1, 1)


def flight(key="one", **changes):
    return FlightRecord.model_validate(
        dict(
            provider="test",
            provider_id=key,
            origin_iata="AAA",
            destination_iata="BBB",
            departure_scheduled="2024-01-01T10:00:00Z",
            arrival_scheduled="2024-01-01T11:00:00Z",
            aircraft_type_code="A320",
            fetched_at="2024-01-02T00:00:00Z",
            **changes,
        )
    )


@pytest.fixture
def stores(tmp_path):
    stage = IngestionStore(tmp_path / "ingestion.db")
    stage.initialize()
    serving = FlightUpdater(tmp_path / "serving.db")
    serving.initialize()
    return stage, serving


def test_role_survives_rename_and_blocks_wrong_writers_and_server(stores, tmp_path, monkeypatch):
    stage, serving = stores
    with pytest.raises(sqlite3.DatabaseError, match="Ingestion database"):
        FlightUpdater(stage.path).reset()
    with pytest.raises(ValueError, match="ingestion database"):
        IngestionStore(serving.path).initialize()
    alias = tmp_path / "renamed.db"
    alias.hardlink_to(stage.path)
    assert ingestion_path(alias)
    with pytest.raises(sqlite3.DatabaseError):
        FlightUpdater(alias).initialize()
    monkeypatch.setattr(api, "get_settings", lambda: Settings(database_path=stage.path))
    with TestClient(api.app) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/v1/catalog").status_code == 503
    assert FlightReader(stage.path).stats()["flights"] == 0


@pytest.mark.parametrize(
    "command",
    [
        ["maintenance", "init"],
        ["maintenance", "apply-admission"],
        ["db", "--create"],
        ["storage", "quota", "1000000"],
        ["create-route"],
        ["create-airport"],
        ["delete", "test", "one"],
        ["flight", "approve", "test", "one", "--yes"],
        ["flight", "edit", "test", "one"],
        ["airports", "import", "missing.json"],
        ["import-json", "missing.json"],
    ],
)
def test_admin_rejects_every_manual_write_path(stores, command):
    stage, _ = stores
    before = stage.path.read_bytes()
    with pytest.raises((ValueError, SystemExit)):
        admin.run(["--db", str(stage.path), *command])
    assert stage.path.read_bytes() == before


def test_admin_query_status_and_clear_reset(stores, capsys):
    stage, serving = stores
    stage.upsert([flight()])
    stage.document(
        source="reference",
        kind="route",
        key="AAA-BBB",
        payload={"origin": "AAA"},
        source_url="https://example.test",
        valid_from="2024-01-01T00:00:00Z",
    )
    with stage.connect() as c:
        c.execute("INSERT INTO ingestion_profiles VALUES('test','{}',1)")
    admin.run(
        ["--db", str(stage.path), "inspect", "query", "SELECT count(*) FROM ingestion_documents"]
    )
    assert json.loads(capsys.readouterr().out)["rows"] == [[1]]
    for sql in [
        "DELETE FROM flight_legs",
        "PRAGMA writable_schema=ON",
        "ATTACH ':memory:' AS attack",
    ]:
        with pytest.raises(sqlite3.DatabaseError):
            admin.run(["--db", str(stage.path), "inspect", "query", sql])
    with pytest.raises(ValueError, match="--yes"):
        admin.run(["--db", str(stage.path), "maintenance", "clear-data"])
    set_quota(stage.path, 0)
    # Explicit clearing is possible even when the producer quota is exceeded.
    admin.run(["--db", str(stage.path), "maintenance", "clear-data", "--yes"])
    capsys.readouterr()
    with FlightReader(stage.path).connect() as c:
        assert c.execute("SELECT count(*) FROM flights").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM ingestion_documents").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM ingestion_profiles").fetchone()[0] == 1
        assert c.execute("SELECT generation FROM ingestion_store_metadata").fetchone()[0] == 2
    assert policy(stage.path)["quota_bytes"] == 0
    admin.run(["--db", str(stage.path), "maintenance", "reset", "--yes"])
    with FlightReader(stage.path).connect() as c:
        assert c.execute("SELECT count(*) FROM ingestion_profiles").fetchone()[0] == 0
    assert policy(stage.path)["quota_bytes"] == 20 * 1024**3
    assert FlightReader(serving.path).stats()["flights"] == 0


@pytest.mark.parametrize("suffix", [".job.lock", ".extraction.lock", ".producer.lock"])
def test_clear_refuses_active_pipeline(stores, suffix):
    stage, _ = stores
    stage.upsert([flight()])
    with file_mutex(Path(str(stage.path) + suffix)):
        with pytest.raises(ValueError, match="active ingestion"):
            stage.clear()
    assert FlightReader(stage.path).stats()["flights"] == 1


def test_normalized_evidence_is_deduplicated_versioned_and_never_published(stores):
    stage, serving = stores
    data = dict(
        source="reference",
        kind="route",
        key="AAA-BBB",
        payload={"origin": "AAA"},
        source_url="https://example.test",
        license_expression="test-only",
    )
    stage.document(**data)
    stage.document(**data)
    with stage.connect() as c:
        assert c.execute("SELECT revision FROM ingestion_documents").fetchone()[0] == 1
    data["payload"]["aircraft"] = "A320"
    stage.document(**data)
    with stage.connect() as c:
        assert c.execute("SELECT revision FROM ingestion_documents").fetchone()[0] == 2
    publish_day(stage.path, serving.path, DAY)
    with serving.connect() as c:
        names = {row[0] for row in c.execute("SELECT name FROM sqlite_schema")}
        assert not names.intersection(
            {
                "ingestion_documents",
                "ingestion_profiles",
                "ingestion_store_metadata",
                "pending_movements",
                "extraction_days",
            }
        )
        assert c.execute("SELECT count(*) FROM ingestion_batches").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM data_sources").fetchone()[0] == 0


def test_publication_admission_retry_retraction_and_admin_protection(stores):
    stage, serving = stores
    estimate = flight("estimate", review_reasons=["airborne_airport_estimate"])
    held = flight("held", review_reasons=["observation_gap"])
    stage.upsert([flight(), estimate, held])
    first = publish_day(stage.path, serving.path, DAY)
    assert first["inserted"] == 2 and first["held"] == 1
    reader = FlightReader(serving.path, serving_only=True)
    assert reader.flight("test", "estimate").review_reasons == estimate.review_reasons
    assert not reader.flight("test", "estimate").itinerary_reviewed
    assert publish_day(stage.path, serving.path, DAY)["duplicate"] == 2
    original = reader.flight("test", "one")
    serving.update(original, original.model_copy(update={"aircraft_type_code": "B738"}))
    serving.delete("test", "estimate")
    assert publish_day(stage.path, serving.path, DAY)["protected"] == 2
    assert reader.flight("test", "one").aircraft_type_code == "B738"
    with pytest.raises(ValueError, match="not found"):
        reader.flight("test", "estimate")
    # A candidate becoming held retracts only publisher-owned untouched facts.
    stage.upsert([flight("new")])
    publish_day(stage.path, serving.path, DAY)
    stage.upsert([flight("new", review_reasons=["observation_gap"])])
    assert publish_day(stage.path, serving.path, DAY)["retired"] == 1
    with pytest.raises(ValueError, match="not found"):
        reader.flight("test", "new")
    # Clearing ingestion never clears serving. Reconciliation preserves edits.
    stage.clear()
    publish_day(stage.path, serving.path, DAY)
    assert reader.flight("test", "one").aircraft_type_code == "B738"


def test_publication_preserves_partial_and_cancelled_lookup_facts(stores):
    stage, serving = stores
    partial = flight().model_copy(update={"provider_id": "partial", "departure_scheduled": None})
    stage.upsert([partial, flight("cancelled", status="cancelled")])
    assert publish_day(stage.path, serving.path, DAY)["inserted"] == 2
    reader = FlightReader(serving.path)
    assert reader.flight("test", "partial").departure_scheduled is None
    assert reader.flight("test", "cancelled").status == "cancelled"
    assert reader.available_dates("AAA", "UTC").dates == []


def test_failed_publication_rolls_back_and_rejects_wrong_store_and_alias(stores, tmp_path):
    stage, serving = stores
    stage.upsert([flight()])
    set_quota(serving.path, 0)
    with pytest.raises(OSError, match="quota"):
        publish_day(stage.path, serving.path, DAY)
    assert FlightReader(serving.path).stats()["flights"] == 0
    set_quota(serving.path, 20 * 1024**3)
    assert publish_day(stage.path, serving.path, DAY)["inserted"] == 1
    other = IngestionStore(tmp_path / "other.db")
    other.initialize()
    with pytest.raises(ValueError, match="different ingestion store"):
        publish_day(other.path, serving.path, DAY)
    alias = tmp_path / "alias.db"
    alias.hardlink_to(stage.path)
    with pytest.raises(ValueError, match="different files"):
        publish_day(stage.path, alias, DAY)


def test_provider_api_rejects_serving_and_refuses_clear_during_fetch(stores):
    stage, serving = stores

    class Provider:
        name = "test"

        async def flights_for_airport(self, *args):
            with pytest.raises(ValueError, match="active ingestion"):
                stage.clear()
            return [flight()]

    with pytest.raises(ValueError, match="IngestionStore"):
        IngestionService(serving, Provider(), 31)
    result = asyncio.run(IngestionService(stage, Provider(), 31).ingest(["AAA", "BBB"], DAY, DAY))
    assert result.upserted == 1
    assert FlightReader(serving.path).stats()["flights"] == 0


def test_admin_cannot_launch_jobs_or_overwrite_database_through_export(stores, tmp_path):
    stage, _ = stores
    with pytest.raises(SystemExit):
        admin.run(["--db", str(stage.path), "ingest", "adsblol", "run"])
    for destination in [stage.path, Path(str(stage.path) + "-wal")]:
        with pytest.raises(ValueError, match="overwrite"):
            admin.run(["--db", str(stage.path), "export-json", str(destination)])
    alias = tmp_path / "alias.json"
    alias.hardlink_to(stage.path)
    with pytest.raises(ValueError, match="overwrite"):
        admin.run(["--db", str(stage.path), "export-json", str(alias)])


def test_cli_defaults_to_separate_store_and_imports_timestamped_evidence(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(
        database_path=tmp_path / "serving.db", ingestion_database_path=tmp_path / "ingestion.db"
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    cli.run(["init"])
    assert ingestion_path(settings.ingestion_database_path) and not settings.database_path.exists()
    document = tmp_path / "record.json"
    document.write_text(
        json.dumps(
            dict(
                source="test",
                kind="aircraft",
                key="tail",
                payload={"type": "A320"},
                source_url="https://example.test",
                valid_from="2024-01-01T00:00:00Z",
            )
        )
    )
    cli.run(["document", str(document)])
    capsys.readouterr()
    with FlightReader(settings.ingestion_database_path).connect() as c:
        assert c.execute("SELECT valid_from_us FROM ingestion_documents").fetchone()[0] == int(
            datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1e6
        )


def test_publication_uses_private_date_index_and_preserves_airport_provenance(stores):
    from core.storage import source_id
    from ingestion.publish import PARTITION_TIME_SQL, event_us

    stage, serving = stores
    item = flight()
    stage.upsert([item])
    with stage.connect() as c:
        sid = source_id(c, "airport-reference")
        c.execute("UPDATE data_sources SET license_expression='Public Domain' WHERE id=?", (sid,))
        c.execute(
            "UPDATE airports SET name='Origin',city_id='MET',timezone='UTC',latitude=1,longitude=2,metadata_source_id=?,metadata_updated_us=1 WHERE iata='AAA'",
            (sid,),
        )
        c.execute("UPDATE airports SET name='Destination',city_id='MET' WHERE iata='BBB'")
        c.execute("INSERT INTO airport_transfers VALUES('AAA','BBB',90,'reference')")
        plan = c.execute(
            f"EXPLAIN QUERY PLAN SELECT source_record_key FROM flight_legs WHERE {PARTITION_TIME_SQL}>=? AND {PARTITION_TIME_SQL}<?",
            (event_us(item), event_us(item) + 1),
        ).fetchall()
        assert any("USING INDEX ingestion_publication_time" in row[3] for row in plan)
    # Force source-ID remapping in the output.
    with serving.connect() as c:
        source_id(c, "existing")
    publish_day(stage.path, serving.path, DAY)
    with serving.connect() as c:
        row = c.execute(
            "SELECT a.name,a.metadata_updated_us,s.code,s.license_expression FROM airports a JOIN data_sources s ON s.id=a.metadata_source_id WHERE a.iata='AAA'"
        ).fetchone()
        assert tuple(row) == ("Origin", 1, "airport-reference", "Public Domain")
        assert c.execute("SELECT minimum_minutes FROM airport_transfers").fetchone()[0] == 90
        c.execute("UPDATE airports SET name='Admin edit' WHERE iata='AAA'")
        c.execute("UPDATE airport_transfers SET minimum_minutes=120")
    publish_day(stage.path, serving.path, DAY)
    with serving.connect() as c:
        assert c.execute("SELECT name FROM airports WHERE iata='AAA'").fetchone()[0] == "Admin edit"
        assert c.execute("SELECT minimum_minutes FROM airport_transfers").fetchone()[0] == 120


def test_publication_projected_growth_rolls_back_fact_and_catalog(stores):
    from core.disk_quota import database_bytes

    stage, serving = stores
    stage.upsert([flight()])
    with stage.connect() as c:
        c.execute("UPDATE airports SET name=? WHERE iata='AAA'", ("x" * 600000,))
    set_quota(serving.path, database_bytes(serving.path) + 256 * 1024)
    with pytest.raises(OSError, match="quota"):
        publish_day(stage.path, serving.path, DAY)
    with serving.connect() as c:
        assert c.execute("SELECT count(*) FROM flight_legs").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM airports").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM publication_runs").fetchone()[0] == 0


def test_producer_refuses_existing_serving_before_network(stores, tmp_path, monkeypatch):
    from test_adsblol_extraction import run
    from test_adsblol_legs import leg_trace

    from ingestion.adsblol import extraction

    _, serving = stores

    def unexpected(*args, **kwargs):
        raise AssertionError("wrong-role target reached network")

    monkeypatch.setattr(extraction, "download_manifest", unexpected)
    with pytest.raises(ValueError, match="ingestion database"):
        run(tmp_path / "inputs", DAY, [leg_trace()], output_db=serving.path)

    class Provider:
        name = "never"

        async def flights_for_airport(self, *args):
            unexpected()

    with pytest.raises(ValueError, match="ingestion database"):
        asyncio.run(
            IngestionService(IngestionStore(serving.path), Provider(), 31).ingest(["AAA"], DAY, DAY)
        )
    assert FlightReader(serving.path).stats()["flights"] == 0


@pytest.mark.parametrize("confirmed", [False, True])
def test_reset_guards_precede_schema_access_for_unrelated_files(tmp_path, confirmed):
    sentinel = tmp_path / "outside.db"
    sentinel.write_bytes(b"keep this file")
    arguments = ["--db", str(sentinel), "maintenance", "clear-data"]
    if confirmed:
        arguments.append("--yes")
    with pytest.raises(
        ValueError, match="project's var directory" if confirmed else "supply --yes"
    ):
        admin.run(arguments)
    assert sentinel.read_bytes() == b"keep this file"
    assert not ingestion_path(sentinel)
