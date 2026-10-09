"""Read-only flight queries for serving clients; never initializes or mutates data."""
import json
from datetime import datetime
from pathlib import Path

from flight_engine.core.models import FlightRecord, RouteSummary
from flight_engine.core.storage import SQLiteConnection, utc_text


class FlightReader(SQLiteConnection):
    def __init__(self, path: Path):
        super().__init__(path, read_only=True)

    def stats(self) -> dict:
        with self.connect() as connection:
            count = connection.execute('SELECT COUNT(*) FROM flights').fetchone()[0]
            providers = dict(connection.execute('SELECT provider, COUNT(*) FROM flights GROUP BY provider').fetchall())
            mode = connection.execute('PRAGMA journal_mode').fetchone()[0]
        return {'data_file': str(self.path), 'flights': count, 'providers': providers, 'journal_mode': mode}

    @staticmethod
    def _record(row):
        data = dict(row)
        data['codeshares'] = json.loads(data['codeshares'])
        return FlightRecord.model_validate(data)

    def all_records(self) -> list[FlightRecord]:
        with self.connect() as connection:
            return [self._record(row) for row in connection.execute('SELECT * FROM flights ORDER BY departure_scheduled')]

    @staticmethod
    def _where(start, end, origin, destination, include_cancelled=False):
        clauses = ['departure_scheduled >= ?', 'departure_scheduled < ?']
        params = [utc_text(start), utc_text(end)]
        for name, value in (('origin_iata', origin), ('destination_iata', destination)):
            if value:
                clauses.append(f'{name} = ?')
                params.append(value.upper())
        if not include_cancelled:
            clauses.append("status != 'cancelled'")
        return ' AND '.join(clauses), params

    def search(self, start: datetime, end: datetime, origin: str | None = None,
               destination: str | None = None, include_cancelled: bool = False,
               limit: int = 500) -> list[FlightRecord]:
        where, params = self._where(start, end, origin, destination, include_cancelled)
        with self.connect() as connection:
            return [self._record(row) for row in connection.execute(
                f'SELECT * FROM flights WHERE {where} ORDER BY departure_scheduled LIMIT ?', [*params, limit])]

    def routes(self, start: datetime, end: datetime, origin: str | None = None,
               destination: str | None = None, limit: int = 500) -> list[RouteSummary]:
        where, params = self._where(start, end, origin, destination)
        with self.connect() as connection:
            rows = connection.execute(f'''SELECT origin_iata, destination_iata, COUNT(*) flight_count,
                GROUP_CONCAT(DISTINCT airline_iata) airlines, MIN(departure_scheduled) first_departure,
                MAX(departure_scheduled) last_departure FROM flights WHERE {where}
                GROUP BY origin_iata, destination_iata
                ORDER BY flight_count DESC, origin_iata, destination_iata LIMIT ?''', [*params, limit])
            return [RouteSummary(origin_iata=row['origin_iata'], destination_iata=row['destination_iata'],
                                 flight_count=row['flight_count'], airlines=sorted(filter(None, (row['airlines'] or '').split(','))),
                                 first_departure=datetime.fromisoformat(row['first_departure']),
                                 last_departure=datetime.fromisoformat(row['last_departure'])) for row in rows]
