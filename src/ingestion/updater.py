"""Internal database initialization and mutation; not imported by the serving API."""

import json
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime

from core.airport_timezones import protect
from core.connections import Airport, AirportTransfer
from core.database_roles import require_serving
from core.models import FlightRecord
from core.storage import (
    SCHEMA,
    SQLiteConnection,
    source_id,
    store_flight,
    upgrade_route_verification,
    utc_us,
)


class FlightUpdater(SQLiteConnection):
    @contextmanager
    def connect(self):
        with super().connect() as connection:
            require_serving(connection)
            yield connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                raise RuntimeError("SQLite WAL mode could not be enabled")
            legacy = connection.execute(
                "SELECT type FROM sqlite_master WHERE name='flights'"
            ).fetchone()
            if legacy and legacy[0] == "table":
                raise ValueError(
                    "Legacy database schema; initialize a new file for the shared flight-leg contract"
                )
            connection.executescript(SCHEMA)
            upgrade_route_verification(connection)

    def upsert(self, records: Iterable[FlightRecord], *, restore=True) -> int:
        records = [FlightRecord.model_validate(record.model_dump()) for record in records]
        if not records:
            return 0
        with self.connect() as connection:
            applied = 0
            for record in records:
                applied += store_flight(connection, record, restore=restore) != "protected"
        return applied

    def reset(self) -> None:
        self.initialize()
        # Transactional deletion keeps existing connections valid; never unlink a live DB.
        with self.connect() as connection:
            connection.execute("DELETE FROM flight_legs")
            connection.execute("DELETE FROM flight_tombstones")
            connection.execute("DELETE FROM ingestion_batches")
            connection.execute("DELETE FROM airport_transfers")
            connection.execute("DELETE FROM airport_timezone_state")
            connection.execute("DELETE FROM airport_timezone_snapshots")
            connection.execute("DELETE FROM airports")
            connection.execute("DELETE FROM carriers")
            connection.execute("DELETE FROM reference_snapshots")
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
            connection.execute("DELETE FROM data_sources")

    def create_airport(self, airport: Airport) -> None:
        """Insert one validated airport without replacing existing metadata."""
        airport = Airport.model_validate(
            {key: getattr(airport, key) for key in airport.model_fields_set}
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO airports (iata, name, city_id, timezone, latitude, longitude)
                VALUES (:iata, :name, :city_id, :timezone, :latitude, :longitude)
                ON CONFLICT(iata) DO UPDATE SET name=excluded.name,city_id=excluded.city_id,
                timezone=excluded.timezone,latitude=excluded.latitude,longitude=excluded.longitude,
                metadata_source_id=NULL
                WHERE airports.name IS NULL OR airports.city_id IS NULL OR airports.timezone IS NULL
                OR airports.latitude IS NULL OR airports.longitude IS NULL""",
                airport.model_dump(),
            )
            self._update_airport_reference(connection, airport)
            if "timezone" in airport.model_fields_set:
                protect(connection, airport.iata)
            if cursor.rowcount == 0:
                raise ValueError(
                    f"Airport {airport.iata} already exists; use airports import to update it"
                )

    @staticmethod
    def _update_airport_reference(connection, airport):
        # Legacy admin imports do not erase new fields they never supplied.
        fields = [
            key
            for key in ("icao", "municipality", "country_code", "region_code")
            if key in airport.model_fields_set
        ]
        if fields:
            connection.execute(
                "UPDATE airports SET " + ",".join(key + "=?" for key in fields) + " WHERE iata=?",
                [getattr(airport, key) for key in fields] + [airport.iata],
            )

    def update_airports(
        self, airports: Iterable[Airport], transfers: Iterable[AirportTransfer]
    ) -> None:
        """Validate a complete metadata batch, then write it atomically."""
        airports = [
            Airport.model_validate({key: getattr(a, key) for key in a.model_fields_set})
            for a in airports
        ]
        transfers = [AirportTransfer.model_validate(t.model_dump()) for t in transfers]
        with self.connect() as connection:
            for airport in airports:
                fields = ("name", "city_id", "timezone", "latitude", "longitude")
                supplied = [key for key in fields if key in airport.model_fields_set]
                connection.execute(
                    "INSERT INTO airports(iata," + ",".join(fields) + ") VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(iata) DO "
                    + (
                        "UPDATE SET " + ",".join(f"{key}=excluded.{key}" for key in supplied)
                        if supplied
                        else "NOTHING"
                    ),
                    [airport.iata] + [getattr(airport, key) for key in fields],
                )
                self._update_airport_reference(connection, airport)
                if "timezone" in airport.model_fields_set:
                    protect(connection, airport.iata)
                elif {"latitude", "longitude"} & airport.model_fields_set:
                    # A generated zone is tied to the exact coordinates resolved.
                    previous = connection.execute(
                        "SELECT * FROM airport_timezone_state WHERE iata=? AND owner='reference'",
                        (airport.iata,),
                    ).fetchone()
                    current = connection.execute(
                        "SELECT timezone,latitude,longitude FROM airports WHERE iata=?",
                        (airport.iata,),
                    ).fetchone()
                    if previous and (previous["latitude"], previous["longitude"]) != (
                        current["latitude"],
                        current["longitude"],
                    ):
                        if current["timezone"] == previous["applied_zone"]:
                            connection.execute(
                                "UPDATE airports SET timezone=NULL WHERE iata=?", (airport.iata,)
                            )
                            connection.execute(
                                "DELETE FROM airport_timezone_state WHERE iata=?", (airport.iata,)
                            )
            invalid = connection.execute("""SELECT 1 FROM airport_transfers t
                JOIN airports a ON a.iata=t.arrival_iata JOIN airports b ON b.iata=t.departure_iata
                WHERE a.city_id IS NULL OR b.city_id IS NULL OR a.city_id != b.city_id LIMIT 1""").fetchone()
            if invalid:
                raise ValueError("city update would invalidate an existing transfer")
            for transfer in transfers:
                rows = connection.execute(
                    "SELECT city_id FROM airports WHERE iata IN (?, ?)",
                    (transfer.arrival_iata, transfer.departure_iata),
                ).fetchall()
                if (
                    len(rows) != 2
                    or rows[0]["city_id"] is None
                    or rows[0]["city_id"] != rows[1]["city_id"]
                ):
                    raise ValueError("transfer airports must exist and share a city_id")
            connection.executemany(
                """INSERT INTO airport_transfers VALUES
                (:arrival_iata, :departure_iata, :minimum_minutes, :source)
                ON CONFLICT(arrival_iata, departure_iata) DO UPDATE SET
                minimum_minutes=excluded.minimum_minutes, source=excluded.source""",
                [t.model_dump() for t in transfers],
            )

    def release_airport_timezone(self, code):
        """Explicitly allow the next producer publication to replace this override."""
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM airports WHERE iata=?", (code,)).fetchone():
                raise ValueError(f"Unknown airport: {code}")
            connection.execute("UPDATE airports SET timezone=NULL WHERE iata=?", (code,))
            connection.execute("DELETE FROM airport_timezone_state WHERE iata=?", (code,))

    def delete(
        self, provider: str, provider_id: str, *, expected: FlightRecord | None = None
    ) -> int:
        with self.connect() as connection:
            if expected is not None:
                connection.execute("BEGIN IMMEDIATE")
                self._require_unchanged(connection, expected)
            sid = source_id(connection, provider)
            changed = connection.execute(
                "SELECT COUNT(*) FROM flight_legs WHERE source_id=? AND source_record_key=?",
                (sid, provider_id),
            ).fetchone()[0]
            if changed:
                connection.execute(
                    "INSERT INTO flight_tombstones VALUES (?,?,?) ON CONFLICT(source_id,source_record_key) DO UPDATE SET deleted_us=excluded.deleted_us",
                    (sid, provider_id, utc_us(datetime.now(UTC))),
                )
            connection.execute(
                "DELETE FROM flight_legs WHERE source_id=? AND source_record_key=?",
                (sid, provider_id),
            )
            return changed

    @staticmethod
    def _require_unchanged(connection, original):
        row = connection.execute(
            "SELECT * FROM flights WHERE provider=? AND provider_id=?",
            (original.provider, original.provider_id),
        ).fetchone()
        current = dict(row) if row is not None else None
        if current is not None:
            current["codeshares"] = json.loads(current["codeshares"])
            current["review_reasons"] = json.loads(current.get("review_reasons", "[]"))
            if current.get("route_verification") is not None:
                current["route_verification"] = json.loads(current["route_verification"])
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
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_unchanged(connection, original)
            store_flight(connection, replacement, local_edit=True)
            return 1
