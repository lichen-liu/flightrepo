import csv
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from core.models import FlightRecord, FlightStatus
from core.routing.models import utc_instant
from ingestion.providers.base import FlightProvider


def optional_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


class CsvProvider(FlightProvider):
    """Licensed bulk-feed adapter. Expected columns match FlightRecord field names."""

    name = "csv"

    def __init__(self, path: Path):
        self.path = path

    async def flights_for_airport(self, airport_iata: str, start: date, end: date) -> list[FlightRecord]:
        records = []
        airport_iata = airport_iata.strip().upper()
        with self.path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {'flight_number', 'origin_iata', 'destination_iata', 'departure_scheduled', 'arrival_scheduled'}
            missing = required-set(reader.fieldnames or [])
            if missing:
                raise ValueError('CSV is missing columns: '+', '.join(sorted(missing)))
            for row in reader:
                if airport_iata not in ((row['origin_iata'] or '').strip().upper(), (row['destination_iata'] or '').strip().upper()):
                    continue
                original_departure = datetime.fromisoformat(row["departure_scheduled"].replace("Z", "+00:00"))
                departure = utc_instant(original_departure)
                if not start <= departure.date() <= end:
                    continue
                identity = row.get("provider_id") or "|".join((row["flight_number"], row["origin_iata"], original_departure.isoformat()))
                records.append(FlightRecord(
                    provider=self.name, provider_id=hashlib.sha256(identity.encode()).hexdigest()[:32],
                    flight_number=row["flight_number"], airline_iata=row.get("airline_iata") or None,
                    origin_iata=row["origin_iata"], destination_iata=row["destination_iata"],
                    departure_scheduled=departure,
                    arrival_scheduled=datetime.fromisoformat(row["arrival_scheduled"].replace("Z", "+00:00")),
                    departure_actual=optional_timestamp(row.get('departure_actual')),
                    arrival_actual=optional_timestamp(row.get('arrival_actual')),
                    codeshares=json.loads(row['codeshares']) if row.get('codeshares') else [],
                    status=FlightStatus(row.get("status") or "scheduled"),
                    fetched_at=optional_timestamp(row.get('fetched_at')) or datetime.now(timezone.utc),
                ))
        return records

