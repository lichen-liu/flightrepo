"""Resumable private-day verification followed by explicit live publication."""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from core.database_roles import require_ingestion, require_serving
from core.storage import SQLiteConnection, utc_us
from ingestion.audit import audit_database
from ingestion.publish import PARTITION_TIME_SQL, publish_range
from ingestion.references import ensure_carriers
from ingestion.store import IngestionStore, check_quota, job_lock
from ingestion.vrs import admit, ensure_snapshot

VERSION = "daily-verified-publication-v1"
DAY_US = 86400_000000


def fingerprint(path, input_day, start, end, departure_year):
    """Pin input scope, reference evidence, dimensions and retained revisions."""
    with SQLiteConnection(path, read_only=True).connect() as c:
        c.execute("BEGIN")
        require_ingestion(c)
        identity = tuple(
            c.execute(
                "SELECT store_id,generation FROM ingestion_store_metadata WHERE id=1"
            ).fetchone()
        )
        row = c.execute(
            "SELECT receipt FROM extraction_days WHERE day=?", (str(input_day),)
        ).fetchone()
        if row is None:
            raise ValueError("Daily publication requires a committed input receipt")
        receipt = json.loads(row[0])
        years = receipt.get("departure_years", [2023])
        if years is not None and (departure_year is None or departure_year not in years):
            raise ValueError("Input receipt does not cover requested departure scope")
        digest = hashlib.sha256()

        def add(value):
            digest.update(json.dumps(value, sort_keys=True, default=str).encode() + b"\n")

        add([VERSION, identity, receipt, str(start), str(end)])
        for table, column in [
            ("ingestion_vrs_snapshot", "receipt_json"),
            ("reference_snapshots", "sha256"),
        ]:
            for item in c.execute(f"SELECT {column} FROM {table} ORDER BY {column}"):
                add(tuple(item))
        lower = utc_us(datetime.combine(start, time.min, UTC))
        upper = utc_us(datetime.combine(end + timedelta(days=1), time.min, UTC))
        from ingestion import authority

        canonical = authority.configuration(c)
        if canonical:
            add(["serving-authority-v1", canonical])
            for item in authority.fingerprint_rows(c, lower, upper):
                add(item)
        for item in (
            ()
            if canonical
            else c.execute(
                f"""SELECT f.id,f.revision,f.source_id,f.source_record_key,
                f.locally_modified_us,f.itinerary_reviewed,a.latitude,a.longitude,b.latitude,b.longitude
                FROM flight_legs f JOIN airports a ON a.iata=f.origin_iata
                JOIN airports b ON b.iata=f.destination_iata
                WHERE ({PARTITION_TIME_SQL})>=? AND ({PARTITION_TIME_SQL})<? ORDER BY f.id""",
                (lower, upper),
            )
        ):
            add(tuple(item))
        for item in c.execute(
            "SELECT source_id,source_record_key,deleted_us FROM flight_tombstones ORDER BY source_id,source_record_key"
        ):
            add(tuple(item))
        return identity, digest.hexdigest()


def serving_receipts(path, store_id, generation, start, end, expected=None):
    if not Path(path).is_file():
        return []
    with SQLiteConnection(path, read_only=True).connect() as c:
        require_serving(c)
        if not c.execute("SELECT 1 FROM sqlite_schema WHERE name='publication_runs'").fetchone():
            return []
        result = []
        for offset in range((end - start).days + 1):
            lower = utc_us(datetime.combine(start + timedelta(days=offset), time.min, UTC))
            if expected is not None:
                if len(expected) != (end - start).days + 1:
                    return []
                proof = expected[offset]
                row = c.execute(
                    """SELECT id,finished_us FROM publication_runs WHERE store_id=? AND generation=?
                    AND start_us=? AND end_us=? AND id=? AND finished_us=?""",
                    (store_id, generation, lower, lower + DAY_US, *proof),
                ).fetchone()
                if row is None:
                    return []
                result.append(list(row))
                continue
            row = c.execute(
                """SELECT id,finished_us FROM publication_runs
                WHERE store_id=? AND generation=? AND start_us=? AND end_us=? ORDER BY id DESC LIMIT 1""",
                (store_id, generation, lower, lower + DAY_US),
            ).fetchone()
            if row is None:
                return []
            result.append(list(row))
        return result


def daily_range(
    path,
    serving,
    start,
    end,
    *,
    ingest_day,
    finish_year=False,
    progress=None,
    serving_authoritative=False,
):
    """Never advance to the next raw day before audit and publication succeed.

    Successful receipts are reused; failed/interrupted stages replay idempotently.
    Republish D-1 after D to include newly joined overnight legs. Current-day
    complete local legs are visible immediately, without claiming full coverage.
    """
    path, serving = Path(path).resolve(), Path(serving).resolve()
    if path == serving or serving.exists() and path.samefile(serving):
        raise ValueError("Ingestion and serving databases must be different files")
    if end < start:
        raise ValueError("End must follow start")
    if finish_year and (start.year != end.year or (end.month, end.day) != (12, 31)):
        raise ValueError("--finish-year requires one year's range ending December 31")
    last = end + timedelta(days=bool(finish_year))
    departure_year = end.year if finish_year else None
    store = IngestionStore(path)
    with job_lock(path, blocking=False) as acquired:
        if not acquired:
            raise ValueError("Stop the active ingestion job before starting daily publication")
        store.initialize()
        from ingestion import authority

        if serving_authoritative:
            authority.bind(path, serving)
        canonical = authority.selected(path, serving)
        if serving.exists():
            with SQLiteConnection(serving, read_only=True).connect() as c:
                require_serving(c)
                owner = (
                    c.execute("SELECT store_id FROM publication_origin WHERE id=1").fetchone()
                    if c.execute(
                        "SELECT 1 FROM sqlite_schema WHERE name='publication_origin'"
                    ).fetchone()
                    else None
                )
            with store.connect() as c:
                own_id = c.execute(
                    "SELECT store_id FROM ingestion_store_metadata WHERE id=1"
                ).fetchone()[0]
            if owner is not None and owner[0] != own_id:
                raise ValueError("Serving publication is owned by a different ingestion store")
        ensure_carriers(path)
        ensure_snapshot(path, fetch_missing=True)
        for offset in range((last - start).days + 1):
            day = start + timedelta(days=offset)
            first, final = max(start, day - timedelta(days=1)), min(day, end)
            # Re-running extraction validates source selection/policy and cleans
            # leftover raw copies, but never redownloads committed inputs.
            ingest_day(day, departure_year)
            identity, before = fingerprint(path, day, first, final, departure_year)
            key = f"{VERSION}:{identity[1]}:{hashlib.sha256(str(serving).encode()).hexdigest()}:{start}:{end}:{day}"
            with store.connect() as c:
                previous = c.execute(
                    "SELECT payload_json FROM ingestion_documents d JOIN data_sources s ON s.id=d.source_id WHERE s.code='adsblol' AND kind='validation' AND source_record_key=?",
                    (key,),
                ).fetchone()
            previous = json.loads(previous[0]) if previous else None
            proofs = serving_receipts(
                serving,
                *identity,
                first,
                final,
                expected=previous["serving_receipts"] if previous else None,
            )
            if (
                previous
                and previous["fingerprint"] == before
                and proofs
                and previous["serving_receipts"] == proofs
            ):
                yield dict(
                    event="daily_already_published",
                    input_day=str(day),
                    start=str(first),
                    end=str(final),
                )
                continue
            if progress:
                progress(
                    dict(
                        event="daily_verifying",
                        input_day=str(day),
                        start=str(first),
                        end=str(final),
                    )
                )
            if canonical:
                authority.hydrate_window(path, first, final)
            audit = audit_database(path, apply=True, start=first, end=final, progress=progress)
            if not audit["health"]["passed"] or audit["changes"]["skipped_concurrent"]:
                raise ValueError("Daily audit failed; publication stopped")
            verification = admit(path, start=first, end=final, apply=True, progress=progress)
            # No second VRS pass; the producer lock spans all stages.
            published = list(publish_range(path, serving, first, final, audit_routes=False))
            proofs = serving_receipts(serving, *identity, first, final)
            identity, after = fingerprint(path, day, first, final, departure_year)
            result = dict(
                event="daily_published",
                version=VERSION,
                input_day=str(day),
                start=str(first),
                end=str(final),
                fingerprint=after,
                serving_receipts=proofs,
                audit=dict(
                    health=audit["health"], counts=audit["counts"], changes=audit["changes"]
                ),
                vrs=verification["counts"],
                publication=published,
            )
            if canonical:
                result["released_private_copies"] = sum(
                    p["released_private_copies"] for p in published
                )
            check_quota(path)
            check_quota(serving)
            store.document(
                source="adsblol",
                kind="validation",
                key=key,
                payload=result,
                source_url="https://www.adsb.lol/docs/open-data/historical/",
            )
            if progress:
                progress(result)
            yield result
