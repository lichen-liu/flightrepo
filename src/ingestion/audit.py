"""Bulk retained-fact audit and narrowly supported machine flag corrections."""

import json
from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryFile
from uuid import uuid4

from core.admission import requires_review, route_verified_sql
from core.database_roles import is_ingestion, require_ingestion
from core.disk_quota import database_bytes
from core.file_lock import file_mutex
from core.storage import EPOCH, SQLiteConnection, refresh_admission_view, utc_us
from ingestion.adsblol.legs import distance_km
from ingestion.publish import PARTITION_TIME_SQL
from ingestion.store import IngestionStore
from ingestion.validation import semantic_health

VERSION = "retained-itinerary-audit-v2"
KNOWN_REASONS = {
    "airborne_airport_estimate",
    "observation_gap",
    "callsign_variation",
    "implausible_airport_timing",
}


def scope_sql(start=None, end=None, origin=None, destination=None, reason=None):
    clauses, values = [], []
    if (start is None) != (end is None):
        raise ValueError("Audit requires both --start and --end, or neither")
    if start is not None:
        start = date.fromisoformat(start) if isinstance(start, str) else start
        end = date.fromisoformat(end) if isinstance(end, str) else end
        if end < start:
            raise ValueError("Audit end must follow start")
        clauses.append(f"({PARTITION_TIME_SQL})>=? AND ({PARTITION_TIME_SQL})<?")
        values += [
            utc_us(datetime.combine(start, time.min, UTC)),
            utc_us(datetime.combine(end + timedelta(days=1), time.min, UTC)),
        ]
    for column, value in [("origin_iata", origin), ("destination_iata", destination)]:
        if value is not None:
            if len(value) != 3 or not value.isascii() or not value.isalpha():
                raise ValueError("Audit airport filters require three ASCII letters")
            clauses.append(f"f.{column}=?")
            values.append(value.upper())
    if reason is not None:
        if reason not in KNOWN_REASONS:
            raise ValueError("Unknown review reason")
        clauses.append("EXISTS(SELECT 1 FROM json_each(f.review_reasons) WHERE value=?)")
        values.append(reason)
    clauses.append(
        "NOT EXISTS(SELECT 1 FROM flight_tombstones t WHERE t.source_id=f.source_id AND t.source_record_key=f.source_record_key)"
    )
    return " AND ".join(clauses), values


def assessment(row):
    """Never infer new endpoints/times/types or erase a continuity hold."""
    reasons = json.loads(row["review_reasons"])
    if not isinstance(reasons, list) or any(not isinstance(x, str) for x in reasons):
        raise ValueError("Invalid review-reason representation")
    facts = dict(
        origin=row["origin_iata"],
        destination=row["destination_iata"],
        departure_lower_us=row["departure_observed_lower_us"],
        arrival_upper_us=row["arrival_observed_upper_us"],
        coordinates=[
            row["origin_lat"],
            row["origin_lon"],
            row["destination_lat"],
            row["destination_lon"],
        ],
    )
    timing = None
    if (
        row["provider"] == "adsblol"
        and all(x is not None for x in facts["coordinates"])
        and facts["departure_lower_us"] is not None
        and facts["arrival_upper_us"] is not None
    ):
        elapsed = (facts["arrival_upper_us"] - facts["departure_lower_us"]) / 1_000_000
        if elapsed > 0:
            km = distance_km(*facts["coordinates"])
            facts.update(
                distance_km=round(km, 3), minimum_required_kmh=round(km * 3600 / elapsed, 3)
            )
            timing = km > 1500 * elapsed / 3600
    corrected = list(reasons)
    if row["provider"] == "adsblol":
        if timing is True and "implausible_airport_timing" not in corrected:
            corrected.append("implausible_airport_timing")
        elif timing is False:
            corrected = [x for x in corrected if x != "implausible_airport_timing"]
        if (
            row["evidence_kind"] == "inferred_airport_proximity"
            and "airborne_airport_estimate" not in corrected
        ):
            corrected.append("airborne_airport_estimate")
        elif row["evidence_kind"] == "inferred_ground_brackets":
            corrected = [x for x in corrected if x != "airborne_airport_estimate"]
    protected = row["locally_modified_us"] is not None or bool(row["itinerary_reviewed"])
    return reasons, corrected, timing, protected, facts


def audit_database(
    path,
    *,
    apply=False,
    start=None,
    end=None,
    origin=None,
    destination=None,
    reason=None,
    sample_limit=3,
    cohort_limit=50,
    progress=None,
):
    path = Path(path).expanduser().resolve()
    if not 1 <= sample_limit <= 20 or not 1 <= cohort_limit <= 500:
        raise ValueError("Samples must be 1..20 and cohorts 1..500")
    where, values = scope_sql(start, end, origin, destination, reason)
    started = datetime.now(UTC)
    # Apply excludes ingestion jobs across the scan and transaction. Direct tool
    # updates are still handled by generation/revision/airport comparisons below.
    with (
        file_mutex(Path(str(path) + ".job.lock"), blocking=False)
        if apply
        else nullcontext(True) as acquired
    ):
        if not acquired:
            raise ValueError("Finish the active ingestion job before applying an audit")
        with TemporaryFile(mode="w+t", encoding="utf-8") as patches:
            with SQLiteConnection(path, read_only=True).connect() as source:
                source.execute("BEGIN")
                contract = source.execute(
                    "SELECT contract,version FROM schema_metadata WHERE id=1"
                ).fetchone()
                if tuple(contract or ()) != ("flight-legs", 1):
                    raise ValueError("Unsupported flight contract")
                identity = None
                if is_ingestion(source):
                    require_ingestion(source)
                    identity = tuple(
                        source.execute(
                            "SELECT store_id,generation FROM ingestion_store_metadata WHERE id=1"
                        ).fetchone()
                    )
                if apply and identity is None:
                    raise ValueError(
                        "Audit writes require the private ingestion database; legacy/serving files are read-only"
                    )
                integrity = [row[0] for row in source.execute("PRAGMA integrity_check(20)")]
                semantics = semantic_health(source)
                foreign_keys = [
                    tuple(row) for row in source.execute("PRAGMA foreign_key_check").fetchmany(20)
                ]
                counts = Counter(
                    dict.fromkeys(
                        [
                            "records",
                            "held",
                            "searchable",
                            "with_aircraft_type",
                            "manually_reviewed",
                            "locally_modified",
                        ],
                        0,
                    )
                )
                findings, reasons_count, months = Counter(), Counter(), Counter()
                cohorts = Counter()
                samples = defaultdict(list)
                missing_proposals = []
                columns = {r[1] for r in source.execute("PRAGMA table_info(flight_legs)")}
                verified = route_verified_sql("f") if "route_verification" in columns else "0"
                query = f"""SELECT f.*,({verified}) AS route_verified,s.code AS provider,a.latitude AS origin_lat,a.longitude AS origin_lon,
                    b.latitude AS destination_lat,b.longitude AS destination_lon,
                    ({PARTITION_TIME_SQL}) AS event_us
                    FROM flight_legs f JOIN data_sources s ON s.id=f.source_id
                    JOIN airports a ON a.iata=f.origin_iata JOIN airports b ON b.iata=f.destination_iata
                    WHERE {where} ORDER BY f.id"""
                for row in source.execute(query, values):
                    counts["records"] += 1
                    try:
                        old, new, timing, protected, evidence = assessment(row)
                    except (ValueError, TypeError):
                        findings["invalid_review_reasons"] += 1
                        continue
                    held = requires_review(
                        old, bool(row["itinerary_reviewed"]), bool(row["route_verified"])
                    )
                    counts["held"] += held
                    complete = bool(
                        row["departure_observed_lower_us"] is not None
                        or row["departure_scheduled_us"] is not None
                        and row["arrival_scheduled_us"] is not None
                        or row["departure_actual_us"] is not None
                        and row["arrival_actual_us"] is not None
                    )
                    counts["searchable"] += not held and row["status"] != "cancelled" and complete
                    counts["with_aircraft_type"] += row["aircraft_type_code"] is not None
                    counts["manually_reviewed"] += bool(row["itinerary_reviewed"])
                    counts["locally_modified"] += row["locally_modified_us"] is not None
                    month = (
                        (EPOCH + timedelta(microseconds=row["event_us"])).strftime("%Y-%m")
                        if row["event_us"] is not None
                        and -62135596800000000 <= row["event_us"] <= 253402300799999999
                        else "missing_time_evidence"
                    )
                    months[month] += 1
                    reasons_count.update(set(old))
                    if row["aircraft_type_code"] is None:
                        findings["missing_aircraft_type"] += 1
                    if row["callsign"] is None:
                        findings["missing_callsign"] += 1
                    if any(x is None for x in evidence["coordinates"]):
                        findings["missing_airport_coordinates"] += 1
                    if timing is True:
                        findings["implausible_timing_in_retained_facts"] += 1
                    if timing is True and protected:
                        findings["protected_timing_override"] += 1
                    if "observation_gap" in old:
                        findings[
                            "gap_corroborated_by_vrs"
                            if row["route_verified"]
                            else "gap_requires_observations_to_resolve"
                        ] += 1
                    if set(old) - KNOWN_REASONS:
                        findings["unknown_review_reason"] += 1
                    label = "+".join(sorted(set(old))) or "no_review_reason"
                    if held:
                        severity = (
                            "timing"
                            if "implausible_airport_timing" in old or timing is True
                            else "continuity"
                            if "observation_gap" in old
                            else "other"
                        )
                        group = (severity, label, row["origin_iata"], row["destination_iata"])
                        cohorts[group] += 1
                        if len(samples[group]) < sample_limit:
                            samples[group].append(
                                dict(
                                    provider=row["provider"],
                                    key=row["source_record_key"],
                                    callsign=row["callsign"],
                                    aircraft_type=row["aircraft_type_code"],
                                    **evidence,
                                )
                            )
                    if old != new:
                        findings[
                            "protected_corrections" if protected else "supported_flag_corrections"
                        ] += 1
                        if not protected:
                            patch = dict(
                                id=row["id"],
                                source_id=row["source_id"],
                                key=row["source_record_key"],
                                revision=row["revision"],
                                old=old,
                                new=new,
                                evidence=evidence,
                            )
                            patches.write(json.dumps(patch, separators=(",", ":")) + "\n")
                            if len(missing_proposals) < 20:
                                missing_proposals.append(patch)
                    if progress and counts["records"] % 50000 == 0:
                        progress(
                            {
                                "phase": "scanning",
                                "records": counts["records"],
                                "held": counts["held"],
                            }
                        )
                # Same-source exact signatures are review hints, not proof of
                # duplicate physical flights; never delete or merge these rows.
                duplicate_groups, duplicate_records, duplicate_samples = 0, 0, []
                duplicates = f"""SELECT s.code provider,f.origin_iata,f.destination_iata,f.callsign,
                    ({PARTITION_TIME_SQL}) event_us,
                    COALESCE(f.arrival_scheduled_us,f.arrival_observed_upper_us,f.arrival_actual_us) arrival_us,
                    COUNT(*) records,MIN(f.source_record_key) example_key
                    FROM flight_legs f JOIN data_sources s ON s.id=f.source_id
                    WHERE {where} AND f.callsign IS NOT NULL
                    AND COALESCE(f.arrival_scheduled_us,f.arrival_observed_upper_us,f.arrival_actual_us) IS NOT NULL
                    GROUP BY f.source_id,f.origin_iata,f.destination_iata,f.callsign,event_us,arrival_us HAVING COUNT(*)>1"""
                for row in source.execute(duplicates, values):
                    duplicate_groups += 1
                    duplicate_records += row["records"]
                    if len(duplicate_samples) < 20:
                        duplicate_samples.append(dict(row))
                pipeline = {}
                if source.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name='extraction_days'"
                ).fetchone():
                    days = source.execute(
                        "SELECT COUNT(*),MIN(day),MAX(day) FROM extraction_days"
                    ).fetchone()
                    pipeline.update(
                        completed_inputs=days[0], first_input=days[1], last_input=days[2]
                    )
                pipeline["active_batches"] = source.execute(
                    "SELECT COUNT(*) FROM ingestion_batches WHERE state='running'"
                ).fetchone()[0]
                pipeline["failed_attempts"] = source.execute(
                    "SELECT COUNT(*) FROM ingestion_batches WHERE state IN ('failed','interrupted')"
                ).fetchone()[0]
                global_issues = source.execute(
                    """SELECT COUNT(*) FROM (SELECT source_id,source_record_key FROM flight_legs GROUP BY source_id,source_record_key HAVING COUNT(*)>1)"""
                ).fetchone()[0]
                health = dict(
                    integrity=integrity,
                    foreign_key_errors=foreign_keys,
                    duplicate_source_identities=global_issues,
                    **semantics,
                )
                health["invalid_review_reason_records"] = (
                    findings["invalid_review_reasons"] + findings["unknown_review_reason"]
                )
                health["passed"] = (
                    integrity == ["ok"]
                    and not foreign_keys
                    and global_issues == 0
                    and health["invalid_review_reason_records"] == 0
                    and not any(semantics.values())
                )
            ranked = sorted(
                cohorts,
                key=lambda key: (
                    {"timing": 0, "continuity": 1, "other": 2}[key[0]],
                    -cohorts[key],
                    key,
                ),
            )[:cohort_limit]
            report = dict(
                version=VERSION,
                counts_stage="before_corrections",
                database=str(path),
                mode="read_only",
                started_at=started.isoformat(),
                scope=dict(
                    start=str(start) if start else None,
                    end=str(end) if end else None,
                    origin=origin,
                    destination=destination,
                    reason=reason,
                ),
                counts=dict(counts),
                findings=dict(findings),
                review_reasons=dict(reasons_count),
                months=dict(sorted(months.items())),
                health=health,
                pipeline=pipeline,
                possible_duplicates=dict(
                    groups=duplicate_groups,
                    records=duplicate_records,
                    samples=duplicate_samples,
                    action="report only; no automatic merge/delete",
                ),
                cohort_count=len(cohorts),
                cohorts=[
                    dict(
                        severity=key[0],
                        reasons=key[1],
                        origin=key[2],
                        destination=key[3],
                        records=cohorts[key],
                        samples=samples[key],
                    )
                    for key in ranked
                ],
                correction_examples=missing_proposals,
                changes=dict(applied=0, skipped_concurrent=0),
                limitations=[
                    "Retained itinerary facts cannot verify airport truth, missing stops or commercial service.",
                    "Gap resolution and missing source metadata may require targeted raw-day reprocessing.",
                    "Only derived timing/estimate flags can change; endpoints, times, type, IDs and manual decisions remain intact.",
                ],
            )
            if apply:
                if not health["passed"]:
                    raise ValueError("Structural audit failed; no corrections were applied")
                if pipeline["active_batches"]:
                    raise ValueError(
                        "Active/interrupted running batch remains; finish or resume before audit writes"
                    )
                IngestionStore(path).initialize()
                _apply(path, identity, patches, report, progress)
            report["finished_at"] = datetime.now(UTC).isoformat()
            report["database_family_bytes"] = database_bytes(path)
            return report


def _apply(path, identity, patches, report, progress):
    run_id = uuid4().hex
    with IngestionStore(path).connect() as output:
        output.execute("BEGIN IMMEDIATE")
        current = tuple(
            output.execute(
                "SELECT store_id,generation FROM ingestion_store_metadata WHERE id=1"
            ).fetchone()
        )
        if current != identity:
            raise ValueError("Ingestion store reset/replaced during audit; rerun it")
        output.execute(
            "INSERT INTO ingestion_audit_runs VALUES(?,?,?,?,?)",
            (run_id, VERSION, identity[1], utc_us(datetime.now(UTC)), "{}"),
        )
        patches.seek(0)
        for line in patches:
            patch = json.loads(line)
            row = output.execute(
                """SELECT f.*,s.code provider,a.latitude origin_lat,a.longitude origin_lon,
                b.latitude destination_lat,b.longitude destination_lon FROM flight_legs f
                JOIN data_sources s ON s.id=f.source_id JOIN airports a ON a.iata=f.origin_iata
                JOIN airports b ON b.iata=f.destination_iata WHERE f.id=? AND NOT EXISTS
                (SELECT 1 FROM flight_tombstones t WHERE t.source_id=f.source_id AND t.source_record_key=f.source_record_key)""",
                (patch["id"],),
            ).fetchone()
            if (
                row is None
                or row["revision"] != patch["revision"]
                or row["source_id"] != patch["source_id"]
                or row["source_record_key"] != patch["key"]
            ):
                report["changes"]["skipped_concurrent"] += 1
                continue
            old, new, _, protected, evidence = assessment(row)
            if (
                protected
                or old != patch["old"]
                or new != patch["new"]
                or evidence != patch["evidence"]
            ):
                report["changes"]["skipped_concurrent"] += 1
                continue
            output.execute(
                "UPDATE flight_legs SET review_reasons=?,revision=revision+1 WHERE id=?",
                (json.dumps(new, separators=(",", ":")), patch["id"]),
            )
            output.execute(
                "INSERT INTO ingestion_audit_changes VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    row["source_id"],
                    row["source_record_key"],
                    row["revision"],
                    row["revision"] + 1,
                    json.dumps(old),
                    json.dumps(new),
                    json.dumps(evidence),
                ),
            )
            report["changes"]["applied"] += 1
            if progress and report["changes"]["applied"] % 1000 == 0:
                progress({"phase": "applying", "records": report["changes"]["applied"]})
        refresh_admission_view(output)
        report.update(mode="applied", run_id=run_id)
        # Keep full cohort reports in artifacts; compact durable audit provenance
        # retains every change and the run's counters/scope, avoiding huge JSON.
        summary = {
            key: report[key]
            for key in ("version", "scope", "counts", "findings", "health", "changes")
        }
        output.execute(
            "UPDATE ingestion_audit_runs SET summary_json=?,finished_us=? WHERE id=?",
            (json.dumps(summary), utc_us(datetime.now(UTC)), run_id),
        )
