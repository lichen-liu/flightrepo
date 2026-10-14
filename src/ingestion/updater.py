"""Internal database initialization and mutation; not imported by the serving API."""

import json
from collections.abc import Iterable

from core.connections import Airport, AirportTransfer
from core.itinerary_cache import ItineraryCache
from core.models import FlightRecord
from core.storage import SCHEMA, SQLiteConnection, utc_text


class FlightUpdater(SQLiteConnection):
    def __init__(self, path, *, cache=None):
        super().__init__(path)
        self.cache = cache or ItineraryCache(path)

    def _remote_original(self, provider, provider_id):
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM flights WHERE provider=? AND provider_id=?", (provider, provider_id)
            ).fetchone()
        if exists:
            return None, None
        catalog = self.cache.catalog()
        if catalog is None:
            return None, None
        remote = self.cache.flight(provider, provider_id, catalog=catalog)
        return remote, catalog.model_dump_json() if remote else None

    @staticmethod
    def _cache_schema(connection):
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='itinerary_cache_deleted'"
            ).fetchone()
            is not None
        )

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                raise RuntimeError("SQLite WAL mode could not be enabled")
            connection.executescript(SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            old_policy = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(itinerary_cache_policy)")
            }
            if "catalog_json" in old_policy:
                connection.execute(
                    "UPDATE itinerary_catalog_state SET url=(SELECT catalog_url FROM itinerary_cache_policy WHERE id=1), data=(SELECT catalog_json FROM itinerary_cache_policy WHERE id=1) WHERE id=1 AND data IS NULL"
                )
                connection.execute(
                    "UPDATE itinerary_cache_policy SET catalog_json=NULL, catalog_url=NULL WHERE id=1"
                )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(flights)")}
            if "aircraft_type_code" not in columns:
                connection.execute("ALTER TABLE flights ADD COLUMN aircraft_type_code TEXT")

    def upsert(self, records: Iterable[FlightRecord]) -> int:
        rows = []
        for record in records:
            record = FlightRecord.model_validate(record.model_dump())
            data = record.model_dump(mode="json")
            for field in (
                "departure_scheduled",
                "arrival_scheduled",
                "departure_actual",
                "arrival_actual",
                "fetched_at",
            ):
                value = getattr(record, field)
                data[field] = utc_text(value) if value else None
            data["codeshares"] = json.dumps(record.codeshares)
            data["aircraft_type_code"] = record.aircraft_type_code
            rows.append(data)
        if not rows:
            return 0
        columns = list(rows[0])
        updates = ", ".join(
            f"{name}=excluded.{name}" for name in columns if name not in ("provider", "provider_id")
        )
        sql = (
            f"INSERT INTO flights ({', '.join(columns)}) VALUES ({', '.join(':' + name for name in columns)}) "
            f"ON CONFLICT(provider, provider_id) DO UPDATE SET {updates}"
        )
        with self.connect() as connection:
            connection.executemany(sql, rows)
            if self._cache_schema(connection):
                connection.executemany(
                    "DELETE FROM itinerary_cache_deleted WHERE provider=? AND provider_id=?",
                    [(record["provider"], record["provider_id"]) for record in rows],
                )
        return len(rows)

    def reset(self) -> None:
        self.initialize()
        # Transactional deletion keeps existing connections valid; never unlink a live DB.
        with self.connect() as connection:
            connection.execute("DELETE FROM flights")
            connection.execute("DELETE FROM airport_transfers")
            connection.execute("DELETE FROM airports")
            connection.execute("DELETE FROM itinerary_cache_entries")
            connection.execute("DELETE FROM itinerary_cache_refs")
            connection.execute("DELETE FROM itinerary_cache_deleted")
            connection.execute("UPDATE itinerary_cache_policy SET source_url='' WHERE id=1")
            connection.execute("UPDATE itinerary_catalog_state SET url=NULL, data=NULL WHERE id=1")
            # A hosted file may also hold observed itinerary facts and their resume state.
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in (
                "itinerary_legs",
                "extraction_days",
                "pending_movements",
                "extraction_boundaries",
                "extraction_config",
                "extraction_sources",
            ):
                if table in tables:
                    connection.execute(f'DELETE FROM "{table}"')

    def create_airport(self, airport: Airport) -> None:
        """Insert one validated airport without replacing existing metadata."""
        airport = Airport.model_validate(airport.model_dump())
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO airports (iata, name, city_id, timezone, latitude, longitude)
                VALUES (:iata, :name, :city_id, :timezone, :latitude, :longitude)
                ON CONFLICT(iata) DO NOTHING""",
                airport.model_dump(),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"Airport {airport.iata} already exists; use airports import to update it"
                )

    def update_airports(
        self, airports: Iterable[Airport], transfers: Iterable[AirportTransfer]
    ) -> None:
        """Validate a complete metadata batch, then write it atomically."""
        airports = [Airport.model_validate(a.model_dump()) for a in airports]
        transfers = [AirportTransfer.model_validate(t.model_dump()) for t in transfers]
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO airports VALUES (:iata, :name, :city_id, :timezone, :latitude, :longitude)
                ON CONFLICT(iata) DO UPDATE SET name=excluded.name, city_id=excluded.city_id,
                timezone=excluded.timezone, latitude=excluded.latitude, longitude=excluded.longitude""",
                [a.model_dump() for a in airports],
            )
            invalid = connection.execute("""SELECT 1 FROM airport_transfers t
                JOIN airports a ON a.iata=t.arrival_iata JOIN airports b ON b.iata=t.departure_iata
                WHERE a.city_id != b.city_id LIMIT 1""").fetchone()
            if invalid:
                raise ValueError("city update would invalidate an existing transfer")
            for transfer in transfers:
                rows = connection.execute(
                    "SELECT city_id FROM airports WHERE iata IN (?, ?)",
                    (transfer.arrival_iata, transfer.departure_iata),
                ).fetchall()
                if len(rows) != 2 or rows[0]["city_id"] != rows[1]["city_id"]:
                    raise ValueError("transfer airports must exist and share a city_id")
            connection.executemany(
                """INSERT INTO airport_transfers VALUES
                (:arrival_iata, :departure_iata, :minimum_minutes, :source)
                ON CONFLICT(arrival_iata, departure_iata) DO UPDATE SET
                minimum_minutes=excluded.minimum_minutes, source=excluded.source""",
                [t.model_dump() for t in transfers],
            )

    def delete(
        self, provider: str, provider_id: str, *, expected: FlightRecord | None = None
    ) -> int:
        remote, version = (
            self._remote_original(provider, provider_id) if expected is not None else (None, None)
        )
        with self.connect() as connection:
            if expected is not None:
                connection.execute("BEGIN IMMEDIATE")
                self._require_unchanged(connection, expected, remote=remote, version=version)
            changed = connection.execute(
                "DELETE FROM flights WHERE provider=? AND provider_id=?",
                (provider, provider_id),
            ).rowcount
            if self._cache_schema(connection) and (
                remote is not None
                or connection.execute(
                    "SELECT 1 FROM itinerary_cache_refs WHERE provider=? AND provider_id=?",
                    (provider, provider_id),
                ).fetchone()
            ):
                connection.execute(
                    "INSERT OR IGNORE INTO itinerary_cache_deleted VALUES (?,?)",
                    (provider, provider_id),
                )
            return changed or int(remote is not None)

    @staticmethod
    def _require_unchanged(connection, original, *, remote=None, version=None):
        row = connection.execute(
            "SELECT * FROM flights WHERE provider=? AND provider_id=?",
            (original.provider, original.provider_id),
        ).fetchone()
        current = dict(row) if row is not None else None
        if current is not None:
            current["codeshares"] = json.loads(current["codeshares"])
        elif remote is not None:
            state = connection.execute(
                "SELECT data FROM itinerary_catalog_state WHERE id=1"
            ).fetchone()
            deleted = connection.execute(
                "SELECT 1 FROM itinerary_cache_deleted WHERE provider=? AND provider_id=?",
                (original.provider, original.provider_id),
            ).fetchone()
            if state is not None and state[0] == version and not deleted:
                current = remote.model_dump()
        if current is None or FlightRecord.model_validate(current) != original:
            raise ValueError("Flight changed or was deleted; reload it before updating or deleting")

    def update(self, original: FlightRecord, replacement: FlightRecord) -> int:
        """Update an existing record only if it still matches the reviewed version."""
        replacement = FlightRecord.model_validate(replacement.model_dump())
        if (original.provider, original.provider_id) != (
            replacement.provider,
            replacement.provider_id,
        ):
            raise ValueError("provider and provider_id cannot change during an update")
        data = replacement.model_dump(mode="json")
        for field in (
            "departure_scheduled",
            "arrival_scheduled",
            "departure_actual",
            "arrival_actual",
            "fetched_at",
        ):
            value = getattr(replacement, field)
            data[field] = utc_text(value) if value else None
        data["codeshares"] = json.dumps(data["codeshares"])
        data["aircraft_type_code"] = replacement.aircraft_type_code
        remote, version = self._remote_original(original.provider, original.provider_id)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_unchanged(connection, original, remote=remote, version=version)
            assignments = ", ".join(
                f"{field}=:{field}" for field in data if field not in ("provider", "provider_id")
            )
            count = connection.execute(
                f"UPDATE flights SET {assignments} WHERE provider=:provider AND provider_id=:provider_id",
                data,
            ).rowcount
            if not count:
                connection.execute(
                    f"INSERT INTO flights ({','.join(data)}) VALUES ({','.join(':' + name for name in data)})",
                    data,
                )
            if self._cache_schema(connection):
                connection.execute(
                    "DELETE FROM itinerary_cache_deleted WHERE provider=? AND provider_id=?",
                    (original.provider, original.provider_id),
                )
            return 1
