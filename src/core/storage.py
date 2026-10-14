"""Embedded SQLite repository shared by independent local Python processes."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS flights (
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    flight_number TEXT NOT NULL,
    airline_iata TEXT,
    origin_iata TEXT NOT NULL,
    destination_iata TEXT NOT NULL,
    departure_scheduled TEXT NOT NULL,
    arrival_scheduled TEXT NOT NULL,
    departure_actual TEXT,
    arrival_actual TEXT,
    status TEXT NOT NULL,
    codeshares TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    aircraft_type_code TEXT,
    PRIMARY KEY (provider, provider_id)
);
CREATE INDEX IF NOT EXISTS idx_flights_route_departure
ON flights(origin_iata, destination_iata, departure_scheduled);
CREATE INDEX IF NOT EXISTS idx_flights_departure ON flights(departure_scheduled);
CREATE INDEX IF NOT EXISTS idx_flights_origin_departure ON flights(origin_iata, departure_scheduled);
CREATE INDEX IF NOT EXISTS idx_flights_routing
ON flights(origin_iata, departure_scheduled, provider, provider_id) WHERE status != 'cancelled';
CREATE TABLE IF NOT EXISTS airports (
    iata TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city_id TEXT NOT NULL,
    timezone TEXT NOT NULL,
    latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180)
);
CREATE INDEX IF NOT EXISTS idx_airports_city ON airports(city_id, iata);
CREATE TABLE IF NOT EXISTS airport_transfers (
    arrival_iata TEXT NOT NULL REFERENCES airports(iata),
    departure_iata TEXT NOT NULL REFERENCES airports(iata),
    minimum_minutes INTEGER NOT NULL CHECK(minimum_minutes BETWEEN 1 AND 1440),
    source TEXT NOT NULL,
    PRIMARY KEY (arrival_iata, departure_iata),
    CHECK(arrival_iata != departure_iata)
);
CREATE TABLE IF NOT EXISTS itinerary_cache_policy (
    id INTEGER PRIMARY KEY CHECK(id=1),
    quota_bytes INTEGER CHECK(quota_bytes >= 0),
    source_url TEXT,
    access_sequence INTEGER NOT NULL DEFAULT 0,
    evicted_chunks INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ingestion_profiles (
    source TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    last_used INTEGER NOT NULL
);
INSERT OR IGNORE INTO itinerary_cache_policy(id, quota_bytes) VALUES (1, 21474836480);
CREATE TABLE IF NOT EXISTS itinerary_catalog_state (
    id INTEGER PRIMARY KEY CHECK(id=1),
    url TEXT,
    data TEXT
);
INSERT OR IGNORE INTO itinerary_catalog_state(id) VALUES (1);
CREATE TABLE IF NOT EXISTS itinerary_cache_entries (
    sha256 TEXT PRIMARY KEY,
    last_access INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_itinerary_cache_lru ON itinerary_cache_entries(last_access, sha256);
CREATE TABLE IF NOT EXISTS itinerary_cache_deleted (
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    PRIMARY KEY(provider, provider_id)
);
CREATE TABLE IF NOT EXISTS itinerary_cache_refs (
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(provider, provider_id)
);
"""


def utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class SQLiteConnection:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only

    @contextmanager
    def connect(self):
        # Cache policy is shared on disk, so live admin changes apply at the next IO.
        # This never creates or migrates a database and never retires permanent facts.
        from core.itinerary_cache import enforce_quota

        enforce_quota(self.path)
        if self.read_only:
            connection = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10
            )
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()
