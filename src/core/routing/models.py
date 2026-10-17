"""Validated search inputs and bounded, replayable routing diagnostics."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

ENGINE_VERSION = "routing-7"
Ranking = Literal["fewest_legs", "shortest_elapsed", "earliest_arrival"]
TimeBasis = Literal["scheduled", "actual", "observed"]
ViaAirports = str | list[str] | None
MAX_DEPARTURE_DAYS = 31
MAX_ONWARD_DAYS = 31


def airport_code(value: str) -> str:
    if not isinstance(value, str):
        # TypeError escapes Pydantic validation instead of producing HTTP 422.
        raise ValueError("airport codes must be strings")  # noqa: TRY004
    value = value.strip().upper()
    if len(value) != 3 or not value.isascii() or not value.isalpha():
        raise ValueError("airports must be three-letter IATA codes")
    return value


def airport_codes(values) -> list[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        # TypeError escapes Pydantic validation instead of producing HTTP 422.
        raise ValueError("airports must be a list of IATA codes")  # noqa: TRY004
    return sorted({airport_code(value) for value in values})


def optional_airport_code(value: str | None) -> str | None:
    return airport_code(value) if value is not None else None


def via_airports(value) -> ViaAirports:
    if value is None or isinstance(value, str):
        return optional_airport_code(value)
    if not isinstance(value, (list, tuple)):
        raise ValueError("via_airport must be an IATA code or an ordered list of codes")
    if len(value) > 6:
        raise ValueError("at most six via airports are supported")
    codes = [airport_code(code) for code in value]
    if len(set(codes)) != len(codes):
        raise ValueError("via airports must not repeat")
    return codes


def required_via_airports(value: ViaAirports) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else tuple(value or ())


def validate_via_constraints(value, endpoints, avoided, max_legs, allow_airport_change):
    codes = required_via_airports(value)
    if any(code in endpoints for code in codes):
        raise ValueError("via airports must be intermediate airports")
    if any(code in avoided for code in codes):
        raise ValueError("cannot avoid a required via airport")
    capacity = (max_legs - 1) * (2 if allow_airport_change else 1) if max_legs is not None else None
    if capacity is not None and len(codes) > capacity:
        raise ValueError("increase max_legs or allow airport changes to visit all via airports")


def utc_instant(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ValueError("timestamp cannot be represented in UTC") from error


class SearchLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_records: int = Field(
        default=10_000,
        ge=1,
        le=20_000,
        description="Full-record snapshot/replay limit. SQL-projected database routing does not use this limit.",
    )
    max_candidates: int | None = Field(
        default=None,
        ge=1,
        description="Optional caller work budget. Null examines candidates without a count cutoff.",
    )
    max_trace_events: int = Field(default=200, ge=0, le=500)


class RouteSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: str
    destination: str
    departure_start: datetime
    departure_end: datetime
    horizon_end: datetime
    max_legs: int | None = Field(default=None, ge=1, description="Null means no leg-count limit.")
    minimum_connection_minutes: int = Field(default=45, ge=0, le=1440)
    maximum_connection_minutes: int | None = Field(default=360, ge=0, le=44_640)
    allow_airport_change: bool = False
    via_airport: ViaAirports = Field(
        default=None,
        description="Required intermediate IATA code or ordered list. Each can be an arrival or permitted transfer departure.",
    )
    avoid_airports: list[str] = Field(default_factory=list, max_length=50)
    ranking: Ranking = "fewest_legs"
    limit: int = Field(default=20, ge=1, le=100)
    trace: bool = False
    limits: SearchLimits = Field(default_factory=SearchLimits)
    time_basis: TimeBasis | None = None

    @model_serializer(mode="wrap")
    def optional_basis(self, handler):
        data = handler(self)
        if self.time_basis is None:
            data.pop("time_basis", None)
        return data

    _codes = field_validator("origin", "destination", mode="before")(airport_code)
    _times = field_validator("departure_start", "departure_end", "horizon_end")(utc_instant)

    _avoided_codes = field_validator("avoid_airports", mode="before")(airport_codes)
    _via_codes = field_validator("via_airport", mode="before")(via_airports)

    @model_validator(mode="after")
    def sensible_query(self):
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        if not self.departure_start < self.departure_end <= self.horizon_end:
            raise ValueError("require departure_start < departure_end <= horizon_end")
        if self.departure_end - self.departure_start > timedelta(days=MAX_DEPARTURE_DAYS):
            raise ValueError("departure window must be at most 31 days")
        if self.horizon_end - self.departure_end > timedelta(days=MAX_ONWARD_DAYS):
            raise ValueError("arrival horizon must be within 31 days of departure_end")
        if (
            self.maximum_connection_minutes is not None
            and self.maximum_connection_minutes < self.minimum_connection_minutes
        ):
            raise ValueError("maximum connection must be at least the minimum")
        if self.origin in self.avoid_airports or self.destination in self.avoid_airports:
            raise ValueError("cannot avoid the origin or destination")
        validate_via_constraints(
            self.via_airport,
            (self.origin, self.destination),
            self.avoid_airports,
            self.max_legs,
            self.allow_airport_change,
        )
        return self


class TraceEvent(BaseModel):
    path: list[tuple[str, str]] = Field(default_factory=list)
    candidate: tuple[str, str]
    decision: str
    wait_minutes: float | None = None
    minimum_minutes: int | None = None


class SearchStats(BaseModel):
    rows_loaded: int = 0
    records_hydrated: int = 0
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
    scope: str = "loaded_snapshot"
    exhaustive: bool
    termination_reasons: list[str] = Field(default_factory=list)
    result_limit_reached: bool = False
    stats: SearchStats
    events: list[TraceEvent] = Field(default_factory=list)


class RoutingError(ValueError):
    """A stable machine-readable code alongside a readable validation message."""

    def __init__(self, code: str, message: str, *, context: dict | None = None):
        super().__init__(message)
        self.code = code
        self.context = context


class IncompleteSearchError(RoutingError):
    def __init__(self, diagnostics: SearchDiagnostics):
        super().__init__(
            "incomplete_search",
            "search limits reached; use /v1/itineraries/search for partial results and diagnostics",
        )
        self.diagnostics = diagnostics
