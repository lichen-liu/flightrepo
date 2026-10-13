"""Read contract used by the shared itinerary algorithm."""

from datetime import datetime
from typing import Protocol

from core.models import FlightRecord
from core.routing.models import RouteSearchRequest
from core.routing.results import RouteSearchResult


class FlightQuery(Protocol):
    def search(
        self,
        start: datetime,
        end: datetime,
        origin: str | None = None,
        destination: str | None = None,
        include_cancelled: bool = False,
        limit: int = 500,
    ) -> list[FlightRecord]: ...


class RoutingQuery(Protocol):
    def search_itineraries(self, request: RouteSearchRequest) -> RouteSearchResult: ...
