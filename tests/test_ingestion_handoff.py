"""Finished-archive adoption and evidence-supported bulk review, without live writes."""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from test_adsblol_extraction import run
from test_adsblol_legs import leg_trace
from test_shared_flight_database import observed

from core.disk_quota import set_quota
from core.file_lock import file_mutex
from ingestion import adoption, audit, cli
from ingestion.adoption import adopt_archive
from ingestion.audit import audit_database
from ingestion.publish import publish_day
from ingestion.store import IngestionStore
from ingestion.updater import FlightUpdater
from server.reader import FlightReader

DAY = date(2025, 1, 1)


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "legacy"
    run(root, DAY, [leg_trace()])
    path = root / "itineraries.db"
    with sqlite3.connect(path) as c:
        # Keep the frozen v5 contract, not a hand-maintained list of today's
        # private extensions. Future private tables must not leak into this fixture.
        tables = [row[0] for row in c.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
        for name in tables:
            if name not in adoption.ARCHIVE_TABLES | {"ingestion_profiles"}:
                c.execute('DROP TABLE "' + name.replace('"', '""') + '"')
        c.execute("DROP INDEX ingestion_publication_time")
    writer = FlightUpdater(path)
    manual = observed("manual", "AAA", "BBB", 0, 3).model_copy(
        update={"itinerary_reviewed": True, "review_reasons": ["implausible_airport_timing"]}
    )
    writer.upsert([manual])
    writer.update(manual, manual)
    deleted = observed("deleted", "AAA", "BBB", 0, 60)
    writer.upsert([deleted])
    writer.delete("adsblol", "deleted")
    return path, root


def test_adoption_preserves_facts_receipts_boundaries_decisions_and_quota(archive, tmp_path):
    source, root = archive
    with sqlite3.connect(source) as c:
        original = adoption.table_digests(c)
        before_rows = c.execute(
            "SELECT source_record_key,itinerary_reviewed,locally_modified_us,aircraft_type_code FROM flight_legs ORDER BY id"
        ).fetchall()
    live = tmp_path / "canonical.db"
    FlightUpdater(live).initialize()
    set_quota(live, 7 * 1024**3)
    target = tmp_path / "private" / "ingestion.db"
    report = adopt_archive(source, target, DAY, DAY, source_root=root, policy_db=live)
    assert report["source_changed"] is report["serving_changed"] is False
    with sqlite3.connect(source) as c:
        assert adoption.table_digests(c) == original
        assert not c.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='ingestion_store_metadata'"
        ).fetchone()
    with sqlite3.connect(target) as c:
        assert (
            c.execute(
                "SELECT source_record_key,itinerary_reviewed,locally_modified_us,aircraft_type_code FROM flight_legs ORDER BY id"
            ).fetchall()
            == before_rows
        )
        assert c.execute("SELECT quota_bytes FROM database_policy").fetchone()[0] == 7 * 1024**3
        assert c.execute("SELECT COUNT(*) FROM extraction_days").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM flight_tombstones").fetchone()[0] == 1
        provenance = json.loads(
            c.execute("SELECT summary_json FROM ingestion_adoptions").fetchone()[0]
        )
        assert provenance["tables"] == original
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    with pytest.raises(sqlite3.DatabaseError):
        FlightReader(target, serving_only=True).stats()
    # Explicit publication remains the only route to serving; no private state leaks.
    serving = tmp_path / "published.db"
    assert publish_day(target, serving, DAY)["inserted"] == 2
    assert FlightReader(serving).flight("adsblol", "manual").itinerary_reviewed
    with sqlite3.connect(serving) as c:
        assert not c.execute(
            "SELECT 1 FROM sqlite_schema WHERE name IN ('ingestion_adoptions','ingestion_audit_runs','extraction_days')"
        ).fetchone()


@pytest.mark.parametrize("suffix", [".job.lock", ".extraction.lock", ".producer.lock"])
def test_adoption_refuses_active_source_and_never_touches_target(archive, tmp_path, suffix):
    source, root = archive
    target = tmp_path / "new.db"
    with file_mutex(Path(str(source) + suffix)):
        with pytest.raises(ValueError, match="active producer"):
            adopt_archive(source, target, DAY, DAY, source_root=root)
    assert not target.exists()


def test_adoption_requires_complete_inputs_and_valid_receipts(archive, tmp_path):
    source, root = archive
    target = tmp_path / "new.db"
    with pytest.raises(ValueError, match="not complete"):
        adopt_archive(source, target, DAY, date(2025, 1, 2), source_root=root)
    with sqlite3.connect(source) as c:
        c.execute("UPDATE ingestion_batches SET pipeline_version='observed-itinerary-v4'")
    with pytest.raises(ValueError, match="incompatible"):
        adopt_archive(source, target, DAY, DAY, source_root=root)
    assert not target.exists()


def test_adoption_refuses_unfinished_batch_even_when_day_receipt_exists(archive, tmp_path):
    source, root = archive
    with sqlite3.connect(source) as c:
        c.execute(
            "UPDATE ingestion_batches SET state='running',finished_us=NULL,inserted_count=0,updated_count=0"
        )
    with pytest.raises(ValueError, match="running batch"):
        adopt_archive(source, tmp_path / "new.db", DAY, DAY, source_root=root)


@pytest.mark.parametrize("existing", ["file", "sidecar", "journal", "alias"])
def test_adoption_never_overwrites_database_family(archive, tmp_path, existing):
    source, root = archive
    target = tmp_path / "new.db"
    if existing == "alias":
        target.hardlink_to(source)
    elif existing in {"sidecar", "journal"}:
        Path(str(target) + ("-wal" if existing == "sidecar" else "-journal")).write_bytes(b"keep")
    else:
        target.write_bytes(b"keep")
    with pytest.raises(ValueError):
        adopt_archive(source, target, DAY, DAY, source_root=root)
    if existing == "file":
        assert target.read_bytes() == b"keep"
    if existing in {"sidecar", "journal"}:
        assert (
            Path(str(target) + ("-wal" if existing == "sidecar" else "-journal")).read_bytes()
            == b"keep"
        )


def test_adoption_failed_validation_or_quota_leaves_no_destination(archive, tmp_path, monkeypatch):
    source, root = archive
    target = tmp_path / "new.db"
    set_quota(source, 0)
    with pytest.raises(OSError, match="quota"):
        adopt_archive(source, target, DAY, DAY, source_root=root)
    assert not target.exists() and not list(tmp_path.glob(".adopt-*"))
    set_quota(source, 20 * 1024**3)

    def broken(self):
        raise ValueError("injected validation failure")

    monkeypatch.setattr(adoption.IngestionStore, "initialize", broken)
    with pytest.raises(ValueError, match="injected"):
        adopt_archive(source, target, DAY, DAY, source_root=root)
    assert not target.exists() and not list(tmp_path.glob(".adopt-*"))
    assert len(FlightReader(source).all_records()) == 2


def test_adoption_rejects_serving_or_already_tagged_store(tmp_path):
    for writer in [
        FlightUpdater(tmp_path / "serving.db"),
        IngestionStore(tmp_path / "ingestion.db"),
    ]:
        writer.initialize()
        with pytest.raises(ValueError):
            adopt_archive(
                writer.path, tmp_path / "new.db", DAY, DAY, source_root=tmp_path / "inputs"
            )


@pytest.fixture
def candidates(tmp_path):
    store = IngestionStore(tmp_path / "private.db")
    store.initialize()

    def item(key, minutes=60, reasons=(), kind="inferred_ground_brackets", **changes):
        return observed(key, "AAA", "BBB", 0, minutes).model_copy(
            update={"review_reasons": list(reasons), "evidence_kind": kind, **changes}
        )

    store.upsert(
        [
            item("fast", minutes=3),
            item("stale", reasons=["implausible_airport_timing"]),
            item("estimated", kind="inferred_airport_proximity"),
            item("ground", reasons=["airborne_airport_estimate"]),
            item("gap", reasons=["observation_gap"]),
            item(
                "gap-estimate",
                reasons=["observation_gap", "airborne_airport_estimate"],
                kind="inferred_airport_proximity",
            ),
            item("approved", minutes=3, itinerary_reviewed=True),
            item("edited", minutes=3),
            item("unknown", reasons=["implausible_airport_timing"], origin_iata="CCC"),
        ]
    )
    with store.connect() as c:
        c.execute("UPDATE airports SET latitude=40,longitude=-70 WHERE iata='AAA'")
        c.execute("UPDATE airports SET latitude=41,longitude=-71 WHERE iata='BBB'")
        c.execute("UPDATE flight_legs SET locally_modified_us=1 WHERE source_record_key='edited'")
    return store


def test_audit_reports_cohorts_and_supported_corrections_without_writing(candidates):
    store = candidates
    with store.connect() as c:
        before = [tuple(r) for r in c.execute("SELECT * FROM flight_legs ORDER BY id")]
    result = audit_database(store.path)
    assert result["health"]["passed"]
    assert result["counts"]["records"] == 9
    assert result["findings"]["supported_flag_corrections"] == 4
    assert result["findings"]["protected_corrections"] == 2
    assert result["changes"]["applied"] == 0
    assert result["findings"]["gap_requires_observations_to_resolve"] == 2
    assert result["cohorts"][0]["severity"] == "timing"
    with store.connect() as c:
        assert [tuple(r) for r in c.execute("SELECT * FROM flight_legs ORDER BY id")] == before
        assert c.execute("SELECT COUNT(*) FROM ingestion_audit_runs").fetchone()[0] == 0


def test_audit_apply_journals_only_supported_flags_and_preserves_manuals_and_facts(candidates):
    store = candidates
    reader = FlightReader(store.path)
    original = {r.provider_id: r.model_dump() for r in reader.all_records()}
    result = audit_database(store.path, apply=True)
    assert result["changes"] == {"applied": 4, "skipped_concurrent": 0}
    assert reader.flight("adsblol", "fast").review_reasons == ["implausible_airport_timing"]
    assert reader.flight("adsblol", "stale").review_reasons == []
    assert reader.flight("adsblol", "estimated").review_reasons == ["airborne_airport_estimate"]
    assert reader.flight("adsblol", "ground").review_reasons == []
    assert reader.flight("adsblol", "unknown").review_reasons == ["implausible_airport_timing"]
    for key in ["approved", "edited", "gap", "gap-estimate"]:
        assert reader.flight("adsblol", key).model_dump() == original[key]
    for r in reader.all_records():
        current = r.model_dump()
        current["review_reasons"] = original[r.provider_id].get("review_reasons", [])
        old = original[r.provider_id].copy()
        old.setdefault("review_reasons", [])
        assert current == old
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM ingestion_audit_changes").fetchone()[0] == 4
        assert c.execute("SELECT SUM(itinerary_reviewed) FROM flight_legs").fetchone()[0] == 1
    again = audit_database(store.path, apply=True)
    assert again["changes"]["applied"] == 0
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM ingestion_audit_changes").fetchone()[0] == 4
    store.clear()
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM ingestion_audit_changes").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM ingestion_audit_runs").fetchone()[0] == 0


def test_audit_apply_requires_private_and_inactive_store(archive, candidates):
    source, _ = archive
    assert audit_database(source)["mode"] == "read_only"
    with pytest.raises(ValueError, match="private ingestion"):
        audit_database(source, apply=True)
    with file_mutex(Path(str(candidates.path) + ".job.lock")):
        with pytest.raises(ValueError, match="active ingestion"):
            audit_database(candidates.path, apply=True)


def test_audit_scoped_cohort_and_sample_limits(candidates):
    result = audit_database(
        candidates.path,
        start=DAY,
        end=DAY,
        origin="aaa",
        destination="BBB",
        reason="observation_gap",
        sample_limit=1,
        cohort_limit=1,
    )
    assert result["counts"]["records"] == 2 and result["counts"]["held"] == 2
    assert len(result["cohorts"]) == 1 and len(result["cohorts"][0]["samples"]) == 1
    with pytest.raises(ValueError):
        audit_database(candidates.path, start=DAY)
    with pytest.raises(ValueError):
        audit_database(candidates.path, sample_limit=0)


def test_audit_detects_possible_duplicates_without_merging(candidates):
    before = FlightReader(candidates.path).stats()["flights"]
    report = audit_database(candidates.path, apply=True)
    assert report["possible_duplicates"]["groups"] >= 1
    assert FlightReader(candidates.path).stats()["flights"] == before


def test_concurrent_edit_is_never_overwritten(candidates, monkeypatch):
    apply = audit._apply

    def edited(path, identity, patches, report, progress):
        with IngestionStore(path).connect() as c:
            c.execute(
                "UPDATE flight_legs SET callsign='NEW',revision=revision+1 WHERE source_record_key='fast'"
            )
            c.execute("UPDATE airports SET latitude=10 WHERE iata='AAA'")
        return apply(path, identity, patches, report, progress)

    monkeypatch.setattr(audit, "_apply", edited)
    result = audit_database(candidates.path, apply=True)
    assert result["changes"]["skipped_concurrent"] >= 1
    record = FlightReader(candidates.path).flight("adsblol", "fast")
    assert record.callsign == "NEW" and record.review_reasons == []


def test_quota_failure_rolls_back_flags_and_audit_journal(candidates):
    store = candidates
    with store.connect() as c:
        before = [tuple(r) for r in c.execute("SELECT * FROM flight_legs ORDER BY id")]
    set_quota(store.path, 0)
    with pytest.raises(OSError, match="quota"):
        audit_database(store.path, apply=True)
    with FlightReader(store.path).connect() as c:
        assert [tuple(r) for r in c.execute("SELECT * FROM flight_legs ORDER BY id")] == before
        assert c.execute("SELECT COUNT(*) FROM ingestion_audit_runs").fetchone()[0] == 0


def test_cli_reports_never_overwrite_database_and_admin_cannot_launch_tools(
    candidates, tmp_path, capsys
):
    from admin import client as admin

    with pytest.raises(ValueError, match="overwrite"):
        cli.run(
            ["--db", str(candidates.path), "audit", "--apply", "--report", str(candidates.path)]
        )
    for tool in ["audit", "adopt"]:
        with pytest.raises(SystemExit):
            admin.run(["--db", str(candidates.path), tool])
    report = tmp_path / "audit.json"
    cli.run(["--db", str(candidates.path), "audit", "--report", str(report)])
    assert json.loads(report.read_text())["counts"]["records"] == 9
    assert json.loads(capsys.readouterr().out)["report"] == str(report)


def test_publish_preserves_canonical_controls_without_copying_old_ingestion(tmp_path):
    from ingestion.publish import publish_range
    from ingestion.serving_controls import verify_serving_controls

    canonical = FlightUpdater(tmp_path / "canonical.db")
    canonical.initialize()
    old = observed("old-only", "AAA", "BBB", 0, 60)
    edited = observed("edited", "AAA", "BBB", 0, 60)
    manual = observed("manual-route", "AAA", "BBB", 0, 60).model_copy(update={"provider": "manual"})
    gone = observed("gone", "AAA", "BBB", 0, 60)
    canonical.upsert([old, edited, manual, gone])
    canonical.update(edited, edited.model_copy(update={"aircraft_type_code": "B738"}))
    canonical.delete("adsblol", "gone")
    set_quota(canonical.path, 7 * 1024**3)
    with canonical.connect() as c:
        c.execute(
            "UPDATE airports SET name='Admin Airport',city_id='MET',timezone='UTC' WHERE iata='AAA'"
        )
        c.execute("UPDATE airports SET city_id='MET' WHERE iata='BBB'")
        c.execute("INSERT INTO airport_transfers VALUES('AAA','BBB',90,'admin rule')")
    stage = IngestionStore(tmp_path / "ingestion.db")
    stage.initialize()
    stage.upsert([edited, gone, observed("new", "AAA", "BBB", 0, 60)])
    target = tmp_path / "replacement.db"
    list(publish_range(stage.path, target, DAY, DAY, preserve_serving=canonical.path))
    reader = FlightReader(target)
    assert {r.provider_id for r in reader.all_records()} == {"manual-route", "edited", "new"}
    assert reader.flight("adsblol", "edited").aircraft_type_code == "B738"
    assert verify_serving_controls(canonical.path, target)["unchanged"]
    with reader.connect() as c:
        assert c.execute("SELECT quota_bytes FROM database_policy").fetchone()[0] == 7 * 1024**3
        assert (
            c.execute("SELECT name FROM airports WHERE iata='AAA'").fetchone()[0] == "Admin Airport"
        )
        assert c.execute("SELECT minimum_minutes FROM airport_transfers").fetchone()[0] == 90
        assert c.execute("SELECT count(*) FROM flight_tombstones").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM ingestion_batches").fetchone()[0] == 0
    # A retry is safe, but new canonical edits block stale cutover and require a
    # fresh replacement. No destination or canonical edits are silently discarded.
    assert (
        len(list(publish_range(stage.path, target, DAY, DAY, preserve_serving=canonical.path))) == 1
    )
    set_quota(canonical.path, 6 * 1024**3)
    with pytest.raises(ValueError, match="administrative state changed"):
        list(publish_range(stage.path, target, DAY, DAY, preserve_serving=canonical.path))
    assert reader.flight("adsblol", "edited").aircraft_type_code == "B738"


def test_serving_control_seed_refuses_existing_untracked_or_private_targets(tmp_path):
    from ingestion.serving_controls import prepare_serving

    canonical = FlightUpdater(tmp_path / "canonical.db")
    canonical.initialize()
    existing = FlightUpdater(tmp_path / "existing.db")
    existing.initialize()
    private = IngestionStore(tmp_path / "ingestion.db")
    private.initialize()
    with pytest.raises(ValueError, match="no canonical-control snapshot"):
        prepare_serving(canonical.path, existing.path)
    with pytest.raises(sqlite3.DatabaseError):
        prepare_serving(canonical.path, private.path)
    with pytest.raises(sqlite3.DatabaseError):
        prepare_serving(private.path, tmp_path / "new.db")
    with pytest.raises(ValueError, match="different new replacement"):
        prepare_serving(canonical.path, canonical.path)


def test_audit_refuses_changed_store_generation(candidates, monkeypatch):
    apply = audit._apply

    def changed(path, identity, patches, report, progress):
        with candidates.connect() as c:
            c.execute("UPDATE ingestion_store_metadata SET generation=generation+1")
        return apply(path, identity, patches, report, progress)

    monkeypatch.setattr(audit, "_apply", changed)
    with pytest.raises(ValueError, match="reset/replaced"):
        audit_database(candidates.path, apply=True)
    assert FlightReader(candidates.path).flight("adsblol", "fast").review_reasons == []
    with candidates.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM ingestion_audit_changes").fetchone()[0] == 0


def test_structural_failure_is_reported_and_never_corrected(candidates):
    with candidates.connect() as c:
        c.execute("PRAGMA ignore_check_constraints=ON")
        c.execute(
            "UPDATE flight_legs SET arrival_observed_lower_us=departure_observed_upper_us WHERE source_record_key='gap'"
        )
    report = audit_database(candidates.path)
    assert not report["health"]["passed"]
    with pytest.raises(ValueError, match="Structural audit failed"):
        audit_database(candidates.path, apply=True)
    with FlightReader(candidates.path).connect() as c:
        assert c.execute("SELECT COUNT(*) FROM ingestion_audit_changes").fetchone()[0] == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE flight_legs SET arrival_observed_lower_us=departure_observed_upper_us",
        "UPDATE flight_legs SET origin_iata=destination_iata",
        "UPDATE airports SET latitude=110",
    ],
)
def test_adoption_rejects_invalid_retained_contract_even_if_sqlite_allows_it(
    archive, tmp_path, statement
):
    source, root = archive
    with sqlite3.connect(source) as c:
        c.execute("PRAGMA ignore_check_constraints=ON")
        c.execute(statement)
    target = tmp_path / "new.db"
    with pytest.raises(ValueError, match="validation failed"):
        adopt_archive(source, target, DAY, DAY, source_root=root)
    assert not target.exists()


def test_adoption_preserves_old_selected_producer_configuration_privately(archive, tmp_path):
    source, root = archive
    selected = tmp_path / "selected.db"
    with sqlite3.connect(selected) as c:
        c.execute(
            "CREATE TABLE ingestion_profiles(source TEXT PRIMARY KEY,data TEXT,last_used INTEGER)"
        )
        c.execute(
            "INSERT INTO ingestion_profiles VALUES(?,?,?)",
            (
                "adsblol",
                json.dumps(
                    {"data_root": str(root), "output_db": str(source), "manifest": "manifest.json"}
                ),
                2,
            ),
        )
        c.execute(
            "INSERT INTO ingestion_profiles VALUES(?,?,?)",
            ("csv", json.dumps({"csv": "licensed.csv", "airports": ["AAA"]}), 1),
        )
    target = tmp_path / "private.db"
    result = adopt_archive(source, target, DAY, DAY, source_root=root, profiles_db=selected)
    assert set(result["profiles_copied"]) == {"adsblol", "csv"}
    with sqlite3.connect(target) as c:
        profile = json.loads(
            c.execute("SELECT data FROM ingestion_profiles WHERE source='adsblol'").fetchone()[0]
        )
        assert profile["output_db"] == str(target) and profile["data_root"] == str(root)
        assert (
            json.loads(
                c.execute("SELECT data FROM ingestion_profiles WHERE source='csv'").fetchone()[0]
            )["csv"]
            == "licensed.csv"
        )
    with sqlite3.connect(selected) as c:
        assert json.loads(
            c.execute("SELECT data FROM ingestion_profiles WHERE source='adsblol'").fetchone()[0]
        )["output_db"] == str(source)
