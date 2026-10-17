"""CC0 VRS snapshots, route evidence and explicit best-effort admission."""

import csv
import hashlib
import io
import json
import re
from collections import Counter
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from core.admission import blocked_for_search, route_verified_sql, search_blocked_sql
from core.database_roles import ingestion_target, require_ingestion
from core.models import RouteVerification
from core.storage import SQLiteConnection, utc_us
from ingestion.audit import scope_sql
from ingestion.store import IngestionStore, check_quota, job_lock

BASE_URL = "https://vrs-standing-data.adsb.lol/"
MAX_BYTES = 64 * 1024**2
VERSION = "vrs-route-plausibility-v1"


def download(name):
    """Bounded in-memory raw download: no temporary SSD file or website scraping."""
    if name not in {"routes", "airports"}:
        raise ValueError("Choose VRS routes or airports")
    data = bytearray()
    with httpx.stream("GET", BASE_URL + name + ".csv", timeout=60) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError("VRS download exceeds 64 MiB")
        metadata = dict(url=str(response.url), last_modified=response.headers.get("Last-Modified"))
    return bytes(data), metadata


def csv_rows(data, required):
    if not data or len(data) > MAX_BYTES:
        raise ValueError("VRS CSV must be nonempty and at most 64 MiB")
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    if not set(required).issubset(reader.fieldnames or []):
        raise ValueError("Unexpected VRS CSV columns")
    try:
        for row in reader:
            if None in row or any(row[key] is None for key in required):
                raise ValueError("Malformed VRS CSV row")
            yield row
    except csv.Error as error:
        raise ValueError("Malformed VRS CSV") from error


def import_snapshot(store, routes, airports, *, metadata=None, fetched_at=None):
    """Atomic whole-snapshot replacement. A failed refresh leaves the old snapshot usable."""
    if not isinstance(store, IngestionStore):
        raise ValueError("VRS references require a private IngestionStore")
    ingestion_target(store.path)
    fetched_at = fetched_at or datetime.now(UTC)
    fetched_us = utc_us(fetched_at)
    metadata = metadata or {}
    receipt = dict(
        version=VERSION,
        source="vrs",
        license="CC0-1.0",
        fetched_us=fetched_us,
        date_valid_reference=False,
        files={
            name: dict(
                **metadata.get(name, {"url": BASE_URL + name + ".csv"}),
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
            )
            for name, data in [("routes", routes), ("airports", airports)]
        },
    )
    with job_lock(store.path, blocking=False) as acquired:
        if not acquired:
            raise ValueError("Stop the active ingestion job before refreshing VRS")
        store.initialize()
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT receipt_json FROM ingestion_vrs_snapshot WHERE id=1"
            ).fetchone()
            if previous and json.loads(previous[0])["files"] == receipt["files"]:
                # Re-reading identical bytes is not a new reference revision.
                c.execute(
                    "UPDATE ingestion_vrs_snapshot SET receipt_json=? WHERE id=1",
                    (json.dumps(receipt | {"counts": json.loads(previous[0])["counts"]}),),
                )
                return receipt | {
                    "counts": json.loads(previous[0])["counts"],
                    "unchanged": True,
                    "flight_records_changed": False,
                    "serving_changed": False,
                }
            c.execute("DELETE FROM ingestion_vrs_routes")
            c.execute("DELETE FROM ingestion_vrs_airports")
            airport_count = 0
            invalid_iata = 0
            for row in csv_rows(airports, ("Code", "IATA")):
                code, iata = row["Code"].strip().upper(), row["IATA"].strip().upper()
                if not re.fullmatch(r"[A-Z0-9]{2,8}", code):
                    raise ValueError("Invalid VRS airport code")
                if iata and not re.fullmatch(r"[A-Z]{3}", iata):
                    # A malformed optional identifier is unknown, never an invented mapping.
                    invalid_iata += 1
                    iata = ""
                c.execute("INSERT INTO ingestion_vrs_airports VALUES (?,?)", (code, iata or None))
                airport_count += 1
            mapping = dict(c.execute("SELECT code,iata FROM ingestion_vrs_airports"))
            counts = Counter(airports=airport_count, invalid_iata_mappings=invalid_iata)
            batch = []
            for row in csv_rows(routes, ("Callsign", "AirportCodes")):
                callsign = row["Callsign"].strip().upper()
                chain = row["AirportCodes"].strip().upper().split("-")
                if (
                    not re.fullmatch(r"[A-Z0-9]{1,16}", callsign)
                    or not 2 <= len(chain) <= 64
                    or any(not re.fullmatch(r"[A-Z0-9]{2,8}", x) for x in chain)
                ):
                    raise ValueError("Invalid VRS callsign/airport chain")
                resolved = [mapping.get(x) or "?" for x in chain]
                batch.append((callsign, "-".join(chain), "|" + "|".join(resolved) + "|"))
                counts["routes"] += 1
                counts["routes_with_unmapped_airports"] += "?" in resolved
                if len(batch) == 1000:
                    c.executemany("INSERT INTO ingestion_vrs_routes VALUES (?,?,?)", batch)
                    batch.clear()
                    check_quota(store.path, c)
            c.executemany("INSERT INTO ingestion_vrs_routes VALUES (?,?,?)", batch)
            if not airport_count or not counts["routes"]:
                raise ValueError("Refuse an empty VRS snapshot")
            receipt["counts"] = dict(counts)
            c.execute(
                "INSERT INTO ingestion_vrs_snapshot VALUES (1,?) ON CONFLICT(id) DO UPDATE SET receipt_json=excluded.receipt_json",
                (json.dumps(receipt),),
            )
    return receipt | {"unchanged": False, "flight_records_changed": False, "serving_changed": False}


def import_files(store, *, routes_path=None, airports_path=None):
    """Producer entry point: paired files or the published full CSVs, never per-flight calls."""
    ingestion_target(store.path)
    if (routes_path is None) != (airports_path is None):
        raise ValueError("Supply both --routes-csv and --airports-csv, or neither")
    files, metadata = {}, {}
    for name, path in [("routes", routes_path), ("airports", airports_path)]:
        if path is None:
            files[name], metadata[name] = download(name)
        else:
            with Path(path).open("rb") as handle:
                files[name] = handle.read(MAX_BYTES + 1)
            metadata[name] = dict(url=Path(path).resolve().as_uri())
    return import_snapshot(store, files["routes"], files["airports"], metadata=metadata)


def report(path, *, start=None, end=None, cohort_limit=50):
    """One consistent read snapshot; no reference result changes facts, holds or approval."""
    if not 0 <= cohort_limit <= 1000:
        raise ValueError("Cohort limit must be between zero and 1000")
    where, parameters = scope_sql(start, end)
    held = search_blocked_sql("f")
    classification = """CASE
        WHEN r.callsign IS NULL THEN 'no_callsign_reference'
        WHEN instr(r.iata_chain,'|'||f.origin_iata||'|'||f.destination_iata||'|')>0
            THEN CASE WHEN length(r.airport_codes)-length(replace(r.airport_codes,'-',''))=1
                THEN 'exact_two_airport_association' ELSE 'adjacent_leg_in_multistop_association' END
        WHEN instr(r.iata_chain,'?')>0 THEN 'incomplete_airport_mapping'
        ELSE 'different_undated_route_association' END"""
    # Number of separators is reliable for all source code lengths; no IATA/ICAO guessing.
    sql = f"""SELECT {classification} result,f.origin_iata,f.destination_iata,
        COUNT(*) records,SUM({held}) held
        FROM flight_legs f JOIN data_sources s ON s.id=f.source_id
        LEFT JOIN ingestion_vrs_routes r ON r.callsign=upper(trim(f.callsign))
        WHERE s.code='adsblol' AND {where}
        GROUP BY result,f.origin_iata,f.destination_iata"""
    counts, cohorts = {}, []
    with SQLiteConnection(path, read_only=True).connect() as c:
        c.execute("BEGIN")
        require_ingestion(c)
        snapshot = c.execute(
            "SELECT receipt_json FROM ingestion_vrs_snapshot WHERE id=1"
        ).fetchone()
        if snapshot is None:
            raise ValueError("Import a VRS snapshot before reporting")
        snapshot = json.loads(snapshot[0])
        # Aggregate bounded cohorts in Python; SQLite performs indexed callsign lookups.
        with closing(c.execute(sql, parameters)) as rows:
            for row in rows:
                result = row["result"]
                item = counts.setdefault(result, dict(records=0, held=0))
                item["records"] += row["records"]
                item["held"] += row["held"]
                if cohort_limit:
                    cohorts.append(dict(row))
                    if len(cohorts) > cohort_limit * 2:
                        cohorts.sort(
                            key=lambda r: (
                                -r["held"],
                                -r["records"],
                                r["result"],
                                r["origin_iata"],
                                r["destination_iata"],
                            )
                        )
                        del cohorts[cohort_limit:]
    cohorts.sort(
        key=lambda r: (
            -r["held"],
            -r["records"],
            r["result"],
            r["origin_iata"],
            r["destination_iata"],
        )
    )
    return dict(
        version=VERSION,
        snapshot=snapshot,
        scope=dict(start=start, end=end, provider="adsblol", partition="UTC departure day"),
        counts=counts,
        cohorts=cohorts[:cohort_limit],
        normalization="trim/uppercase callsign only; explicit VRS airport mapping",
        date_valid_reference=False,
        flight_records_changed=False,
        serving_changed=False,
        admission_changed=False,
    )


ADMISSION_VERSION = "vrs-route-audit-v2"


def admit(path, *, start, end, apply=False, progress=None):
    """Confirm directed airport pairs and waive gap holds; never alter source facts.

    Dry-run is read-only. Explicit application reconciles each requested UTC day
    against the pinned snapshot, revoking old automatic evidence on mismatch.
    Human approvals/edits and tombstones are protected. Timing/other holds remain.
    """
    start, end = (date.fromisoformat(x) if isinstance(x, str) else x for x in (start, end))
    if end < start:
        raise ValueError("VRS admission end must follow start")
    path = Path(path).resolve()
    ingestion_target(path)
    with job_lock(path, blocking=False) as acquired:
        if not acquired:
            raise ValueError("Stop the active ingestion job before VRS admission")
        store = IngestionStore(path)
        if apply:
            store.initialize()
        with SQLiteConnection(path, read_only=True).connect() as c:
            require_ingestion(c)
            row = c.execute("SELECT receipt_json FROM ingestion_vrs_snapshot WHERE id=1").fetchone()
            if row is None:
                raise ValueError("Import a VRS snapshot before admission")
            receipt = json.loads(row[0])
        reference = hashlib.sha256(
            json.dumps(
                {k: v["sha256"] for k, v in receipt["files"].items()},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if apply:
            with store.connect() as c:
                has_receipt = c.execute(
                    "SELECT 1 FROM ingestion_documents d JOIN data_sources s ON s.id=d.source_id WHERE s.code='vrs' AND d.kind='validation' AND d.source_record_key=?",
                    (reference + ":" + ADMISSION_VERSION,),
                ).fetchone()
            if not has_receipt:
                store.document(
                    source="vrs",
                    kind="validation",
                    key=reference + ":" + ADMISSION_VERSION,
                    payload={"snapshot": receipt, "audit_policy": ADMISSION_VERSION},
                    source_url=BASE_URL,
                    source_group="vrs-route-snapshot",
                    license_expression="CC0-1.0",
                )
            with store.connect() as c:
                c.execute(
                    "UPDATE data_sources SET source_url=?,license_url=?,attribution=? WHERE code='vrs'",
                    (
                        BASE_URL,
                        "https://creativecommons.org/publicdomain/zero/1.0/",
                        "VRS standing data, CC0; undated route association",
                    ),
                )
        totals = Counter()
        current = start
        while current <= end:
            where, parameters = scope_sql(current, current)
            counts = Counter()
            context = store.connect() if apply else SQLiteConnection(path, read_only=True).connect()
            with context as c:
                c.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
                columns = {r[1] for r in c.execute("PRAGMA table_info(flight_legs)")}
                verified = route_verified_sql("f") if "route_verification" in columns else "0"
                rows = c.execute(
                    f"""SELECT f.*,({verified}) AS verified,
                    r.iata_chain,r.airport_codes FROM flight_legs f INDEXED BY ingestion_publication_time
                    JOIN data_sources s ON s.id=f.source_id
                    LEFT JOIN ingestion_vrs_routes r ON r.callsign=upper(trim(f.callsign))
                    WHERE s.code='adsblol' AND {where} ORDER BY f.id""",
                    parameters,
                ).fetchall()
                updates = []
                for row in rows:
                    counts["records"] += 1
                    reasons = json.loads(row["review_reasons"])
                    old_held = blocked_for_search(reasons, bool(row["itinerary_reviewed"]))
                    counts["held_before"] += old_held
                    if row["locally_modified_us"] is not None or row["itinerary_reviewed"]:
                        counts["protected"] += 1
                        counts["held_after"] += old_held
                        continue
                    pair = "|" + row["origin_iata"] + "|" + row["destination_iata"] + "|"
                    match = row["iata_chain"] is not None and pair in row["iata_chain"]
                    if match:
                        result = "exact" if row["airport_codes"].count("-") == 1 else "adjacent"
                        counts["matched"] += 1
                    elif row["iata_chain"] is None:
                        result = "missing"
                    elif "?" in row["iata_chain"]:
                        result = "incomplete"
                    else:
                        result = "different"
                    counts["audited"] += 1
                    counts["reference_" + result] += 1
                    chain = (
                        [
                            None if code == "?" else code
                            for code in row["iata_chain"].strip("|").split("|")
                        ]
                        if row["iata_chain"]
                        else None
                    )
                    proof = RouteVerification(
                        match_kind=result,
                        reference_sha256=reference,
                        callsign=(row["callsign"] or "").strip().upper() or None,
                        origin_iata=row["origin_iata"],
                        destination_iata=row["destination_iata"],
                        reference_airports=chain,
                    ).model_dump_json()
                    new_held = blocked_for_search(reasons)
                    counts["held_after"] += new_held
                    counts["released"] += old_held and not new_held
                    counts["reheld"] += not old_held and new_held
                    old_proof = (
                        row["route_verification"] if "route_verification" in columns else None
                    )
                    if proof != old_proof:
                        counts["changed"] += 1
                        counts["newly_confirmed_estimates"] += (
                            match and not row["verified"] and "airborne_airport_estimate" in reasons
                        )
                        updates.append((proof, row["id"], row["revision"]))
                if apply:
                    c.executemany(
                        "UPDATE flight_legs SET route_verification=?,revision=revision+1 WHERE id=? AND revision=? AND locally_modified_us IS NULL AND itinerary_reviewed=0",
                        updates,
                    )
            totals.update(counts)
            if progress:
                progress(
                    dict(phase="vrs_admission", day=str(current), apply=apply, counts=dict(counts))
                )
            current += timedelta(days=1)
        return dict(
            version=ADMISSION_VERSION,
            apply=apply,
            scope=dict(start=str(start), end=str(end)),
            reference_sha256=reference,
            snapshot=receipt,
            counts=dict(totals),
            human_approvals_added=0,
            source_facts_changed=False,
            serving_changed=False,
        )


def ensure_snapshot(path, *, refresh=False, fetch_missing=False):
    """Producer orchestration only: pin a cached snapshot or fetch its two bulk CSVs."""
    path = Path(path).resolve()
    ingestion_target(path)
    with SQLiteConnection(path, read_only=True).connect() as c:
        require_ingestion(c)
        snapshot = c.execute(
            "SELECT receipt_json FROM ingestion_vrs_snapshot WHERE id=1"
        ).fetchone()
    if refresh or (snapshot is None and fetch_missing):
        return import_files(IngestionStore(path))
    return json.loads(snapshot[0]) if snapshot else None


def audit_cached_day(path, day):
    """Every publication audits cached VRS locally; serving queries never fetch or write."""
    snapshot = ensure_snapshot(path)
    if snapshot is None:
        return dict(status="not_checked", reason="no_pinned_snapshot")
    result = admit(path, start=day, end=day, apply=True)
    return dict(
        status="checked",
        version=result["version"],
        reference_sha256=result["reference_sha256"],
        counts=result["counts"],
    )
