"""Internal database initialization and mutation; not imported by the serving API."""

import json
from collections.abc import Iterable

from core.connections import Airport, AirportTransfer
from core.models import AircraftObservation, FlightRecord
from core.storage import SCHEMA, SQLiteConnection, utc_text


class FlightUpdater(SQLiteConnection):
    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                raise RuntimeError("SQLite WAL mode could not be enabled")
            connection.executescript(SCHEMA)
            # Older data files are upgraded only by the ingestion/admin writer.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(flights)")}
            if "aircraft" not in columns:
                connection.execute("ALTER TABLE flights ADD COLUMN aircraft TEXT")

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
            data["aircraft"] = record.aircraft.model_dump_json() if record.aircraft else None
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
        return len(rows)

    def reset(self) -> None:
        self.initialize()
        # Transactional deletion keeps existing connections valid; never unlink a live DB.
        with self.connect() as connection:
            connection.execute("DELETE FROM flights")
            connection.execute("DELETE FROM airport_transfers")
            connection.execute("DELETE FROM airports")
            connection.execute("DELETE FROM aircraft_observations")
            connection.execute("DELETE FROM observation_imports")
            connection.execute("DELETE FROM observation_archives")

    def save_observation_day(self, day, observations, *, filter_sha256, stats, assets=()):
        """Replace a complete filtered day atomically; allow bounded catalog builds."""
        rows = [AircraftObservation.model_validate(o.model_dump()) for o in observations]
        if any(row.day != day for row in rows):
            raise ValueError("Observation day mismatch")
        with self.connect() as connection:
            # Leave ample space for raw days within the shared 20 GiB allowance.
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={6 * 2**30 // page_size}")
            connection.execute("DELETE FROM aircraft_observations WHERE day=?", (day.isoformat(),))
            connection.executemany(
                "INSERT INTO aircraft_observations VALUES (?, ?, ?, ?)",
                [
                    (
                        o.day.isoformat(),
                        o.aircraft.icao_hex,
                        o.aircraft.type_code,
                        o.model_dump_json(),
                    )
                    for o in rows
                ],
            )
            connection.execute(
                "INSERT INTO observation_imports VALUES (?, ?, ?) ON CONFLICT(day) DO UPDATE SET filter_sha256=excluded.filter_sha256, stats=excluded.stats",
                (day.isoformat(), filter_sha256, json.dumps(stats)),
            )
            connection.execute("DELETE FROM observation_archives WHERE day=?", (day.isoformat(),))
            connection.executemany(
                "INSERT INTO observation_archives VALUES (?, ?, ?, ?)",
                [
                    (day.isoformat(), index, asset["url"], asset["bytes"])
                    for index, asset in enumerate(assets)
                ],
            )

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
        with self.connect() as connection:
            if expected is not None:
                connection.execute("BEGIN IMMEDIATE")
                self._require_unchanged(connection, expected)
            return connection.execute(
                "DELETE FROM flights WHERE provider=? AND provider_id=?",
                (provider, provider_id),
            ).rowcount

    @staticmethod
    def _require_unchanged(connection, original):
        row = connection.execute(
            "SELECT * FROM flights WHERE provider=? AND provider_id=?",
            (original.provider, original.provider_id),
        ).fetchone()
        current = dict(row) if row is not None else None
        if current is not None:
            current["codeshares"] = json.loads(current["codeshares"])
            if current.get("aircraft") is not None:
                current["aircraft"] = json.loads(current["aircraft"])
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
        data["aircraft"] = replacement.aircraft.model_dump_json() if replacement.aircraft else None
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_unchanged(connection, original)
            assignments = ", ".join(
                f"{field}=:{field}" for field in data if field not in ("provider", "provider_id")
            )
            return connection.execute(
                f"UPDATE flights SET {assignments} WHERE provider=:provider AND provider_id=:provider_id",
                data,
            ).rowcount
