"""Embedded SQLite repository shared by independent local Python processes."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = '''
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
    PRIMARY KEY (provider, provider_id)
);
CREATE INDEX IF NOT EXISTS idx_flights_route_departure
ON flights(origin_iata, destination_iata, departure_scheduled);
CREATE INDEX IF NOT EXISTS idx_flights_departure ON flights(departure_scheduled);
'''


def utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError('timestamps must include a UTC offset')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


class SQLiteConnection:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only

    @contextmanager
    def connect(self):
        if self.read_only:
            connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
            connection.execute('PRAGMA query_only=ON')
        else:
            connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()
