from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from flight_engine.config import Settings, get_settings
from flight_engine.models import FlightRecord, IngestRequest, IngestResult, Itinerary, RouteSummary
from flight_engine.providers import AeroDataBoxProvider
from flight_engine.services import IngestionService, build_itineraries
from flight_engine.storage import FlightStore

app = FastAPI(title="Flight Routes Engine", version="0.1.0",
              description="Commercial flight facts and route exploration; no fares or inventory.")


@lru_cache
def get_store() -> FlightStore:
    store = FlightStore(get_settings().database_path)
    store.initialize()
    return store


def require_admin(x_admin_token: Annotated[str | None, Header()] = None,
                  settings: Settings = Depends(get_settings)) -> None:
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="invalid admin token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/admin/ingestions", response_model=IngestResult, dependencies=[Depends(require_admin)])
async def ingest(request: IngestRequest, settings: Settings = Depends(get_settings),
                 store: FlightStore = Depends(get_store)) -> IngestResult:
    try:
        provider = AeroDataBoxProvider(settings.provider_api_key, settings.provider_base_url,
                                       settings.provider_host)
        try:
            return await IngestionService(store, provider, settings.max_query_days).ingest(
                request.airports, request.start, request.end)
        finally:
            await provider.aclose()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/v1/flights", response_model=list[FlightRecord])
def flights(start: datetime, end: datetime, origin: str | None = None,
            destination: str | None = None, include_cancelled: bool = False,
            limit: Annotated[int, Query(ge=1, le=1000)] = 500,
            store: FlightStore = Depends(get_store)) -> list[FlightRecord]:
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    return store.search(start, end, origin, destination, include_cancelled, limit)


@app.get("/v1/routes", response_model=list[RouteSummary])
def routes(start: datetime, end: datetime, origin: str | None = None,
           destination: str | None = None, limit: Annotated[int, Query(ge=1, le=1000)] = 500,
           store: FlightStore = Depends(get_store)) -> list[RouteSummary]:
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    return store.routes(start, end, origin, destination, limit)


@app.get("/v1/itineraries", response_model=list[Itinerary])
def itineraries(origin: str, destination: str, start: datetime, end: datetime,
                max_legs: Annotated[int, Query(ge=1, le=3)] = 2,
                min_connection_minutes: Annotated[int, Query(ge=20, le=360)] = 45,
                max_connection_minutes: Annotated[int, Query(ge=60, le=1440)] = 360,
                limit: Annotated[int, Query(ge=1, le=100)] = 20,
                store: FlightStore = Depends(get_store)) -> list[Itinerary]:
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    if origin.upper() == destination.upper():
        raise HTTPException(status_code=422, detail="origin and destination must differ")
    return build_itineraries(store, origin, destination, start, end, max_legs,
                             min_connection_minutes, max_connection_minutes, limit)
