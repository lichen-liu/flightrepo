import logging
import sqlite3
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from core.config import get_settings
from core.connections import Airport
from core.itineraries import build_itineraries
from core.models import RouteSummary
from core.routing.models import (
    IncompleteSearchError,
    RouteSearchRequest,
    RoutingError,
    TimeBasis,
    utc_instant,
)
from server.public import (
    PublicCatalog,
    PublicFlight,
    PublicItinerary,
    PublicLocalSearchResult,
    PublicPlan,
    PublicRebuildRequest,
    PublicSearchResult,
    diagnostics,
    resolve_request,
    to_public,
)
from server.reader import FlightReader
from server.web_assets import WebAssets
from server.web_models import (
    AirportCoverage,
    AvailableDates,
    LocalRouteSearchRequest,
    ResolvedTime,
    ResolveTimeRequest,
    SearchAvailability,
    WebBootstrap,
)

app = FastAPI(
    title="Flyji",
    version="0.1.0",
    description="Commercial flight facts and route exploration; no fares or inventory.",
)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)
WEB_ROOT = Path(__file__).parent / "web"
web_assets = WebAssets(WEB_ROOT)
app.mount("/assets", web_assets, name="web-assets")


@app.middleware("http")
async def browser_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path in {"/", "/data-sources"} or request.url.path.startswith("/assets/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        if request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers.setdefault("Cache-Control", "no-cache")
    elif request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Link"] = '</data-sources>; rel="license"'
    return response


@app.get("/", include_in_schema=False)
def webapp():
    html, revision = web_assets.page()
    return HTMLResponse(html, headers={"X-Flyji-Assets": revision})


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
    settings = get_settings()
    return FlightReader(settings.database_path, serving_only=True)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def readiness(store: FlightReader = Depends(get_store)):
    return store.readiness()


@app.get("/v1/catalog", response_model=PublicCatalog)
def catalog(include_airports: bool = True, store: FlightReader = Depends(get_store)):
    return to_public(store, store.web_catalog(include_airports=include_airports))


@app.get("/v1/bootstrap", response_model=WebBootstrap)
def bootstrap(store: FlightReader = Depends(get_store)):
    return store.web_bootstrap()


@app.get("/v1/search-availability", response_model=SearchAvailability)
def search_availability(
    origin: str,
    destination: str,
    clock: Literal["local", "UTC"] = "local",
    time_basis: TimeBasis | None = None,
    departure_date: date | None = None,
    departure_last_date: date | None = None,
    store: FlightReader = Depends(get_store),
):
    try:
        return store.search_availability(
            origin, destination, clock, time_basis, departure_date, departure_last_date
        )
    except ValueError as error:
        raise routing_http_error(error) from error


@app.get("/v1/dates", response_model=AvailableDates)
def available_dates(
    origin: str,
    clock: Literal["local", "UTC"] = "local",
    time_basis: TimeBasis | None = None,
    destination: str | None = None,
    include_nearby: bool = False,
    store: FlightReader = Depends(get_store),
):
    try:
        return store.available_dates(
            origin, clock, time_basis, destination=destination, include_nearby=include_nearby
        )
    except ValueError as error:
        raise routing_http_error(error) from error


@app.get("/v1/airport-coverage", response_model=AirportCoverage)
def airport_coverage(
    airport: Annotated[list[str], Query(min_length=1, max_length=2)],
    time_basis: TimeBasis | None = None,
    store: FlightReader = Depends(get_store),
):
    try:
        return store.airport_coverage(airport, time_basis)
    except ValueError as error:
        raise routing_http_error(error) from error


@app.post("/v1/itineraries/search/local", response_model=PublicLocalSearchResult)
def local_search(request: LocalRouteSearchRequest, store: FlightReader = Depends(get_store)):
    try:
        return to_public(store, store.search_local(request.model_copy(update={"trace": False})))
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
                "diagnostics": diagnostics(error.diagnostics).model_dump(mode="json"),
            },
        )
    if isinstance(error, RoutingError):
        return HTTPException(
            status_code=422,
            detail={
                "code": error.code,
                "message": str(error),
                **(
                    {
                        "context": {
                            k: v
                            for k, v in error.context.items()
                            if k
                            in {
                                "field",
                                "timezone",
                                "local_time",
                                "utc_choices",
                                "airport",
                                "expected_timezone",
                                "actual_timezone",
                                "departure_utc",
                                "wait_minutes",
                                "minimum_minutes",
                                "next_airport",
                            }
                        }
                    }
                    if error.context is not None
                    else {}
                ),
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


@app.post("/v1/itineraries/search", response_model=PublicSearchResult)
def itinerary_search(
    request: RouteSearchRequest, store: FlightReader = Depends(get_store)
) -> PublicSearchResult:
    """Rank schedule-feasible routes with explicit scope, limits and optional trace."""
    try:
        return to_public(
            store, store.search_itineraries(request.model_copy(update={"trace": False}))
        )
    except ValueError as error:
        raise routing_http_error(error) from error


@app.post("/v1/plans/rebuild", response_model=PublicPlan)
def rebuild(request: PublicRebuildRequest, store: FlightReader = Depends(get_store)) -> PublicPlan:
    """Calculate onward options and a new suffix. Does not save or mutate a plan."""
    try:
        return to_public(store, store.rebuild(resolve_request(store, request)))
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


@app.get("/v1/flights", response_model=list[PublicFlight])
def flights(
    start: datetime,
    end: datetime,
    origin: str | None = None,
    destination: str | None = None,
    include_cancelled: bool = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    store: FlightReader = Depends(get_store),
) -> list[PublicFlight]:
    start, end = query_times(start, end)
    return to_public(store, store.search(start, end, origin, destination, include_cancelled, limit))


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
    response_model=list[PublicItinerary],
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
    max_legs: Annotated[int | None, Query(ge=1)] = None,
    min_connection_minutes: Annotated[int, Query(ge=20, le=360)] = 45,
    max_connection_minutes: Annotated[int, Query(ge=60, le=1440)] = 360,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    horizon_end: datetime | None = None,
    store: FlightReader = Depends(get_store),
) -> list[PublicItinerary]:
    start, end = query_times(start, end)
    try:
        return to_public(
            store,
            build_itineraries(
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
            ),
        )
    except ValueError as error:
        raise routing_http_error(error) from error


@app.get("/data-sources", response_class=HTMLResponse, include_in_schema=False)
def data_sources(store: FlightReader = Depends(get_store)):
    from html import escape
    from urllib.parse import urlsplit

    def link(url, text):
        parts = urlsplit(url or "")
        if parts.scheme not in {"https", "http"} or not parts.hostname:
            return escape(text or "")
        return f'<a href="{escape(url, quote=True)}" rel="noopener noreferrer">{escape(text or "")}</a>'

    with store.connect() as c:
        rows = c.execute(
            "SELECT name,source_url,license_expression,license_url,attribution FROM data_sources ORDER BY code"
        ).fetchall()
    entries = []
    for name, url, licence, licence_url, attribution in rows:
        parts = urlsplit(url or "")
        homepage = f"https://{parts.hostname}/" if parts.hostname else ""
        entries.append(
            f"<li><p>{link(homepage, name)} — {link(licence_url, licence or 'Terms supplied by the data owner')}</p><p>{escape(attribution or '')}</p></li>"
        )
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Data sources &amp; licences · Flyji</title><link rel="stylesheet" href="/assets/app.css"></head><body><main><h1>Data sources &amp; licences</h1><p>Contains information derived from the databases below. Their licences apply to the data; the application has its own licence.</p><ul>'
        + "".join(entries)
        + '</ul><p>Historical itineraries and time ranges are best-effort reconstructions. Data may be incomplete or incorrect; route references do not verify historical operation or exact timing.</p><p><a href="/">Back to flight search</a></p></main></body></html>'
    )
