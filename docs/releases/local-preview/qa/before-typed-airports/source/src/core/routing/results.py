from pydantic import BaseModel, Field

from core.connections import Airport, PlanConnection
from core.models import Itinerary
from core.routing.models import RouteSearchRequest, SearchDiagnostics


class RouteItinerary(Itinerary):
    itinerary_id: str
    connections: list[PlanConnection]


class RouteSearchResult(BaseModel):
    query: RouteSearchRequest
    items: list[RouteItinerary]
    diagnostics: SearchDiagnostics
    airports: list[Airport] = Field(default_factory=list)
