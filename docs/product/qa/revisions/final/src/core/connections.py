"""Airport metadata and read-only continuation planning contracts."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.models import FlightRecord
from core.routing.models import SearchDiagnostics, SearchLimits, airport_code


class Airport(BaseModel):
    iata: str = Field(pattern=r"^[A-Z]{3}$")
    name: str = Field(min_length=1)
    city_id: str = Field(min_length=1)
    timezone: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be an IANA zone") from error
        return value


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
    model_config = ConfigDict(extra='forbid')
    minimum_minutes: int = Field(default=45, ge=0, le=1440)
    maximum_minutes: int | None = Field(default=None, ge=0, le=44_640)
    not_before: datetime | None = None

    @field_validator("not_before")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.utcoffset() is None:
                raise ValueError("not_before must include a UTC offset")
            return value.astimezone(timezone.utc)
        return value

    @model_validator(mode="after")
    def valid_window(self) -> "ConnectionWindow":
        if self.maximum_minutes is not None and self.maximum_minutes < self.minimum_minutes:
            raise ValueError("maximum_minutes must be at least minimum_minutes")
        return self


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prefix: list[FlightRef] = Field(min_length=1, max_length=4)
    destination: str = Field(pattern=r"^[A-Z]{3}$")
    horizon_end: datetime
    window: ConnectionWindow = Field(default_factory=ConnectionWindow)
    allow_airport_change: bool = False
    avoid_airports: list[str] = Field(default_factory=list, max_length=50)
    max_legs: int = Field(default=4, ge=1, le=4)
    chosen_next: FlightRef | None = None
    option_limit: int = Field(default=100, ge=1, le=500)
    trace: bool = False
    limits: SearchLimits = Field(default_factory=SearchLimits)

    @field_validator('avoid_airports', mode='before')
    @classmethod
    def avoided_codes(cls, value):
        return sorted({airport_code(code) for code in value})

    @field_validator("horizon_end")
    @classmethod
    def aware_horizon(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("horizon_end must include a UTC offset")
        return value.astimezone(timezone.utc)


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
