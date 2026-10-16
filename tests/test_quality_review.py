"""Confidence labels, organic reference checks and explicit smart-review decisions."""

import json
import sqlite3
from datetime import date

import pytest
from test_vrs import AIRPORTS, ROUTES, fact

from admin import client as admin
from core.models import RouteVerification
from ingestion import cli, review, vrs
from ingestion.publish import publish_day
from ingestion.store import IngestionStore
from server.reader import FlightReader


@pytest.fixture
def store(tmp_path):
    store = IngestionStore(tmp_path / "private.db")
    store.initialize()
    vrs.import_snapshot(store, ROUTES, AIRPORTS)
    return store


def labels(record):
    return {x["code"]: x["severity"] for x in record.quality["labels"]}


def test_labels_distinguish_information_uncertainty_and_conflicts_without_erasing_evidence():
    record = fact("sample", review_reasons=["airborne_airport_estimate", "observation_gap"])
    assert record.quality["level"] == "uncertain"
    assert labels(record)["observation_gap"] == "caution"
    proof = RouteVerification(
        match_kind="exact",
        reference_sha256="a" * 64,
        callsign="ONE1",
        origin_iata="AAA",
        destination_iata="BBB",
        reference_airports=["AAA", "BBB"],
    )
    record = record.model_copy(update={"route_verification": proof})
    assert record.quality["level"] == "noted" and record.route_verified
    assert "estimated_airports" not in labels(record)
    assert labels(record)["observation_gap"] == "info"
    conflict = record.model_copy(
        update={"review_reasons": ["implausible_airport_timing"], "itinerary_reviewed": True}
    )
    assert conflict.quality["level"] == "conflict"
    assert labels(conflict)["timing_conflict"] == "warning"
    assert labels(conflict)["manually_reviewed"] == "info"
    assert not conflict.needs_review
    assert "implausible_airport_timing" in conflict.model_dump()["review_reasons"]


def test_all_reference_outcomes_are_durable_and_stale_route_changes_invalidate_them(store):
    store.upsert(
        [
            fact("match"),
            fact("different", "ONE1", "BBB", "AAA"),
            fact("missing", "NODATA"),
            fact("incomplete", "MISS3"),
        ]
    )
    vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    reader = FlightReader(store.path)
    for key, result in [
        ("match", "exact"),
        ("different", "different"),
        ("missing", "missing"),
        ("incomplete", "incomplete"),
    ]:
        record = reader.flight("adsblol", key)
        assert record.route_verification.match_kind == result
        assert record.route_verified == (result == "exact")
    record = reader.flight("adsblol", "match").model_copy(update={"destination_iata": "CCC"})
    assert not record.route_verified and record.current_route_reference is None
    assert "vrs_not_checked" in labels(record)


def test_daily_publication_organically_audits_cached_reference_without_network(
    store, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        vrs, "download", lambda *_: pytest.fail("Cached publication must not fetch")
    )
    store.upsert([fact("one")])
    target = tmp_path / "serving.db"
    result = publish_day(store.path, target, date(2023, 7, 10))
    assert result["route_audit"]["status"] == "checked"
    assert result["inserted"] == 1
    served = FlightReader(target).flight("adsblol", "one")
    assert served.route_verified and not served.itinerary_reviewed
    assert served.quality["level"] == "noted"


def test_fresh_producer_publish_recovers_bulk_reference_automatically(
    tmp_path, monkeypatch, capsys
):
    store = IngestionStore(tmp_path / "fresh.db")
    store.initialize()
    store.upsert([fact("one")])
    seen = []

    def download(name):
        seen.append(name)
        return (ROUTES if name == "routes" else AIRPORTS), dict(url=vrs.BASE_URL + name + ".csv")

    monkeypatch.setattr(vrs, "download", download)
    target = tmp_path / "public.db"
    cli.run(
        [
            "--db",
            str(store.path),
            "publish",
            "--serving-db",
            str(target),
            "--start",
            "2023-07-10",
            "--end",
            "2023-07-10",
        ]
    )
    capsys.readouterr()
    assert seen == ["routes", "airports"]
    assert FlightReader(target).flight("adsblol", "one").route_verified


def test_group_acknowledgement_does_not_bulk_verify_and_new_data_reopens_cohort(store):
    store.upsert([fact("one", "NODATA"), fact("two", "NODATA")])
    vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    queued = review.queue(store.path)
    assert queued["pending_records"] == 2 and len(queued["cohorts"]) == 1
    case = review.show(store.path, queued["cohorts"][0]["selection"])
    assert case["records"] == 2 and len(case["samples"]) == 1
    with pytest.raises(ValueError, match="one record"):
        review.decide(
            store.path, case, action="approve", note="Batch must not become human approval"
        )
    result = review.decide(
        store.path, case, action="acknowledge", note="Reviewed common coverage limitation"
    )
    assert not result["publication_required"]
    assert review.queue(store.path)["cohorts"] == []
    assert review.queue(store.path, include_acknowledged=True)["cohorts"][0]["acknowledged"]
    assert all(
        not FlightReader(store.path).flight("adsblol", key).itinerary_reviewed
        for key in ["one", "two"]
    )
    store.upsert([fact("three", "NODATA")])
    assert review.queue(store.path)["pending_records"] == 1  # New, unaudited cohort.
    vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    assert review.queue(store.path)["pending_records"] == 3


def test_review_case_detects_changed_flight_or_airport_metadata(store):
    store.upsert([fact("one", "NODATA")])
    case = review.show(store.path, dict(record_key="one"))
    with store.connect() as c:
        c.execute("UPDATE airports SET name='Updated airport' WHERE iata='AAA'")
    with pytest.raises(ValueError, match="changed"):
        review.decide(store.path, case, action="acknowledge", note="Old evidence")


def test_single_approval_is_protected_and_rejection_retains_source_but_retracts_serving(
    store, tmp_path
):
    store.upsert([fact("approve", "NODATA"), fact("reject")])
    case = review.show(store.path, dict(record_key="approve"))
    review.decide(store.path, case, action="approve", note="Explicit single-record review")
    assert FlightReader(store.path).flight("adsblol", "approve").itinerary_reviewed
    target = tmp_path / "public.db"
    publish_day(store.path, target, date(2023, 7, 10))
    case = review.show(store.path, dict(record_key="reject"))
    review.decide(store.path, case, action="reject", note="Single incorrect candidate")
    result = publish_day(store.path, target, date(2023, 7, 10))
    assert result["retired"] == 1
    with store.connect() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM flight_legs WHERE source_record_key='reject'"
            ).fetchone()[0]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) FROM flight_tombstones WHERE source_record_key='reject'"
            ).fetchone()[0]
            == 1
        )


def test_admin_review_is_readonly_and_producer_reports_cannot_overwrite_database(store, capsys):
    store.upsert([fact("one", "NODATA")])
    admin.run(["--db", str(store.path), "review", "queue"])
    assert json.loads(capsys.readouterr().out)["pending_records"] == 1
    with pytest.raises(SystemExit):
        admin.run(["--db", str(store.path), "review", "decide"])
    with pytest.raises(ValueError, match="overwrite a database"):
        cli.run(["--db", str(store.path), "review", "queue", "--report", str(store.path)])
    with sqlite3.connect(store.path) as c:
        assert c.execute("SELECT count(*) FROM flight_legs").fetchone()[0] == 1


def test_quality_model_and_read_filters_agree_across_reference_results(store):
    from core.quality import review_recommended_sql

    records = []
    for index, (reasons, kind, reference, reviewed) in enumerate(
        [
            ([], "source_reported", None, False),
            (["airborne_airport_estimate"], "inferred_airport_proximity", None, False),
            (["observation_gap"], "inferred_ground_brackets", "exact", False),
            (["observation_gap"], "inferred_ground_brackets", "different", False),
            (["callsign_variation"], "inferred_ground_brackets", "exact", False),
            (["implausible_airport_timing"], "inferred_ground_brackets", "exact", False),
            (["implausible_airport_timing"], "inferred_ground_brackets", "different", True),
            ([], "source_reported", "different", False),
            ([], "source_reported", "missing", False),
            ([], "source_reported", "incomplete", False),
        ]
    ):
        record = fact(
            str(index),
            review_reasons=reasons,
            evidence_kind=kind,
            itinerary_reviewed=reviewed,
            departure_actual=None,
            arrival_actual=None,
            departure_observed_lower="2023-07-10T09:59:00Z",
            departure_observed_upper="2023-07-10T10:01:00Z",
            arrival_observed_lower="2023-07-10T10:59:00Z",
            arrival_observed_upper="2023-07-10T11:01:00Z",
        )
        if reference:
            record.route_verification = RouteVerification(
                match_kind=reference,
                reference_sha256="a" * 64,
                callsign="ONE1",
                origin_iata="AAA",
                destination_iata="BBB",
            )
        records.append(record)
    store.upsert(records)
    expected = {r.provider_id for r in records if r.quality["review_recommended"]}
    with store.connect() as c:
        actual = {
            r[0]
            for r in c.execute(
                "SELECT f.source_record_key FROM flight_legs f WHERE " + review_recommended_sql("f")
            )
        }
    assert actual == expected
    # Source mutations make both positive and negative route references stale.
    with store.connect() as c:
        c.execute("UPDATE flight_legs SET callsign='NEW1'")
        rows = c.execute("SELECT * FROM flights").fetchall()
        actual = {
            r[0]
            for r in c.execute(
                "SELECT source_record_key FROM flight_legs WHERE " + review_recommended_sql()
            )
        }
    from core.storage import flight_record

    expected = {r["provider_id"] for r in rows if flight_record(r).quality["review_recommended"]}
    assert actual == expected


def test_correction_validates_identity_and_invalidates_changed_route_reference(store):
    store.upsert([fact("one")])
    vrs.admit(store.path, start="2023-07-10", end="2023-07-10", apply=True)
    case = review.show(store.path, dict(record_key="one"))
    replacement = case["samples"][0]["record"] | dict(provider_id="changed")
    with pytest.raises(ValueError, match="source identity"):
        review.decide(
            store.path, case, action="correct", note="Wrong identity", replacement=replacement
        )
    replacement = case["samples"][0]["record"] | dict(destination_iata="CCC")
    review.decide(
        store.path,
        case,
        action="correct",
        note="Evidence-based correction",
        replacement=replacement,
    )
    corrected = FlightReader(store.path).flight("adsblol", "one")
    assert corrected.destination_iata == "CCC" and not corrected.itinerary_reviewed
    assert corrected.route_verification is None
    with pytest.raises(ValueError, match="changed"):
        review.decide(store.path, case, action="approve", note="Stale source")
    with store.connect() as c:
        assert c.execute("SELECT locally_modified_us FROM flight_legs").fetchone()[0]
        journal = json.loads(
            c.execute(
                "SELECT payload_json FROM ingestion_documents d JOIN data_sources s ON s.id=d.source_id WHERE s.code='review'"
            ).fetchone()[0]
        )
    assert journal["before"]["destination_iata"] == "BBB"
    assert journal["after"]["destination_iata"] == "CCC"


def test_review_write_respects_active_job_and_quota_without_partial_decisions(store):
    from pathlib import Path

    from core.disk_quota import set_quota
    from core.file_lock import file_mutex

    store.upsert([fact("one", "NODATA")])
    case = review.show(store.path, dict(record_key="one"))
    with file_mutex(Path(str(store.path) + ".job.lock")):
        with pytest.raises(ValueError, match="active ingestion"):
            review.decide(store.path, case, action="approve", note="Blocked active job")
    set_quota(store.path, 1)
    with pytest.raises(OSError, match="quota"):
        review.decide(store.path, case, action="approve", note="Blocked quota")
    assert not FlightReader(store.path).flight("adsblol", "one").itinerary_reviewed
    with sqlite3.connect(store.path) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM ingestion_documents d JOIN data_sources s ON s.id=d.source_id WHERE s.code='review'"
            ).fetchone()[0]
            == 0
        )


def test_missing_callsign_can_record_absence_but_cannot_claim_route_match():
    fields = dict(reference_sha256="a" * 64, origin_iata="AAA", destination_iata="BBB")
    assert RouteVerification(match_kind="missing", **fields).callsign is None
    with pytest.raises(ValueError, match="requires a callsign"):
        RouteVerification(match_kind="exact", **fields)
