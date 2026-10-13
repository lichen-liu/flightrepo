import logging
import sqlite3
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from core.config import get_settings
from core.connections import Airport, RebuildRequest, RebuiltPlan
from core.itineraries import build_itineraries
from core.models import FlightRecord, Itinerary, RouteSummary
from core.routing.models import (
    IncompleteSearchError,
    RouteSearchRequest,
    RoutingError,
    utc_instant,
)
from core.routing.results import RouteSearchResult
from server.reader import FlightReader
from server.web_models import (
    LocalRouteSearchRequest,
    LocalRouteSearchResult,
    ResolvedTime,
    ResolveTimeRequest,
    WebCatalog,
)

app = FastAPI(
    title="Flyji",
    version="0.1.0",
    description="Commercial flight facts and route exploration; no fares or inventory.",
)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)
WEB_ROOT = Path(__file__).parent / "web"
app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="web-assets")


@app.middleware("http")
async def browser_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path == "/" or request.url.path.startswith("/assets/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Cache-Control"] = "no-cache"
    elif request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
def webapp():
    return FileResponse(WEB_ROOT / "index.html")


@app.exception_handler(sqlite3.Error)
async def storage_error(request: Request, error: sqlite3.Error):
    logging.getLogger(__name__).warning("SQLite read unavailable: %s", type(error).__name__)
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "storage_unavailable",
                "message": "Flight data is unavailable. Initialize or check the local database, then retry.",
            }
        },
    )


@lru_cache
def get_store() -> FlightReader:
    return FlightReader(get_settings().database_path)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def readiness(store: FlightReader = Depends(get_store)):
    catalog = store.web_catalog()
    return {"status": "ready", "flight_count": catalog.dataset.flight_count}


@app.get("/v1/catalog", response_model=WebCatalog)
def catalog(store: FlightReader = Depends(get_store)) -> WebCatalog:
    return store.web_catalog()


@app.post("/v1/itineraries/search/local", response_model=LocalRouteSearchResult)
def local_search(request: LocalRouteSearchRequest, store: FlightReader = Depends(get_store)):
    try:
        return store.search_local(request)
    except ValueError as error:
        raise routing_http_error(error) from error


@app.post("/v1/time/resolve", response_model=ResolvedTime)
def resolve_time(request: ResolveTimeRequest, store: FlightReader = Depends(get_store)):
    try:
        return store.resolve_time(
            request.airport, request.local_time, request.fold, request.expected_timezone
        )
    except ValueError as error:
        raise routing_http_error(error) from error


def routing_http_error(error):
    if isinstance(error, IncompleteSearchError):
        return HTTPException(
            status_code=409,
            detail={
                "code": error.code,
                "message": str(error),
                "diagnostics": error.diagnostics.model_dump(mode="json"),
            },
        )
    if isinstance(error, RoutingError):
        return HTTPException(
            status_code=422,
            detail={
                "code": error.code,
                "message": str(error),
                **({"context": error.context} if error.context is not None else {}),
            },
        )
    return HTTPException(status_code=422, detail=str(error))


def query_times(start, end):
    try:
        start, end = utc_instant(start), utc_instant(end)
        if end <= start:
            raise ValueError("end must be after start")
        return start, end
    except ValueError as error:
        raise routing_http_error(error) from error


@app.post("/v1/itineraries/search", response_model=RouteSearchResult)
def itinerary_search(
    request: RouteSearchRequest, store: FlightReader = Depends(get_store)
) -> RouteSearchResult:
    """Rank schedule-feasible routes with explicit scope, limits and optional trace."""
    try:
        return store.search_itineraries(request)
    except ValueError as error:
        raise routing_http_error(error) from error


@app.post("/v1/plans/rebuild", response_model=RebuiltPlan)
def rebuild(request: RebuildRequest, store: FlightReader = Depends(get_store)) -> RebuiltPlan:
    """Calculate onward options and a new suffix. Does not save or mutate a plan."""
    try:
        return store.rebuild(request)
    except ValueError as error:
        raise routing_http_error(error) from error


@app.get("/v1/airports", response_model=list[Airport])
def airports(
    city_id: str | None = None,
    q: Annotated[str, Query(max_length=80)] = "",
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    store: FlightReader = Depends(get_store),
) -> list[Airport]:
    return store.airports(city_id, q, limit)


@app.get("/v1/flights", response_model=list[FlightRecord])
def flights(
    start: datetime,
    end: datetime,
    origin: str | None = None,
    destination: str | None = None,
    include_cancelled: bool = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    store: FlightReader = Depends(get_store),
) -> list[FlightRecord]:
    start, end = query_times(start, end)
    return store.search(start, end, origin, destination, include_cancelled, limit)


@app.get("/v1/routes", response_model=list[RouteSummary])
def routes(
    start: datetime,
    end: datetime,
    origin: str | None = None,
    destination: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    store: FlightReader = Depends(get_store),
) -> list[RouteSummary]:
    start, end = query_times(start, end)
    return store.routes(start, end, origin, destination, limit)


@app.get(
    "/v1/itineraries",
    response_model=list[Itinerary],
    responses={
        409: {
            "description": "Search limits prevented proving the list ranking; detail contains incomplete_search diagnostics."
        }
    },
)
def itineraries(
    origin: str,
    destination: str,
    start: datetime,
    end: datetime,
    max_legs: Annotated[int, Query(ge=1, le=3)] = 2,
    min_connection_minutes: Annotated[int, Query(ge=20, le=360)] = 45,
    max_connection_minutes: Annotated[int, Query(ge=60, le=1440)] = 360,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    horizon_end: datetime | None = None,
    store: FlightReader = Depends(get_store),
) -> list[Itinerary]:
    start, end = query_times(start, end)
    try:
        return build_itineraries(
            store,
            origin,
            destination,
            start,
            end,
            max_legs,
            min_connection_minutes,
            max_connection_minutes,
            limit,
            horizon_end=horizon_end,
        )
    except ValueError as error:
        raise routing_http_error(error) from error
