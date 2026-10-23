"""Offline, versioned reference enrichment; never rewrites flight facts."""

import hashlib
import json
import math
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfoNotFoundError

from core.airport_timezones import (
    coverage,
    has_state,
    normalized,
    protect,
    save_state,
    state_rows,
)
from core.database_roles import require_ingestion
from core.storage import SQLiteConnection, source_id
from core.tzdb import VERSION as RULES_VERSION
from core.tzdb import zone
from ingestion.store import IngestionStore, check_quota, job_lock

MANIFEST = Path(__file__).with_name("timezone-data.json")


def digest(value):
    return hashlib.sha256(normalized(value).encode()).hexdigest()


class Resolver:
    def __init__(self):
        self.manifest = json.loads(MANIFEST.read_text()) | {"rules_version": RULES_VERSION}
        self.lock = RLock()
        try:
            import timezonefinder
            from timezonefinder.utils import coord2int

            if version("timezonefinder") != self.manifest["version"]:
                raise ValueError("Timezone resolver version does not match the pinned manifest")
            root = Path(timezonefinder.__file__).parent / "data"
            for name, expected in self.manifest["files"].items():
                if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                    raise ValueError(f"Timezone boundary data failed integrity check: {name}")
            self.finder = timezonefinder.TimezoneFinder(in_memory=False)
            self.coord2int = coord2int
        except (ImportError, OSError) as error:
            raise ValueError(
                "Install the pinned producer dependencies: pip install '.[producer]'"
            ) from error
        self.identity = digest(self.manifest)

    def candidates(self, latitude, longitude):
        # Enumerate the full pinned data, not shortcuts which can discard overlaps.
        x, y = self.coord2int(longitude), self.coord2int(latitude)
        with self.lock:
            return sorted(
                {
                    self.finder.zone_name_from_id(self.finder.zone_id_of(index))
                    for index in range(self.finder.nr_of_polygons)
                    if self.finder.inside_of_polygon(index, x, y)
                }
            )


def register_source(connection):
    sid = source_id(connection, "timezone-boundary-builder")
    values = (
        "Timezone Boundary Builder",
        "https://github.com/evansiroky/timezone-boundary-builder",
        "ODbL-1.0",
        "https://opendatacommons.org/licenses/odbl/1-0/",
        "Timezone Boundary Builder; OpenStreetMap contributors; coordinate lookup via timezonefinder",
    )
    connection.execute(
        "UPDATE data_sources SET name=?,source_url=?,license_expression=?,license_url=?,attribution=? "
        "WHERE id=? AND (name,source_url,license_expression,license_url,attribution) IS NOT (?,?,?,?,?)",
        (*values, sid, *values),
    )


@lru_cache(maxsize=1)
def resolver():
    return Resolver()


def resolve(latitude, longitude, lookup):
    if latitude is None or longitude is None:
        return None, "missing_coordinates", []
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return None, "invalid", []
    candidates = sorted(set(lookup.candidates(latitude, longitude)))
    civil = [name for name in candidates if not name.startswith("Etc/")]
    if not civil:
        return None, "no_match", candidates
    try:
        for name in civil:
            zone(name)
    except ZoneInfoNotFoundError:
        return None, "invalid", candidates
    if len(civil) != 1 or len(candidates) != 1:
        return None, "ambiguous", candidates
    return civil[0], "resolved", candidates


def read_inputs(connection):
    return [
        dict(row)
        for row in connection.execute(
            "SELECT iata,timezone,latitude,longitude FROM airports ORDER BY iata"
        )
    ], state_rows(connection)


def plan(rows, states, lookup, incoming=(), release=()):
    original = digest([rows, states])
    effective = {row["iata"]: dict(row) for row in rows}
    for row in incoming:
        current = effective.setdefault(
            row["iata"], dict(iata=row["iata"], timezone=None, latitude=None, longitude=None)
        )
        for key in ("latitude", "longitude"):
            if current[key] is None:
                current[key] = row[key]
    release = set(release)
    if release - effective.keys():
        raise ValueError("Cannot release timezone ownership for an unknown airport")
    changes, counts = (
        [],
        dict(
            populated=0, changed=0, unchanged=0, protected=0, unresolved=0, ambiguous=0, invalid=0
        ),
    )
    for row in effective.values():
        code, current = row["iata"], row["timezone"]
        previous = states.get(code)
        owned = (
            previous and previous["owner"] == "reference" and previous["applied_zone"] == current
        )
        if code not in release and (
            (previous and not owned) or (not previous and current is not None)
        ):
            owner = previous["owner"] if previous and previous["owner"] != "reference" else "legacy"
            values = dict(
                iata=code,
                owner=owner,
                applied_zone=current,
                latitude=row["latitude"],
                longitude=row["longitude"],
                snapshot_id=None,
                status="protected",
                candidates="[]",
            )
            counts["protected"] += 1
        elif (
            owned
            and code not in release
            and previous["snapshot_id"] == lookup.identity
            and previous["latitude"] == row["latitude"]
            and previous["longitude"] == row["longitude"]
        ):
            values = previous
            counts["unchanged"] += 1
        else:
            value, status, candidates = resolve(row["latitude"], row["longitude"], lookup)
            values = dict(
                iata=code,
                owner="reference",
                applied_zone=value,
                latitude=row["latitude"],
                longitude=row["longitude"],
                snapshot_id=lookup.identity,
                status=status,
                candidates=normalized(candidates),
            )
            counts[
                "populated"
                if current is None and value is not None
                else "changed"
                if current != value
                else "unchanged"
            ] += 1
        if values["owner"] == "reference" and values["status"] != "resolved":
            counts["unresolved"] += 1
            if values["status"] in ("ambiguous", "invalid"):
                counts[values["status"]] += 1
        changes.append(values)
    return dict(original=original, states=changes, counts=counts)


def apply(connection, prepared, lookup, *, recheck=True):
    if recheck and digest(list(read_inputs(connection))) != prepared["original"]:
        raise ValueError("Airport inputs changed during timezone lookup; retry enrichment")
    connection.execute(
        "INSERT OR IGNORE INTO airport_timezone_snapshots VALUES(?,?)",
        (lookup.identity, normalized(lookup.manifest)),
    )
    register_source(connection)
    for values in prepared["states"]:
        connection.execute(
            "UPDATE airports SET timezone=? WHERE iata=? AND timezone IS NOT ?",
            (values["applied_zone"], values["iata"], values["applied_zone"]),
        )
        save_state(connection, values)


def enrich(path, *, dry_run=False, release=(), lookup=None):
    path = Path(path).resolve()
    lookup = lookup or resolver()
    # Dry-run opens existing storage read-only, without initialization or locks/files.
    if dry_run:
        with SQLiteConnection(path, read_only=True).connect() as connection:
            connection.execute("BEGIN")
            require_ingestion(connection)
            rows, states = read_inputs(connection)
            before = coverage(connection)
        prepared = plan(rows, states, lookup, release=release)
    else:
        with job_lock(path, blocking=False) as acquired:
            if not acquired:
                raise ValueError("Stop the active ingestion job before enriching timezones")
            store = IngestionStore(path)
            store.initialize()
            with store.connect() as connection:
                connection.execute("BEGIN")
                rows, states = read_inputs(connection)
                before = coverage(connection)
            prepared = plan(rows, states, lookup, release=release)
            with store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                apply(connection, prepared, lookup)
                check_quota(path, connection)
    return dict(
        dry_run=dry_run,
        snapshot_id=lookup.identity,
        reference=lookup.manifest,
        before=before,
        counts=prepared["counts"],
        result_sha256=digest(prepared["states"]),
        after=dict(
            airports=len(prepared["states"]),
            resolved=sum(v["applied_zone"] is not None for v in prepared["states"]),
        ),
        mappings=prepared["states"],
        serving_changed=False,
    )


def publish(source, output, *, replace_generated=True):
    counts = dict(applied=0, unchanged=0, protected=0, coordinate_conflicts=0, source_conflicts=0)
    if not has_state(source):
        return counts
    for row in source.execute("SELECT * FROM airport_timezone_snapshots"):
        output.execute("INSERT OR IGNORE INTO airport_timezone_snapshots VALUES(?,?)", tuple(row))
        register_source(output)
    existing = state_rows(output)
    for row in source.execute("SELECT * FROM airport_timezone_state ORDER BY iata"):
        values = dict(row)
        origin = source.execute(
            "SELECT timezone,latitude,longitude FROM airports WHERE iata=?", (row["iata"],)
        ).fetchone()
        if origin["timezone"] != row["applied_zone"]:
            # A producer-side external edit is also an override, never stale reference evidence.
            values.update(
                owner="legacy",
                applied_zone=origin["timezone"],
                snapshot_id=None,
                status="protected",
                candidates="[]",
                latitude=origin["latitude"],
                longitude=origin["longitude"],
            )
        elif values["owner"] == "reference" and (row["latitude"], row["longitude"]) != (
            origin["latitude"],
            origin["longitude"],
        ):
            counts["source_conflicts"] += 1
            continue
        target = output.execute(
            "SELECT timezone,latitude,longitude FROM airports WHERE iata=?", (row["iata"],)
        ).fetchone()
        if target is None:
            continue
        previous = existing.get(row["iata"])
        owned = (
            previous
            and previous["owner"] == "reference"
            and previous["applied_zone"] == target["timezone"]
        )
        if (previous and not owned) or (not previous and target["timezone"] is not None):
            protect(
                output,
                row["iata"],
                previous["owner"] if previous and previous["owner"] != "reference" else "legacy",
            )
            counts["protected"] += 1
            continue
        if owned and values["owner"] == "reference" and not replace_generated:
            # Serving -> producer hydration cannot roll back a newer producer dataset.
            continue
        if values["owner"] == "reference" and (row["latitude"], row["longitude"]) != (
            target["latitude"],
            target["longitude"],
        ):
            counts["coordinate_conflicts"] += 1
            # Remove a generated value that no longer describes serving coordinates.
            if owned:
                output.execute("UPDATE airports SET timezone=NULL WHERE iata=?", (row["iata"],))
                output.execute("DELETE FROM airport_timezone_state WHERE iata=?", (row["iata"],))
            continue
        output.execute(
            "UPDATE airports SET timezone=? WHERE iata=? AND timezone IS NOT ?",
            (values["applied_zone"], row["iata"], values["applied_zone"]),
        )
        save_state(output, values)
        counts[
            "unchanged"
            if previous == values and target["timezone"] == values["applied_zone"]
            else "applied"
        ] += 1
    return counts
