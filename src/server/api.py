from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query

from core.config import get_settings
from core.models import FlightRecord, Itinerary, RouteSummary
from core.itineraries import build_itineraries
from server.reader import FlightReader

app = FastAPI(title="Flyji", version="0.1.0",
              description="Commercial flight facts and route exploration; no fares or inventory.")


@lru_cache
def get_store() -> FlightReader:
    store = FlightReader(get_settings().database_path)
    return store



@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/flights", response_model=list[FlightRecord])
def flights(start: datetime, end: datetime, origin: str | None = None,
            destination: str | None = None, include_cancelled: bool = False,
            limit: Annotated[int, Query(ge=1, le=1000)] = 500,
            store: FlightReader = Depends(get_store)) -> list[FlightRecord]:
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    return store.search(start, end, origin, destination, include_cancelled, limit)


@app.get("/v1/routes", response_model=list[RouteSummary])
def routes(start: datetime, end: datetime, origin: str | None = None,
           destination: str | None = None, limit: Annotated[int, Query(ge=1, le=1000)] = 500,
           store: FlightReader = Depends(get_store)) -> list[RouteSummary]:
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    return store.routes(start, end, origin, destination, limit)


@app.get("/v1/itineraries", response_model=list[Itinerary])
def itineraries(origin: str, destination: str, start: datetime, end: datetime,
                max_legs: Annotated[int, Query(ge=1, le=3)] = 2,
                min_connection_minutes: Annotated[int, Query(ge=20, le=360)] = 45,
                max_connection_minutes: Annotated[int, Query(ge=60, le=1440)] = 360,
                limit: Annotated[int, Query(ge=1, le=100)] = 20,
                store: FlightReader = Depends(get_store)) -> list[Itinerary]:
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    if origin.upper() == destination.upper():
        raise HTTPException(status_code=422, detail="origin and destination must differ")
    return build_itineraries(store, origin, destination, start, end, max_legs,
                             min_connection_minutes, max_connection_minutes, limit)
