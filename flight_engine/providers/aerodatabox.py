import hashlib
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx

from flight_engine.models import FlightRecord, FlightStatus
from flight_engine.providers.base import FlightProvider


class AeroDataBoxProvider(FlightProvider):
    """AeroDataBox RapidAPI adapter using airport FIDS/schedule windows."""

    name = "aerodatabox"
    _status = {
        "scheduled": FlightStatus.SCHEDULED, "active": FlightStatus.ACTIVE,
        "departed": FlightStatus.ACTIVE, "arrived": FlightStatus.LANDED,
        "landed": FlightStatus.LANDED, "cancelled": FlightStatus.CANCELLED,
    }

    def __init__(self, api_key: str, base_url: str, host: str, client: httpx.AsyncClient | None = None):
        if not api_key:
            raise ValueError("FLIGHT_PROVIDER_API_KEY is required for aerodatabox ingestion")
        self.client = client or httpx.AsyncClient(timeout=30)
        self._owns_client = client is None
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": host}

    async def flights_for_airport(self, airport_iata: str, start: date, end: date) -> list[FlightRecord]:
        results: dict[str, FlightRecord] = {}
        cursor = datetime.combine(start, time.min)
        finish = datetime.combine(end + timedelta(days=1), time.min)
        # 12-hour windows work across all current paid tiers.
        while cursor < finish:
            window_end = min(cursor + timedelta(hours=12), finish)
            payload = await self._fetch_window(airport_iata, cursor, window_end)
            fetched_at = datetime.now(timezone.utc)
            for direction in ("departures", "arrivals"):
                for item in payload.get(direction, []):
                    record = self._normalize(item, fetched_at)
                    if record:
                        results[record.provider_id] = record
            cursor = window_end
        return list(results.values())

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _fetch_window(self, airport: str, start: datetime, end: datetime) -> dict[str, Any]:
        url = f"{self.base_url}/flights/airports/iata/{airport}/{start:%Y-%m-%dT%H:%M}/{end:%Y-%m-%dT%H:%M}"
        response = await self.client.get(url, headers=self.headers, params={
            "withLeg": "true", "withCancelled": "true", "withCodeshared": "true",
            "withCargo": "false", "withPrivate": "false",
        })
        response.raise_for_status()
        return response.json()

    def _normalize(self, item: dict[str, Any], fetched_at: datetime) -> FlightRecord | None:
        departure, arrival = item.get("departure", {}), item.get("arrival", {})
        origin = departure.get("airport", {}).get("iata")
        destination = arrival.get("airport", {}).get("iata")
        scheduled_out = self._time(departure, "scheduledTime")
        scheduled_in = self._time(arrival, "scheduledTime")
        number = item.get("number")
        if not all((origin, destination, scheduled_out, scheduled_in, number)):
            return None
        identity = item.get("movementId") or "|".join((number, origin, destination, scheduled_out.isoformat()))
        provider_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
        return FlightRecord(
            provider=self.name, provider_id=provider_id, flight_number=number.replace(" ", "").upper(),
            airline_iata=item.get("airline", {}).get("iata"), origin_iata=origin,
            destination_iata=destination, departure_scheduled=scheduled_out,
            arrival_scheduled=scheduled_in, departure_actual=self._time(departure, "actualTime"),
            arrival_actual=self._time(arrival, "actualTime"),
            status=self._status.get(str(item.get("status", "")).lower(), FlightStatus.UNKNOWN),
            codeshares=[x.get("number") for x in item.get("codeshareStatus", {}).get("all", []) if x.get("number")],
            fetched_at=fetched_at,
        )

    @staticmethod
    def _time(section: dict[str, Any], key: str) -> datetime | None:
        value = section.get(key, {}).get("utc") or section.get(key, {}).get("local")
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
