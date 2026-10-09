import csv
import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

from flight_engine.models import FlightRecord, FlightStatus
from flight_engine.providers.base import FlightProvider


class CsvProvider(FlightProvider):
    """Licensed bulk-feed adapter. Expected columns match FlightRecord field names."""

    name = "csv"

    def __init__(self, path: Path):
        self.path = path

    async def flights_for_airport(self, airport_iata: str, start: date, end: date) -> list[FlightRecord]:
        records = []
        with self.path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                departure = datetime.fromisoformat(row["departure_scheduled"].replace("Z", "+00:00"))
                if not start <= departure.date() <= end:
                    continue
                if airport_iata not in (row["origin_iata"].upper(), row["destination_iata"].upper()):
                    continue
                identity = row.get("provider_id") or "|".join((row["flight_number"], row["origin_iata"], departure.isoformat()))
                records.append(FlightRecord(
                    provider=self.name, provider_id=hashlib.sha256(identity.encode()).hexdigest()[:32],
                    flight_number=row["flight_number"], airline_iata=row.get("airline_iata") or None,
                    origin_iata=row["origin_iata"], destination_iata=row["destination_iata"],
                    departure_scheduled=departure,
                    arrival_scheduled=datetime.fromisoformat(row["arrival_scheduled"].replace("Z", "+00:00")),
                    status=FlightStatus(row.get("status") or "scheduled"), fetched_at=datetime.now(timezone.utc),
                ))
        return records

