"""Admission must agree in SQL, routing, admin and retained manual decisions."""

import json
from datetime import date

import pytest
from test_adsblol_extraction import run
from test_adsblol_legs import leg_trace
from test_shared_flight_database import observed, query

from admin import client as admin
from admin.admission import apply_admission
from core.disk_quota import set_quota
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot
from core.storage import SCHEMA
from ingestion.updater import FlightUpdater
from server.reader import FlightReader


@pytest.mark.parametrize(
    "reasons,blocked",
    [
        ([], False),
        (["airborne_airport_estimate"], False),
        (["airborne_airport_estimate", "airborne_airport_estimate"], False),
        (["observation_gap"], True),
        (["implausible_airport_timing"], True),
        (["callsign_variation"], True),
        (["airborne_airport_estimate", "observation_gap"], True),
        (["airborne_airport_estimate", "implausible_airport_timing"], True),
    ],
)
@pytest.mark.parametrize("reviewed", [False, True])
def test_model_sql_calendar_routing_and_admin_agree(tmp_path, reasons, blocked, reviewed):
    updater = FlightUpdater(tmp_path / "data.db")
    updater.initialize()
    record = observed("sample", "AAA", "BBB", 10, 70).model_copy(
        update={
            "review_reasons": reasons,
            "itinerary_reviewed": reviewed,
            "evidence_kind": "inferred_airport_proximity",
        }
    )
    updater.upsert([record])
    reader = FlightReader(updater.path)
    held = blocked and not reviewed
    assert record.needs_review == held
    with reader.connect() as c:
        assert c.execute("SELECT COUNT(DISTINCT leg_id) FROM searchable_legs").fetchone()[0] == int(
            not held
        )
    assert reader.list_flights(needs_review=True) == ([record] if held else [])
    assert reader.available_dates("AAA", "UTC").dates == ([] if held else [date(2025, 1, 1)])
    assert len(reader.search_itineraries(query()).items) == int(not held)
    assert len(search_routes(query(), RoutingSnapshot((record,))).items) == int(not held)
    facts = reader.web_catalog().dataset
    assert facts.flight_count == int(not held) and facts.needs_review_count == int(held)
    assert reader.overview()["needs_review_count"] == int(held)
    assert reader.flight("adsblol", "sample").itinerary_reviewed == reviewed


def legacy_view(c):
    marker = "CREATE VIEW IF NOT EXISTS searchable_legs AS"
    statement = marker + SCHEMA.split(marker, 1)[1].split(";", 1)[0] + ";"
    statement = statement.replace(
        "(f.itinerary_reviewed = 1 OR NOT EXISTS (SELECT 1 FROM json_each(f.review_reasons) AS reason WHERE reason.value IS NOT 'airborne_airport_estimate'))",
        "((f.evidence_kind <> 'inferred_airport_proximity' AND json_array_length(f.review_reasons) = 0) OR f.itinerary_reviewed = 1)",
    )
    c.execute("DROP VIEW searchable_legs")
    c.execute(statement)


def test_apply_existing_admission_is_idempotent_and_preserves_source_and_admin_state(
    tmp_path, capsys
):
    database = tmp_path / "hosted.db"
    run(tmp_path / "inputs", date(2025, 1, 1), [leg_trace()], output_db=database)
    capsys.readouterr()
    updater = FlightUpdater(database)
    estimated = observed("estimate", "AAA", "BBB", 10, 70).model_copy(
        update={
            "evidence_kind": "inferred_airport_proximity",
            "review_reasons": ["airborne_airport_estimate"],
        }
    )
    gap = estimated.model_copy(
        update={
            "provider_id": "gap",
            "review_reasons": ["airborne_airport_estimate", "observation_gap"],
        }
    )
    fast = observed("fast", "AAA", "BBB", 10, 13)
    manual = fast.model_copy(update={"provider_id": "manual", "itinerary_reviewed": True})
    updater.upsert([estimated, gap, fast, manual])
    updater.update(manual, manual)
    gone = estimated.model_copy(update={"provider_id": "deleted"})
    updater.upsert([gone])
    updater.delete("adsblol", "deleted")
    set_quota(database, 7 * 1024**2)
    with updater.connect() as c:
        legacy_view(c)
        keys = c.execute(
            "SELECT source_record_key FROM flight_legs ORDER BY source_record_key"
        ).fetchall()
        tombstones = [tuple(r) for r in c.execute("SELECT * FROM flight_tombstones")]
        original_receipts = [tuple(r) for r in c.execute("SELECT * FROM extraction_days")]
    admin.run(["--db", str(database), "maintenance", "apply-admission"])
    result = json.loads(capsys.readouterr().out)
    assert result["timing_flags_added"] == 1 and result["manual_approvals_added"] == 0
    reader = FlightReader(database)
    assert reader.flight("adsblol", "estimate").review_reasons == ["airborne_airport_estimate"]
    assert not reader.flight("adsblol", "estimate").itinerary_reviewed
    assert not reader.flight("adsblol", "estimate").needs_review
    assert reader.flight("adsblol", "fast").review_reasons == ["implausible_airport_timing"]
    assert reader.flight("adsblol", "gap").needs_review
    assert reader.flight("adsblol", "manual") == manual
    with reader.connect() as c:
        assert (
            c.execute(
                "SELECT source_record_key FROM flight_legs ORDER BY source_record_key"
            ).fetchall()
            == keys
        )
        assert [tuple(r) for r in c.execute("SELECT * FROM flight_tombstones")] == tombstones
        assert [tuple(r) for r in c.execute("SELECT * FROM extraction_days")] == original_receipts
        assert c.execute("SELECT quota_bytes FROM database_policy").fetchone()[0] == 7 * 1024**2
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    again = apply_admission(database)
    assert again["timing_flags_added"] == 0
    assert again["searchable_before"] == again["searchable_after"] == result["searchable_after"]


def test_manual_review_of_already_admitted_estimate_remains_explicit(tmp_path, capsys):
    updater = FlightUpdater(tmp_path / "data.db")
    updater.initialize()
    record = observed("estimate", "AAA", "BBB", 10, 70).model_copy(
        update={
            "evidence_kind": "inferred_airport_proximity",
            "review_reasons": ["airborne_airport_estimate"],
        }
    )
    updater.upsert([record])
    admin.run(["--db", str(updater.path), "flight", "approve", "adsblol", "estimate", "--yes"])
    assert json.loads(capsys.readouterr().out)["approved"] == 1
    reviewed = FlightReader(updater.path).flight("adsblol", "estimate")
    assert reviewed.itinerary_reviewed and reviewed.review_reasons == record.review_reasons


def test_admission_refresh_failure_rolls_back_flags_and_view(tmp_path, monkeypatch):
    from admin import admission

    updater = FlightUpdater(tmp_path / "data.db")
    updater.initialize()
    fast = observed("fast", "AAA", "BBB", 10, 13)
    updater.upsert([fast])
    with updater.connect() as c:
        c.executemany(
            "UPDATE airports SET latitude=?,longitude=? WHERE iata=?",
            [(40, -70, "AAA"), (41, -71, "BBB")],
        )
    with updater.connect() as c:
        legacy_view(c)
        old_view = c.execute(
            "SELECT sql FROM sqlite_master WHERE name='searchable_legs'"
        ).fetchone()[0]

    def fail_after_drop(connection):
        assert json.loads(
            connection.execute("SELECT review_reasons FROM flight_legs").fetchone()[0]
        ) == ["implausible_airport_timing"]
        connection.execute("DROP VIEW searchable_legs")
        raise RuntimeError("interrupted refresh")

    monkeypatch.setattr(admission, "refresh_admission_view", fail_after_drop)
    with pytest.raises(RuntimeError, match="interrupted refresh"):
        apply_admission(updater.path)
    assert FlightReader(updater.path).flight("adsblol", "fast") == fast
    with updater.connect() as c:
        assert (
            c.execute("SELECT sql FROM sqlite_master WHERE name='searchable_legs'").fetchone()[0]
            == old_view
        )


def test_admission_does_not_overwrite_concurrent_admin_edit(tmp_path, monkeypatch):
    from admin import admission

    updater = FlightUpdater(tmp_path / "data.db")
    updater.initialize()
    fast = observed("fast", "AAA", "BBB", 10, 13)
    updater.upsert([fast])
    with updater.connect() as c:
        c.executemany(
            "UPDATE airports SET latitude=?,longitude=? WHERE iata=?",
            [(40, -70, "AAA"), (41, -71, "BBB")],
        )
    corrected = fast.model_copy(update={"itinerary_reviewed": True})
    saved = []

    def classify_while_admin_edits(*args):
        updater.update(fast, corrected)
        saved.append(FlightReader(updater.path).flight("adsblol", "fast"))
        return True

    monkeypatch.setattr(admission, "airport_timing_is_implausible", classify_while_admin_edits)
    result = apply_admission(updater.path)
    assert result["timing_flags_added"] == 0
    assert saved[0].itinerary_reviewed and not saved[0].review_reasons
    assert FlightReader(updater.path).flight("adsblol", "fast") == saved[0]
