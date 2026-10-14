"""Lazy, recoverable itinerary storage with a database-owned disk quota and LRU."""

import gzip
import hashlib
import io
import json
import re
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from core.file_lock import file_mutex
from core.itinerary_catalog import ItineraryCatalog, ItineraryPayload, http_url
from core.models import FlightRecord

MAX_DOWNLOAD_BYTES = 64 * 1024**2


class ItinerarySourceError(RuntimeError):
    """A catalogue or flight chunk could not be fetched and validated."""


def cache_directory(path):
    path = Path(path)
    return path.with_name(path.name + ".itineraries")


@contextmanager
def control(path, *, writable=False):
    # Explicit mode prevents both policy inspection and setters creating a missing DB.
    mode = "rw" if writable else "ro"
    connection = sqlite3.connect(
        Path(path).resolve().as_uri() + f"?mode={mode}", uri=True, timeout=10
    )
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def policy(path, *, include_catalog=True):
    if not Path(path).is_file():
        return None
    with control(path) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='itinerary_cache_policy'"
        ).fetchone():
            return None
        row = connection.execute(
            "SELECT id,quota_bytes,source_url,access_sequence,evicted_chunks FROM itinerary_cache_policy WHERE id=1"
        ).fetchone()
        if row is None:
            return None
        state = dict(row)
        if include_catalog:
            catalog = connection.execute(
                "SELECT url,data FROM itinerary_catalog_state WHERE id=1"
            ).fetchone()
            state["catalog_url"], state["catalog_json"] = catalog if catalog else (None, None)
        return state


def file_bytes(path):
    try:
        return path.stat().st_size if path.is_file() else 0
    except FileNotFoundError:
        # Another process can retire a payload or checkpoint WAL during accounting.
        return 0


def database_bytes(path):
    path = Path(path)
    return sum(file_bytes(p) for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")))


def usage_bytes(path):
    path = Path(path)
    permanent = database_bytes(path)
    root = cache_directory(path)
    cached = (
        sum(file_bytes(p) for p in root.iterdir() if p.name != ".budget.lock")
        if root.is_dir()
        else 0
    )
    return permanent, cached


def enforce_quota(path):
    """Next-IO retirement. Busy readers keep their chunks; permanent facts stay intact."""
    root = cache_directory(path)
    if not root.is_dir() or not Path(path).is_file():
        return
    with file_mutex(root / ".budget.lock", blocking=False) as acquired:
        if not acquired:
            return
        state = policy(path, include_catalog=False)
        if state is None:
            return
        # The common path performs one size scan without sorting every LRU entry
        # or opening checkpoint writers. Reset's disabled source also cleans orphans.
        if state["source_url"] != "" and (
            state["quota_bytes"] is None or sum(usage_bytes(path)) <= state["quota_bytes"]
        ):
            return
        with control(path, writable=True) as connection:
            connection.execute("PRAGMA busy_timeout=0")
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
            entries = connection.execute(
                "SELECT sha256 FROM itinerary_cache_entries ORDER BY last_access, sha256"
            ).fetchall()
        known = {row[0] for row in entries}
        # Interrupted builders and files whose registry was reset are expendable.
        for file in root.iterdir():
            match = re.fullmatch(r"([a-f0-9]{64})(?:\.partial)?\.sqlite(?:-journal)?", file.name)
            if match and (match[1] not in known or ".partial" in file.name):
                with file_mutex(root / "locks" / (match[1] + ".lock"), blocking=False) as free:
                    if free:
                        file.unlink(missing_ok=True)
        _, cached_bytes = usage_bytes(path)
        for row in entries:
            # A parallel administrator can raise or lower the quota during cleanup.
            with control(path, writable=True) as connection:
                connection.execute("PRAGMA busy_timeout=0")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                quota = connection.execute(
                    "SELECT quota_bytes FROM itinerary_cache_policy WHERE id=1"
                ).fetchone()[0]
            permanent_bytes = database_bytes(path)
            if quota is None or permanent_bytes + cached_bytes <= quota:
                break
            digest = row[0]
            with file_mutex(root / "locks" / (digest + ".lock"), blocking=False) as free:
                if not free:
                    continue
                file = root / (digest + ".sqlite")
                removed_bytes = file.stat().st_size if file.is_file() else 0
                file.unlink(missing_ok=True)
                cached_bytes -= removed_bytes
                with control(path, writable=True) as connection:
                    connection.execute(
                        "DELETE FROM itinerary_cache_entries WHERE sha256=?", (digest,)
                    )
                    connection.execute(
                        "UPDATE itinerary_cache_policy SET evicted_chunks=evicted_chunks+1 WHERE id=1"
                    )
        with control(path, writable=True) as connection:
            connection.execute("PRAGMA busy_timeout=0")
            # If another reader pins WAL frames, physical reclamation waits for later IO.
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def set_quota(path, quota_bytes):
    if quota_bytes is not None and (type(quota_bytes) is not int or quota_bytes < 0):
        raise ValueError("Quota must be a non-negative byte count or unlimited")
    with control(path, writable=True) as connection:
        if (
            connection.execute(
                "UPDATE itinerary_cache_policy SET quota_bytes=? WHERE id=1", (quota_bytes,)
            ).rowcount
            != 1
        ):
            raise ValueError("Initialize cache policy with maintenance init first")
    # No eviction here: a smaller limit is deliberately deferred to next data IO.


def set_source(path, url):
    if url:
        http_url(url)
    with control(path, writable=True) as connection:
        connection.execute("UPDATE itinerary_cache_policy SET source_url=? WHERE id=1", (url,))
        connection.execute("UPDATE itinerary_catalog_state SET data=NULL, url=NULL WHERE id=1")


def cache_status(path):
    enforce_quota(path)
    state = policy(path)
    if state is None:
        raise ValueError("Initialize cache policy with maintenance init first")
    permanent, cached = usage_bytes(path)
    with control(path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM itinerary_cache_entries").fetchone()[0]
    return {
        "quota_bytes": state["quota_bytes"],
        "used_bytes": permanent + cached,
        "database_bytes": permanent,
        "cached_bytes": cached,
        "cached_chunks": count,
        "over_quota_bytes": max(0, permanent + cached - state["quota_bytes"])
        if state["quota_bytes"] is not None
        else 0,
        "evicted_chunks": state["evicted_chunks"],
        "source_url": state["source_url"]
        if state["source_url"] is not None
        else state["catalog_url"],
        "cache_directory": str(cache_directory(path).resolve()),
    }


class ItineraryCache:
    def __init__(self, path, *, source_url=None, client=None):
        self.path, self.source_url, self.client = Path(path), source_url, client
        self._json, self._catalog = None, None
        self._parsed_lock = threading.Lock()

    def _download(self, url, *, expected_bytes=None, sha256=None):
        http_url(url)
        owns = self.client is None
        client = self.client or httpx.Client(timeout=30, follow_redirects=True)
        try:
            content = bytearray()
            with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
                response.raise_for_status()
                chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
                for chunk in chunks:
                    content.extend(chunk)
                    if len(content) > (expected_bytes or MAX_DOWNLOAD_BYTES):
                        raise ValueError("Itinerary download exceeds its size limit")
            if expected_bytes is not None and len(content) != expected_bytes:
                raise ValueError("Incomplete itinerary download")
            if sha256 and hashlib.sha256(content).hexdigest() != sha256:
                raise ValueError("Itinerary SHA-256 mismatch")
            return bytes(content)
        except (httpx.HTTPError, ValueError) as error:
            raise ItinerarySourceError(str(error)) from error
        finally:
            if owns:
                client.close()

    def catalog(self):
        state = policy(self.path)
        if state is None:
            return None
        url = state["source_url"] if state["source_url"] is not None else self.source_url
        if not url:
            return None
        if state["catalog_url"] != url or state["catalog_json"] is None:
            root = cache_directory(self.path)
            with file_mutex(root / ".catalog.lock"):
                state = policy(self.path)
                if state["catalog_url"] != url or state["catalog_json"] is None:
                    try:
                        catalog = ItineraryCatalog.model_validate_json(self._download(url))
                    except ValueError as error:
                        raise ItinerarySourceError("Invalid itinerary catalogue") from error
                    encoded = catalog.model_dump_json()
                    with control(self.path, writable=True) as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        current = connection.execute(
                            "SELECT source_url FROM itinerary_cache_policy WHERE id=1"
                        ).fetchone()[0]
                        current = current if current is not None else self.source_url
                        if current != url:
                            raise ItinerarySourceError(
                                "Itinerary source changed during download; retry"
                            )
                        connection.execute(
                            "UPDATE itinerary_catalog_state SET url=?, data=? WHERE id=1",
                            (url, encoded),
                        )
                        connection.executemany(
                            "INSERT INTO airports(iata,name,city_id,timezone,latitude,longitude) VALUES (:iata,:name,:city_id,:timezone,:latitude,:longitude) ON CONFLICT(iata) DO UPDATE SET name=COALESCE(airports.name,excluded.name),city_id=COALESCE(airports.city_id,excluded.city_id),timezone=COALESCE(airports.timezone,excluded.timezone),latitude=COALESCE(airports.latitude,excluded.latitude),longitude=COALESCE(airports.longitude,excluded.longitude)",
                            [a.model_dump() for a in catalog.airports],
                        )
                        for transfer in catalog.transfers:
                            cities = connection.execute(
                                "SELECT city_id FROM airports WHERE iata IN (?,?)",
                                (transfer.arrival_iata, transfer.departure_iata),
                            ).fetchall()
                            if (
                                len(cities) == 2
                                and cities[0][0] is not None
                                and cities[0][0] == cities[1][0]
                            ):
                                connection.execute(
                                    "INSERT OR IGNORE INTO airport_transfers VALUES (:arrival_iata,:departure_iata,:minimum_minutes,:source)",
                                    transfer.model_dump(),
                                )
                    state["catalog_json"] = encoded
        with self._parsed_lock:
            if state["catalog_json"] != self._json:
                self._catalog = ItineraryCatalog.model_validate_json(state["catalog_json"])
                self._json = state["catalog_json"]
            return self._catalog

    def _build(self, chunk, file):
        content = self._download(chunk.url, expected_bytes=chunk.bytes, sha256=chunk.sha256)
        try:
            if chunk.encoding == "gzip":
                with gzip.GzipFile(fileobj=io.BytesIO(content)) as source:
                    content = source.read(MAX_DOWNLOAD_BYTES + 1)
            if len(content) > MAX_DOWNLOAD_BYTES:
                raise ValueError("Itinerary payload exceeds decompressed size limit")
            records = ItineraryPayload.model_validate_json(content).flights
            if len(records) != chunk.flight_count or len(
                {(f.provider, f.provider_id) for f in records}
            ) != len(records):
                raise ValueError("Itinerary row count or identities disagree with catalogue")
            if any(
                f.effective_time_basis != chunk.time_basis
                or f.provider != chunk.provider
                or f.origin_iata != chunk.origin
                or not chunk.departure_start <= f.departure_lower < chunk.departure_end
                for f in records
            ):
                raise ValueError("Itinerary records fall outside catalogue scope")
            active = [f for f in records if f.status != "cancelled"]
            local = ZoneInfo(chunk.origin_timezone) if chunk.origin_timezone else None
            expected_local = (
                sorted(
                    {
                        f.departure_lower.astimezone(local).date()
                        for f in active
                        if f.departure_lower.astimezone(local).date()
                        == f.departure_upper.astimezone(local).date()
                    }
                )
                if local
                else []
            )
            if (
                sorted({f.destination_iata for f in records}) != chunk.destinations
                or len(records) - len(active) != chunk.cancelled_count
                or sorted(
                    {
                        f.departure_lower.date()
                        for f in active
                        if f.departure_lower.date() == f.departure_upper.date()
                    }
                )
                != chunk.dates_utc
                or expected_local != chunk.dates_local
                or min((f.departure_lower for f in active), default=None) != chunk.first_departure
                or max((f.departure_lower for f in active), default=None) != chunk.last_departure
                or min(f.fetched_at for f in records) != chunk.oldest_fact_refresh
                or max(f.fetched_at for f in records) != chunk.newest_fact_refresh
            ):
                raise ValueError("Itinerary coverage disagrees with catalogue")
        except (ValueError, OSError, EOFError) as error:
            raise ItinerarySourceError(str(error)) from error
        partial = file.with_name(chunk.sha256 + ".partial.sqlite")
        partial.unlink(missing_ok=True)
        try:
            with closing(sqlite3.connect(partial)) as connection, connection:
                from core.storage import FLIGHT_SCHEMA, store_flight

                connection.executescript(FLIGHT_SCHEMA)
                for record in records:
                    store_flight(connection, record)
            with control(self.path, writable=True) as connection:
                connection.executemany(
                    "INSERT INTO itinerary_cache_refs VALUES (?,?,?) ON CONFLICT(provider,provider_id) DO UPDATE SET sha256=excluded.sha256",
                    [(f.provider, f.provider_id, chunk.sha256) for f in records],
                )
            partial.replace(file)
        finally:
            partial.unlink(missing_ok=True)

    @contextmanager
    def access(self, chunk):
        enforce_quota(self.path)
        root = cache_directory(self.path)
        with file_mutex(root / "locks" / (chunk.sha256 + ".lock")):
            file = root / (chunk.sha256 + ".sqlite")
            if file.is_file():
                with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as old:
                    compatible = old.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='schema_metadata'"
                    ).fetchone()
                if not compatible:
                    file.unlink()
            if not file.is_file():
                self._build(chunk, file)
            with control(self.path, writable=True) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE itinerary_cache_policy SET access_sequence=access_sequence+1 WHERE id=1"
                )
                sequence = connection.execute(
                    "SELECT access_sequence FROM itinerary_cache_policy WHERE id=1"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO itinerary_cache_entries VALUES (?,?) ON CONFLICT(sha256) DO UPDATE SET last_access=excluded.last_access",
                    (chunk.sha256, sequence),
                )
            connection = sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()
        enforce_quota(self.path)

    def flight(self, provider, provider_id, *, catalog=None):
        catalog = catalog if catalog is not None else self.catalog()
        if catalog is None:
            return None
        with control(self.path) as connection:
            if connection.execute(
                "SELECT 1 FROM itinerary_cache_deleted WHERE provider=? AND provider_id=?",
                (provider, provider_id),
            ).fetchone():
                return None
            if connection.execute(
                "SELECT 1 FROM flight_tombstones t JOIN data_sources s ON s.id=t.source_id WHERE s.code=? AND t.source_record_key=?",
                (provider, provider_id),
            ).fetchone():
                return None
            ref = connection.execute(
                "SELECT sha256 FROM itinerary_cache_refs WHERE provider=? AND provider_id=?",
                (provider, provider_id),
            ).fetchone()
        chunk = next((c for c in catalog.chunks if ref is not None and c.sha256 == ref[0]), None)
        if chunk is None:
            return None
        with self.access(chunk) as connection:
            row = connection.execute(
                "SELECT * FROM flights WHERE provider=? AND provider_id=?", (provider, provider_id)
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["codeshares"] = json.loads(data["codeshares"])
        return FlightRecord.model_validate(data)
