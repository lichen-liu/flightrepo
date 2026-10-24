"""Reproducible shared airport/operator catalogs; raw CSVs stay in memory."""

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

from core.database_roles import require_ingestion
from core.storage import SQLiteConnection, source_id, utc_us
from ingestion.store import IngestionStore, check_quota, job_lock
from ingestion.updater import FlightUpdater

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
CARRIERS_URL = "https://raw.githubusercontent.com/vradarserver/standing-data/main/airlines/schema-01/airlines.csv"
MAX_BYTES = 64 * 1024**2
AIRPORT_FIELDS = (
    "iata",
    "name",
    "icao",
    "municipality",
    "country_code",
    "region_code",
    "city_id",
    "timezone",
    "latitude",
    "longitude",
    "metadata_source_id",
    "metadata_updated_us",
)
CARRIER_FIELDS = ("code", "name", "icao", "iata", "metadata_source_id", "metadata_updated_us")


def download(url):
    data = bytearray()
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError("Reference CSV exceeds 64 MiB")
    return bytes(data)


def merge_airport(connection, values):
    """Fill missing fields only: never erase or overwrite serving/admin metadata."""
    fields = AIRPORT_FIELDS
    values = {key: values.get(key) for key in fields}
    connection.execute(
        f"INSERT INTO airports({','.join(fields)}) VALUES({','.join(':' + key for key in fields)}) "
        "ON CONFLICT(iata) DO UPDATE SET "
        + ",".join(
            f"{key}=COALESCE(airports.{key},excluded.{key})" for key in fields if key != "iata"
        )
        + " WHERE "
        + " OR ".join(
            f"(airports.{key} IS NULL AND excluded.{key} IS NOT NULL)"
            for key in fields
            if key != "iata"
        ),
        values,
    )


def merge_carrier(connection, values):
    fields = CARRIER_FIELDS
    connection.execute(
        f"INSERT INTO carriers({','.join(fields)}) VALUES({','.join(':' + key for key in fields)}) "
        "ON CONFLICT(code) DO UPDATE SET "
        + ",".join(
            f"{key}=COALESCE(carriers.{key},excluded.{key})" for key in fields if key != "code"
        )
        + " WHERE "
        + " OR ".join(
            f"(carriers.{key} IS NULL AND excluded.{key} IS NOT NULL)"
            for key in fields
            if key != "code"
        ),
        values,
    )


def parse_airports(data):
    from ingestion.vrs import csv_rows

    candidates = []
    ignored = Counter()
    for row in csv_rows(data, ("iata_code", "type", "latitude_deg", "longitude_deg")):
        code = row["iata_code"].strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code) or row["type"] in {
            "closed",
            "heliport",
            "seaplane_base",
        }:
            ignored["not_supported_airport"] += 1
            continue
        try:
            lat, lon = float(row["latitude_deg"]), float(row["longitude_deg"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("coordinates")
        except (TypeError, ValueError):
            ignored["invalid_coordinates"] += 1
            continue
        candidates.append(
            dict(
                iata=code,
                name=row.get("name", "").strip() or None,
                icao=row.get("icao_code", "").strip() or None,
                municipality=row.get("municipality", "").strip() or None,
                country_code=row.get("iso_country", "").strip() or None,
                region_code=row.get("iso_region", "").strip() or None,
                latitude=lat,
                longitude=lon,
            )
        )
    counts = Counter(item["iata"] for item in candidates)
    # Reused codes cannot safely select a single airport. Retain existing metadata.
    rows = [item for item in candidates if counts[item["iata"]] == 1]
    ignored["ambiguous_iata_rows"] = sum(value for value in counts.values() if value > 1)
    if not rows:
        raise ValueError("No unambiguous supported airports in reference")
    return rows, dict(ignored)


def parse_carriers(data):
    from ingestion.vrs import csv_rows

    rows = []
    codes, icaos = set(), set()
    ignored = Counter()
    for row in csv_rows(data, ("Code", "Name", "ICAO", "IATA")):
        code, name = row["Code"].strip(), row["Name"].strip()
        icao, iata = row["ICAO"].strip().upper() or None, row["IATA"].strip().upper() or None
        if (
            not code
            or len(code) > 16
            or not name
            or (icao and not re.fullmatch(r"[A-Z]{3}", icao))
            or (iata and not re.fullmatch(r"[A-Z0-9]{2}", iata))
        ):
            ignored["invalid_carrier"] += 1
            continue
        if code in codes or icao is not None and icao in icaos:
            raise ValueError("Ambiguous duplicate carrier code/ICAO in reference")
        codes.add(code)
        if icao:
            icaos.add(icao)
        rows.append(dict(code=code, name=name, icao=icao, iata=iata))
    if not rows:
        raise ValueError("No valid carriers in reference")
    return rows, dict(ignored)


def import_catalog(path, kind, data, *, source_url=None):
    """Private producer write; validate all input before taking the write lock."""
    if kind not in {"airports", "carriers"}:
        raise ValueError("Choose airports or carriers")
    rows, ignored = parse_airports(data) if kind == "airports" else parse_carriers(data)
    url = source_url or (AIRPORTS_URL if kind == "airports" else CARRIERS_URL)
    code = "ourairports" if kind == "airports" else "vrs-airlines"
    now = utc_us(datetime.now(UTC))
    digest = hashlib.sha256(data).hexdigest()
    store = IngestionStore(path)
    with job_lock(store.path, blocking=False) as acquired:
        if not acquired:
            raise ValueError("Stop the active ingestion job before importing references")
        store.initialize()
        prepared = lookup = None
        if kind == "airports":
            from ingestion.airport_timezones import plan, read_inputs, resolver

            lookup = resolver()
            with store.connect() as c:
                c.execute("BEGIN")
                inputs, states = read_inputs(c)
            prepared = plan(inputs, states, lookup, incoming=rows)
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if prepared is not None:
                from ingestion.airport_timezones import digest as timezone_digest

                if timezone_digest(list(read_inputs(c))) != prepared["original"]:
                    raise ValueError("Airport inputs changed during reference preparation; retry")
            sid = source_id(c, code)
            c.execute(
                "UPDATE data_sources SET name=?,source_url=?,license_expression=?,license_url=?,attribution=? WHERE id=?",
                (
                    "OurAirports" if kind == "airports" else "VRS airline/operator reference",
                    url,
                    "LicenseRef-Public-Domain" if kind == "airports" else "CC0-1.0",
                    "https://ourairports.com/data/"
                    if kind == "airports"
                    else "https://github.com/vradarserver/standing-data/blob/main/LICENSE",
                    "OurAirports public-domain airport catalog"
                    if kind == "airports"
                    else "VRS standing data; current undated operator reference, not historical flight verification",
                    sid,
                ),
            )
            previous = c.execute(
                "SELECT sha256 FROM reference_snapshots WHERE source_id=? AND kind=?", (sid, kind)
            ).fetchone()
            if kind == "carriers":
                # This whole source-owned catalog is an undated current snapshot.
                # Refresh must withdraw stale code assignments, not accumulate them.
                c.execute("DELETE FROM carriers WHERE metadata_source_id=?", (sid,))
            for row in rows:
                values = row | {"metadata_source_id": sid, "metadata_updated_us": now}
                if kind == "airports":
                    merge_airport(c, values)
                else:
                    merge_carrier(c, values)
            if prepared is not None:
                from ingestion.airport_timezones import apply

                apply(c, prepared, lookup, recheck=False)
            c.execute(
                "INSERT INTO reference_snapshots VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,kind) DO UPDATE SET source_url=excluded.source_url,sha256=excluded.sha256,fetched_us=excluded.fetched_us,row_count=excluded.row_count",
                (sid, kind, url, digest, now, len(rows)),
            )
    return dict(
        kind=kind,
        rows=len(rows),
        ignored=ignored,
        source_url=url,
        sha256=digest,
        unchanged=bool(previous and previous[0] == digest),
        serving_changed=False,
        **(
            {"timezones": prepared["counts"], "timezone_snapshot": lookup.identity}
            if prepared is not None
            else {}
        ),
    )


def refresh(path, *, airports_path=None, carriers_path=None, carriers_only=False):
    result = []
    for kind, file, url in [
        ("airports", airports_path, AIRPORTS_URL),
        ("carriers", carriers_path, CARRIERS_URL),
    ]:
        if carriers_only and kind == "airports":
            continue
        data = Path(file).read_bytes() if file else download(url)
        result.append(import_catalog(path, kind, data, source_url=url))
    return result


def ensure_carriers(path):
    with SQLiteConnection(path, read_only=True).connect() as c:
        exists = c.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='reference_snapshots'"
        ).fetchone()
        if (
            exists
            and c.execute("SELECT 1 FROM reference_snapshots WHERE kind='carriers'").fetchone()
        ):
            return
    refresh(path, carriers_only=True)


def copy_source(row, output):
    """Copy attribution without dirtying an unchanged source row."""
    fields = ("name", "source_url", "license_expression", "license_url", "attribution")
    sid = source_id(output, row["code"])
    output.execute(
        "UPDATE data_sources SET "
        + ",".join(f"{key}=:{key}" for key in fields)
        + " WHERE id=:id AND ("
        + ",".join(fields)
        + ") IS NOT ("
        + ",".join(f":{key}" for key in fields)
        + ")",
        {key: row[key] for key in fields} | {"id": sid},
    )
    return sid


def copy_catalog(source, output, *, refresh_timezones=True):
    """Publish reference dimensions independently of millions of flight facts."""
    sources = {
        r[0]
        for r in source.execute(
            "SELECT metadata_source_id FROM airports UNION SELECT metadata_source_id FROM carriers UNION SELECT source_id FROM reference_snapshots"
        )
        if r[0] is not None
    }
    mapping = {}
    for row in source.execute("SELECT * FROM data_sources"):
        if row["id"] not in sources:
            continue
        sid = copy_source(row, output)
        mapping[row["id"]] = sid
    from core.airport_timezones import state_rows
    from ingestion.airport_timezones import publish as publish_timezones

    timezone_states = state_rows(source)
    for row in source.execute("SELECT * FROM airports"):
        values = dict(row)
        if row["iata"] in timezone_states:
            values["timezone"] = None
        merge_airport(
            output, values | {"metadata_source_id": mapping.get(row["metadata_source_id"])}
        )
    timezone_counts = publish_timezones(source, output, replace_generated=refresh_timezones)
    carriers = [
        dict(row) | {"metadata_source_id": mapping.get(row["metadata_source_id"])}
        for row in source.execute("SELECT * FROM carriers")
    ]
    # Compare source-owned dimensions before replacing them. Daily publication
    # usually has the same reference snapshot; avoid deleting/reinserting it.
    changed = set()
    for sid in mapping.values():
        incoming = [row for row in carriers if row["metadata_source_id"] == sid]
        existing = [
            dict(row)
            for row in output.execute("SELECT * FROM carriers WHERE metadata_source_id=?", (sid,))
        ]
        if sorted(incoming, key=lambda row: row["code"]) == sorted(
            existing, key=lambda row: row["code"]
        ):
            continue
        changed.add(sid)
    # Delete changed owners first so reassigned ICAO codes do not collide with
    # the previous snapshot while its replacement is being inserted.
    for sid in changed:
        output.execute("DELETE FROM carriers WHERE metadata_source_id=?", (sid,))
    for row in carriers:
        if row["metadata_source_id"] is None or row["metadata_source_id"] in changed:
            merge_carrier(output, row)
    for row in source.execute("SELECT * FROM reference_snapshots"):
        output.execute(
            "INSERT INTO reference_snapshots VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,kind) DO UPDATE SET source_url=excluded.source_url,sha256=excluded.sha256,fetched_us=excluded.fetched_us,row_count=excluded.row_count "
            "WHERE (reference_snapshots.source_url,reference_snapshots.sha256,reference_snapshots.fetched_us,reference_snapshots.row_count) "
            "IS NOT (excluded.source_url,excluded.sha256,excluded.fetched_us,excluded.row_count)",
            (
                mapping[row["source_id"]],
                row["kind"],
                row["source_url"],
                row["sha256"],
                row["fetched_us"],
                row["row_count"],
            ),
        )
    return timezone_counts


def publish_catalog(ingestion_db, serving_db):
    ingestion_db, serving_db = Path(ingestion_db).resolve(), Path(serving_db).resolve()
    if ingestion_db == serving_db or serving_db.exists() and ingestion_db.samefile(serving_db):
        raise ValueError("Ingestion and serving must be different files")
    writer = FlightUpdater(serving_db)
    # Validate private identity before initializing any output.
    with SQLiteConnection(ingestion_db, read_only=True).connect() as source:
        source.execute("BEGIN")
        require_ingestion(source)
        writer.initialize()
        with writer.connect() as output:
            output.execute("BEGIN IMMEDIATE")
            check_quota(serving_db)
            timezones = copy_catalog(source, output)
            check_quota(serving_db, output)
            counts = {
                table: output.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("airports", "carriers")
            }
    return dict(published=True, **counts, timezones=timezones, flight_facts_changed=False)
