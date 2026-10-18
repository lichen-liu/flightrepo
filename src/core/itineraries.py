"""Legacy list adapter. Partial searches must not masquerade as ranked results."""

from datetime import datetime

from core.contracts import RoutingQuery
from core.models import Itinerary
from core.routing.models import IncompleteSearchError, RouteSearchRequest


def build_itineraries(
    store: RoutingQuery,
    origin: str,
    destination: str,
    start: datetime,
    end: datetime,
    max_legs: int | None = None,
    min_connection_minutes: int = 45,
    max_connection_minutes: int | None = None,
    limit: int = 20,
    *,
    horizon_end: datetime | None = None,
    offset: int = 0,
) -> list[Itinerary]:
    request = RouteSearchRequest(
        origin=origin,
        destination=destination,
        departure_start=start,
        departure_end=end,
        horizon_end=horizon_end,
        max_legs=max_legs,
        minimum_connection_minutes=min_connection_minutes,
        maximum_connection_minutes=max_connection_minutes,
        limit=limit,
        offset=offset,
    )
    result = store.search_itineraries(request)
    if not result.diagnostics.exhaustive:
        raise IncompleteSearchError(result.diagnostics)
    return [Itinerary.model_validate(item.model_dump()) for item in result.items]
