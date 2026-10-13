from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query

from core.config import get_settings
from core.models import FlightRecord, Itinerary, RouteSummary
from core.itineraries import build_itineraries
from server.reader import FlightReader
from core.connections import Airport, RebuildRequest, RebuiltPlan
from core.routing.models import IncompleteSearchError, RouteSearchRequest, RoutingError, utc_instant
from core.routing.results import RouteSearchResult

app = FastAPI(title="Flyji", version="0.1.0",
              description="Commercial flight facts and route exploration; no fares or inventory.")


@lru_cache
def get_store() -> FlightReader:
    store = FlightReader(get_settings().database_path)
    return store



@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def routing_http_error(error):
    if isinstance(error, IncompleteSearchError):
        return HTTPException(status_code=409, detail={'code': error.code, 'message': str(error),
                                                     'diagnostics': error.diagnostics.model_dump(mode='json')})
    if isinstance(error, RoutingError):
        return HTTPException(status_code=422, detail={'code': error.code, 'message': str(error),
                                                     **({'context': error.context} if error.context is not None else {})})
    return HTTPException(status_code=422, detail=str(error))


def query_times(start, end):
    try:
        start, end = utc_instant(start), utc_instant(end)
        if end <= start:
            raise ValueError('end must be after start')
        return start, end
    except ValueError as error:
        raise routing_http_error(error) from error


@app.post('/v1/itineraries/search', response_model=RouteSearchResult)
def itinerary_search(request: RouteSearchRequest, store: FlightReader = Depends(get_store)) -> RouteSearchResult:
    """Rank schedule-feasible routes with explicit scope, limits and optional trace."""
    try:
        return store.search_itineraries(request)
    except ValueError as error:
        raise routing_http_error(error) from error


@app.post('/v1/plans/rebuild', response_model=RebuiltPlan)
def rebuild(request: RebuildRequest, store: FlightReader = Depends(get_store)) -> RebuiltPlan:
    """Calculate onward options and a new suffix. Does not save or mutate a plan."""
    try:
        return store.rebuild(request)
    except ValueError as error:
        raise routing_http_error(error) from error


@app.get('/v1/airports', response_model=list[Airport])
def airports(city_id: str | None = None, q: Annotated[str, Query(max_length=80)] = '',
             limit: Annotated[int, Query(ge=1, le=1000)] = 500,
             store: FlightReader = Depends(get_store)) -> list[Airport]:
    return store.airports(city_id, q, limit)


@app.get("/v1/flights", response_model=list[FlightRecord])
def flights(start: datetime, end: datetime, origin: str | None = None,
            destination: str | None = None, include_cancelled: bool = False,
            limit: Annotated[int, Query(ge=1, le=1000)] = 500,
            store: FlightReader = Depends(get_store)) -> list[FlightRecord]:
    start, end = query_times(start, end)
    return store.search(start, end, origin, destination, include_cancelled, limit)


@app.get("/v1/routes", response_model=list[RouteSummary])
def routes(start: datetime, end: datetime, origin: str | None = None,
           destination: str | None = None, limit: Annotated[int, Query(ge=1, le=1000)] = 500,
           store: FlightReader = Depends(get_store)) -> list[RouteSummary]:
    start, end = query_times(start, end)
    return store.routes(start, end, origin, destination, limit)


@app.get("/v1/itineraries", response_model=list[Itinerary], responses={
    409: {'description': 'Search limits prevented proving the list ranking; detail contains incomplete_search diagnostics.'}
})
def itineraries(origin: str, destination: str, start: datetime, end: datetime,
                max_legs: Annotated[int, Query(ge=1, le=3)] = 2,
                min_connection_minutes: Annotated[int, Query(ge=20, le=360)] = 45,
                max_connection_minutes: Annotated[int, Query(ge=60, le=1440)] = 360,
                limit: Annotated[int, Query(ge=1, le=100)] = 20,
                horizon_end: datetime | None = None,
                store: FlightReader = Depends(get_store)) -> list[Itinerary]:
    start, end = query_times(start, end)
    try:
        return build_itineraries(store, origin, destination, start, end, max_legs,
                                 min_connection_minutes, max_connection_minutes, limit,
                                 horizon_end=horizon_end)
    except ValueError as error:
        raise routing_http_error(error) from error
