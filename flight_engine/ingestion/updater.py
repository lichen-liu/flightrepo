"""Internal database initialization and mutation; not imported by the serving API."""
import json
from collections.abc import Iterable

from flight_engine.core.models import FlightRecord
from flight_engine.core.storage import SQLiteConnection, SCHEMA, utc_text


class FlightUpdater(SQLiteConnection):
    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            mode = connection.execute('PRAGMA journal_mode=WAL').fetchone()[0]
            if mode != 'wal':
                raise RuntimeError('SQLite WAL mode could not be enabled')
            connection.executescript(SCHEMA)

    def upsert(self, records: Iterable[FlightRecord]) -> int:
        rows = []
        for record in records:
            record = FlightRecord.model_validate(record.model_dump())
            data = record.model_dump(mode='json')
            for field in ('departure_scheduled', 'arrival_scheduled', 'departure_actual', 'arrival_actual', 'fetched_at'):
                value = getattr(record, field)
                data[field] = utc_text(value) if value else None
            data['codeshares'] = json.dumps(record.codeshares)
            rows.append(data)
        if not rows:
            return 0
        columns = list(rows[0])
        updates = ', '.join(f'{name}=excluded.{name}' for name in columns if name not in ('provider', 'provider_id'))
        sql = (f"INSERT INTO flights ({', '.join(columns)}) VALUES ({', '.join(':' + name for name in columns)}) "
               f'ON CONFLICT(provider, provider_id) DO UPDATE SET {updates}')
        with self.connect() as connection:
            connection.executemany(sql, rows)
        return len(rows)

    def reset(self) -> None:
        self.initialize()
        # Transactional deletion keeps existing connections valid; never unlink a live DB.
        with self.connect() as connection:
            connection.execute('DELETE FROM flights')

    def delete(self, provider: str, provider_id: str) -> int:
        with self.connect() as connection:
            return connection.execute('DELETE FROM flights WHERE provider=? AND provider_id=?',
                                      (provider, provider_id)).rowcount
