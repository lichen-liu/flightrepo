"""Internal database initialization and mutation; not imported by the serving API."""
import json
from collections.abc import Iterable

from core.models import FlightRecord
from core.connections import Airport, AirportTransfer
from core.storage import SQLiteConnection, SCHEMA, utc_text


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
            connection.execute('DELETE FROM airport_transfers')
            connection.execute('DELETE FROM airports')

    def update_airports(self, airports: Iterable[Airport], transfers: Iterable[AirportTransfer]) -> None:
        """Validate a complete metadata batch, then write it atomically."""
        airports = [Airport.model_validate(a.model_dump()) for a in airports]
        transfers = [AirportTransfer.model_validate(t.model_dump()) for t in transfers]
        with self.connect() as connection:
            connection.executemany('''INSERT INTO airports VALUES (:iata, :name, :city_id, :timezone, :latitude, :longitude)
                ON CONFLICT(iata) DO UPDATE SET name=excluded.name, city_id=excluded.city_id,
                timezone=excluded.timezone, latitude=excluded.latitude, longitude=excluded.longitude''',
                [a.model_dump() for a in airports])
            invalid = connection.execute('''SELECT 1 FROM airport_transfers t
                JOIN airports a ON a.iata=t.arrival_iata JOIN airports b ON b.iata=t.departure_iata
                WHERE a.city_id != b.city_id LIMIT 1''').fetchone()
            if invalid:
                raise ValueError('city update would invalidate an existing transfer')
            for transfer in transfers:
                rows = connection.execute('SELECT city_id FROM airports WHERE iata IN (?, ?)',
                                          (transfer.arrival_iata, transfer.departure_iata)).fetchall()
                if len(rows) != 2 or rows[0]['city_id'] != rows[1]['city_id']:
                    raise ValueError('transfer airports must exist and share a city_id')
            connection.executemany('''INSERT INTO airport_transfers VALUES
                (:arrival_iata, :departure_iata, :minimum_minutes, :source)
                ON CONFLICT(arrival_iata, departure_iata) DO UPDATE SET
                minimum_minutes=excluded.minimum_minutes, source=excluded.source''',
                [t.model_dump() for t in transfers])

    def delete(self, provider: str, provider_id: str) -> int:
        with self.connect() as connection:
            return connection.execute('DELETE FROM flights WHERE provider=? AND provider_id=?',
                                      (provider, provider_id)).rowcount
