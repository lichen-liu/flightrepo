import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from flight_engine.models import FlightRecord, FlightStatus, RouteSummary


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
    codeshares TEXT NOT NULL DEFAULT '[]',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (provider, provider_id)
);
CREATE INDEX IF NOT EXISTS idx_flights_route_departure
ON flights(origin_iata, destination_iata, departure_scheduled);
CREATE INDEX IF NOT EXISTS idx_flights_departure
ON flights(departure_scheduled);
"""


class FlightStore:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def upsert(self, records: Iterable[FlightRecord]) -> int:
        rows = [self._to_row(record) for record in records]
        if not rows:
            return 0
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO flights VALUES (
                    :provider, :provider_id, :flight_number, :airline_iata,
                    :origin_iata, :destination_iata, :departure_scheduled,
                    :arrival_scheduled, :departure_actual, :arrival_actual,
                    :status, :codeshares, :fetched_at
                ) ON CONFLICT(provider, provider_id) DO UPDATE SET
                    flight_number=excluded.flight_number,
                    airline_iata=excluded.airline_iata,
                    origin_iata=excluded.origin_iata,
                    destination_iata=excluded.destination_iata,
                    departure_scheduled=excluded.departure_scheduled,
                    arrival_scheduled=excluded.arrival_scheduled,
                    departure_actual=excluded.departure_actual,
                    arrival_actual=excluded.arrival_actual,
                    status=excluded.status,
                    codeshares=excluded.codeshares,
                    fetched_at=excluded.fetched_at""",
                rows,
            )
        return len(rows)

    def search(self, start: datetime, end: datetime, origin: str | None = None,
               destination: str | None = None, include_cancelled: bool = False,
               limit: int = 500) -> list[FlightRecord]:
        clauses = ["departure_scheduled >= ?", "departure_scheduled < ?"]
        params: list[object] = [start.isoformat(), end.isoformat()]
        if origin:
            clauses.append("origin_iata = ?")
            params.append(origin.upper())
        if destination:
            clauses.append("destination_iata = ?")
            params.append(destination.upper())
        if not include_cancelled:
            clauses.append("status != 'cancelled'")
        params.append(limit)
        sql = f"SELECT * FROM flights WHERE {' AND '.join(clauses)} ORDER BY departure_scheduled LIMIT ?"
        with self.connect() as connection:
            return [self._from_row(row) for row in connection.execute(sql, params)]

    def routes(self, start: datetime, end: datetime, origin: str | None = None,
               destination: str | None = None, limit: int = 500) -> list[RouteSummary]:
        clauses = ["departure_scheduled >= ?", "departure_scheduled < ?", "status != 'cancelled'"]
        params: list[object] = [start.isoformat(), end.isoformat()]
        if origin:
            clauses.append("origin_iata = ?")
            params.append(origin.upper())
        if destination:
            clauses.append("destination_iata = ?")
            params.append(destination.upper())
        params.append(limit)
        sql = f"""SELECT origin_iata, destination_iata, COUNT(*) flight_count,
            GROUP_CONCAT(DISTINCT airline_iata) airlines,
            MIN(departure_scheduled) first_departure,
            MAX(departure_scheduled) last_departure
            FROM flights WHERE {' AND '.join(clauses)}
            GROUP BY origin_iata, destination_iata
            ORDER BY flight_count DESC LIMIT ?"""
        with self.connect() as connection:
            return [RouteSummary(
                origin_iata=row["origin_iata"], destination_iata=row["destination_iata"],
                flight_count=row["flight_count"],
                airlines=sorted(filter(None, (row["airlines"] or "").split(","))),
                first_departure=datetime.fromisoformat(row["first_departure"]),
                last_departure=datetime.fromisoformat(row["last_departure"]),
            ) for row in connection.execute(sql, params)]

    @staticmethod
    def _to_row(record: FlightRecord) -> dict[str, object]:
        data = record.model_dump()
        for field in ("departure_scheduled", "arrival_scheduled", "departure_actual", "arrival_actual", "fetched_at"):
            data[field] = data[field].isoformat() if data[field] else None
        data["status"] = record.status.value
        data["codeshares"] = json.dumps(record.codeshares)
        return data

    @staticmethod
    def _from_row(row: sqlite3.Row) -> FlightRecord:
        data = dict(row)
        data["codeshares"] = json.loads(data["codeshares"])
        data["status"] = FlightStatus(data["status"])
        return FlightRecord.model_validate(data)

