"""Read contract used by the shared itinerary algorithm."""

from typing import Protocol

from core.routing.models import RouteSearchRequest
from core.routing.results import RouteSearchResult


class RoutingQuery(Protocol):
    def search_itineraries(self, request: RouteSearchRequest) -> RouteSearchResult: ...
