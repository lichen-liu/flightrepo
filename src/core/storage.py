"""Embedded SQLite repository shared by independent local Python processes."""

import os
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
    aircraft TEXT,
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
CREATE TABLE IF NOT EXISTS aircraft_observations (
    day TEXT NOT NULL,
    icao_hex TEXT NOT NULL,
    type_code TEXT,
    data TEXT NOT NULL,
    PRIMARY KEY (day, icao_hex)
);
CREATE INDEX IF NOT EXISTS idx_observations_type_day
ON aircraft_observations(type_code, day, icao_hex);
CREATE TABLE IF NOT EXISTS observation_imports (
    day TEXT PRIMARY KEY,
    filter_sha256 TEXT NOT NULL,
    stats TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observation_archives (
    day TEXT NOT NULL,
    part INTEGER NOT NULL,
    url TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    PRIMARY KEY (day, part)
);
"""


def utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


@contextmanager
def adsblol_budget_lock(root):
    """Serialize archive writers and cache eviction under the shared storage cap."""
    with (root / ".trace-cache.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
            yield


class SQLiteConnection:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only

    @contextmanager
    def connect(self):
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
