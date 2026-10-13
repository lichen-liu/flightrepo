"""Validated search inputs and bounded, replayable routing diagnostics."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ENGINE_VERSION = 'routing-2'
Ranking = Literal['fewest_legs', 'shortest_elapsed', 'earliest_arrival']


def airport_code(value: str) -> str:
    value = value.strip().upper()
    if len(value) != 3 or not value.isascii() or not value.isalpha():
        raise ValueError('airports must be three-letter IATA codes')
    return value


def utc_instant(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError('timestamps must include a UTC offset')
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as error:
        raise ValueError('timestamp cannot be represented in UTC') from error


class SearchLimits(BaseModel):
    max_records: int = Field(default=10_000, ge=1, le=20_000)
    max_candidates: int = Field(default=100_000, ge=1, le=200_000)
    max_trace_events: int = Field(default=200, ge=0, le=500)


class RouteSearchRequest(BaseModel):
    origin: str
    destination: str
    departure_start: datetime
    departure_end: datetime
    horizon_end: datetime
    max_legs: int = Field(default=3, ge=1, le=4)
    minimum_connection_minutes: int = Field(default=45, ge=0, le=1440)
    maximum_connection_minutes: int | None = Field(default=360, ge=0, le=44_640)
    allow_airport_change: bool = False
    avoid_airports: list[str] = Field(default_factory=list, max_length=50)
    ranking: Ranking = 'fewest_legs'
    limit: int = Field(default=20, ge=1, le=100)
    trace: bool = False
    limits: SearchLimits = Field(default_factory=SearchLimits)

    _codes = field_validator('origin', 'destination', mode='before')(airport_code)
    _times = field_validator('departure_start', 'departure_end', 'horizon_end')(utc_instant)

    @field_validator('avoid_airports', mode='before')
    @classmethod
    def avoided_codes(cls, value):
        return sorted({airport_code(code) for code in value})

    @model_validator(mode='after')
    def sensible_query(self):
        if self.origin == self.destination:
            raise ValueError('origin and destination must differ')
        if not self.departure_start < self.departure_end <= self.horizon_end:
            raise ValueError('require departure_start < departure_end <= horizon_end')
        if self.departure_end - self.departure_start > timedelta(days=31):
            raise ValueError('departure window must be at most 31 days')
        if self.horizon_end - self.departure_end > timedelta(days=31):
            raise ValueError('arrival horizon must be within 31 days of departure_end')
        if self.maximum_connection_minutes is not None and self.maximum_connection_minutes < self.minimum_connection_minutes:
            raise ValueError('maximum connection must be at least the minimum')
        if self.origin in self.avoid_airports or self.destination in self.avoid_airports:
            raise ValueError('cannot avoid the origin or destination')
        return self


class TraceEvent(BaseModel):
    path: list[tuple[str, str]] = Field(default_factory=list)
    candidate: tuple[str, str]
    decision: str
    wait_minutes: float | None = None
    minimum_minutes: int | None = None


class SearchStats(BaseModel):
    rows_loaded: int = 0
    origins_queried: int = 0
    candidates_checked: int = 0
    candidates_skipped_by_time: int = 0
    states_expanded: int = 0
    memo_hits: int = 0
    routes_found: int = 0
    rejections: dict[str, int] = Field(default_factory=dict)
    trace_events_omitted: int = 0


class SearchDiagnostics(BaseModel):
    engine_version: str = ENGINE_VERSION
    snapshot_id: str
    request_id: str
    ranking: str
    scope: str = 'loaded_snapshot'
    exhaustive: bool
    termination_reasons: list[str] = Field(default_factory=list)
    result_limit_reached: bool = False
    stats: SearchStats
    events: list[TraceEvent] = Field(default_factory=list)


class RoutingError(ValueError):
    """A stable machine-readable code alongside a readable validation message."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class IncompleteSearchError(RoutingError):
    def __init__(self, diagnostics: SearchDiagnostics):
        super().__init__('incomplete_search', 'search limits reached; use /v1/itineraries/search for partial results and diagnostics')
        self.diagnostics = diagnostics
