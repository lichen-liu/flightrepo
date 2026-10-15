"""Airport metadata and read-only continuation planning contracts."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from core.models import FlightRecord
from core.routing.models import (
    SearchDiagnostics,
    SearchLimits,
    TimeBasis,
    ViaAirports,
    airport_codes,
    optional_airport_code,
    utc_instant,
    validate_via_constraints,
    via_airports,
)


class Airport(BaseModel):
    iata: str = Field(pattern=r"^[A-Z]{3}$")
    name: str | None = Field(default=None, min_length=1)
    city_id: str | None = Field(default=None, min_length=1)
    timezone: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be an IANA zone") from error
        return value

    @model_validator(mode="after")
    def coordinate_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("airport coordinates must both be present or both be unknown")
        return self


class AirportTransfer(BaseModel):
    arrival_iata: str = Field(pattern=r"^[A-Z]{3}$")
    departure_iata: str = Field(pattern=r"^[A-Z]{3}$")
    minimum_minutes: int = Field(ge=1, le=1440)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def different_airports(self) -> "AirportTransfer":
        if self.arrival_iata == self.departure_iata:
            raise ValueError("airport transfers require different airports")
        return self


class FlightRef(BaseModel):
    provider: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)


class ConnectionWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_minutes: int = Field(default=45, ge=0, le=1440)
    maximum_minutes: int | None = Field(default=None, ge=0, le=44_640)
    not_before: datetime | None = None

    @field_validator("not_before")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        return utc_instant(value) if value is not None else None

    @model_validator(mode="after")
    def valid_window(self) -> "ConnectionWindow":
        if self.maximum_minutes is not None and self.maximum_minutes < self.minimum_minutes:
            raise ValueError("maximum_minutes must be at least minimum_minutes")
        return self


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: list[FlightRef] = Field(min_length=1)
    destination: str = Field(pattern=r"^[A-Z]{3}$")
    horizon_end: datetime
    window: ConnectionWindow = Field(default_factory=ConnectionWindow)
    allow_airport_change: bool = False
    via_airport: ViaAirports = Field(
        default=None,
        description="Required IATA code or ordered list across the retained prefix and rebuilt suffix.",
    )
    next_airport: str | None = Field(
        default=None,
        description="Exact destination IATA airport of the immediately next flight only.",
    )
    avoid_airports: list[str] = Field(default_factory=list, max_length=50)
    max_legs: int | None = Field(default=None, ge=1, description="Null means no leg-count limit.")
    chosen_next: FlightRef | None = None
    option_limit: int = Field(default=100, ge=1, le=500)
    trace: bool = False
    limits: SearchLimits = Field(default_factory=SearchLimits)
    time_basis: TimeBasis | None = None

    @model_serializer(mode="wrap")
    def optional_basis(self, handler):
        data = handler(self)
        if self.time_basis is None:
            data.pop("time_basis", None)
        return data

    _avoided_codes = field_validator("avoid_airports", mode="before")(airport_codes)
    _via_codes = field_validator("via_airport", mode="before")(via_airports)
    _next_code = field_validator("next_airport", mode="before")(optional_airport_code)
    _horizon = field_validator("horizon_end")(utc_instant)

    @model_validator(mode="after")
    def sensible_via(self):
        validate_via_constraints(
            self.via_airport,
            (self.destination,),
            self.avoid_airports,
            self.max_legs,
            self.allow_airport_change,
        )
        return self


class PlanConnection(BaseModel):
    arrival_iata: str
    departure_iata: str
    wait_minutes: int
    minimum_minutes: int
    airport_change: bool
    source: str


class ContinuationOption(BaseModel):
    flight: FlightRecord
    connection: PlanConnection
    tail: list[FlightRecord]
    tail_connections: list[PlanConnection]
    complete: bool


class RebuiltPlan(BaseModel):
    legs: list[FlightRecord]
    connections: list[PlanConnection]
    complete: bool
    options: list[ContinuationOption]
    data_truncated: bool
    search_truncated: bool = False
    horizon_end: datetime
    message: str
    options_total: int | None = None
    options_truncated: bool = False
    diagnostics: SearchDiagnostics | None = None
    airports: list[Airport] = Field(default_factory=list)
