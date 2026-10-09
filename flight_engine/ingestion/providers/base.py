from abc import ABC, abstractmethod
from datetime import date

from flight_engine.core.models import FlightRecord


class FlightProvider(ABC):
    name: str

    @abstractmethod
    async def flights_for_airport(self, airport_iata: str, start: date, end: date) -> list[FlightRecord]:
        """Return commercial arrivals and departures in the provider's supported window."""

