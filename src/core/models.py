from datetime import date, datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from core.routing.models import airport_code, airport_codes, utc_instant


class FlightStatus(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    LANDED = "landed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class AircraftMetadata(BaseModel):
    """Aircraft identity, separate from a flight's schedule and route evidence."""

    model_config = ConfigDict(extra="forbid")
    icao_hex: str | None = Field(default=None, pattern=r"^[a-f0-9]{6}$")
    registration: str | None = Field(default=None, max_length=32)
    type_code: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=256)
    emitter_category: str | None = Field(default=None, pattern=r"^[A-D][0-7]$")
    operator: str | None = Field(default=None, max_length=128)
    owner_operator: str | None = Field(default=None, max_length=128)
    manufacture_year: str | None = Field(default=None, max_length=8)
    military: bool | None = None
    source: str = Field(min_length=1, max_length=128)
    observed_at: datetime

    _observed = field_validator("observed_at")(utc_instant)


class FlightRecord(BaseModel):
    provider: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    flight_number: str = Field(min_length=1)
    airline_iata: str | None = None
    origin_iata: str = Field(pattern=r"^[A-Z]{3}$")
    destination_iata: str = Field(pattern=r"^[A-Z]{3}$")
    departure_scheduled: datetime
    arrival_scheduled: datetime
    departure_actual: datetime | None = None
    arrival_actual: datetime | None = None
    status: FlightStatus = FlightStatus.UNKNOWN
    codeshares: list[str] = Field(default_factory=list)
    fetched_at: datetime
    aircraft: AircraftMetadata | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        if data.get("aircraft") is None:
            data.pop("aircraft", None)
        return data

    _codes = field_validator("origin_iata", "destination_iata", mode="before")(airport_code)

    @field_validator("flight_number", mode="before")
    @classmethod
    def nonblank_number(cls, value):
        if not isinstance(value, str):
            # TypeError escapes Pydantic validation instead of producing HTTP 422.
            raise ValueError("flight number must be a string")  # noqa: TRY004
        return value.strip()

    @model_validator(mode="after")
    def sensible_leg(self) -> "FlightRecord":
        # Also validate existing model instances supplied by internal callers.
        for field in (
            "departure_scheduled",
            "arrival_scheduled",
            "departure_actual",
            "arrival_actual",
            "fetched_at",
        ):
            value = getattr(self, field)
            if value is not None:
                setattr(self, field, utc_instant(value))
        if self.origin_iata == self.destination_iata:
            raise ValueError("origin and destination must differ")
        if self.arrival_scheduled <= self.departure_scheduled:
            raise ValueError("arrival must be after departure")
        return self


class AircraftObservation(BaseModel):
    """One aircraft's observed UTC day; no inferred schedule or airport endpoints."""

    day: date
    aircraft: AircraftMetadata
    first_seen: datetime
    last_seen: datetime
    positions: int = Field(ge=1)
    callsigns: list[str]
    data_sources: list[str]
    squawks: list[str]
    emergencies: list[str]
    max_altitude_ft: float | None = None
    max_ground_speed_knots: float | None = None
    classification: str = "commercial_fixed_wing_candidate"
    classification_evidence: list[str]
    trace_offset: int | None = Field(default=None, ge=0)
    trace_bytes: int | None = Field(default=None, ge=1, le=32 * 1024**2)
    trace_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    _times = field_validator("first_seen", "last_seen")(utc_instant)

    @model_validator(mode="after")
    def valid_day(self):
        if self.first_seen.date() != self.day or self.last_seen.date() != self.day:
            raise ValueError("observation timestamps must fall within the UTC day")
        if self.last_seen < self.first_seen or self.aircraft.icao_hex is None:
            raise ValueError("observations require an ICAO address and ordered timestamps")
        return self


class IngestRequest(BaseModel):
    airports: list[str] = Field(min_length=1, max_length=50)
    start: date
    end: date

    _airports = field_validator("airports", mode="before")(airport_codes)

    @model_validator(mode="after")
    def valid_range(self) -> "IngestRequest":
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        return self


class IngestResult(BaseModel):
    provider: str
    fetched: int
    upserted: int
    airports: list[str]
    start: date
    end: date


class RouteSummary(BaseModel):
    origin_iata: str
    destination_iata: str
    flight_count: int
    airlines: list[str]
    first_departure: datetime
    last_departure: datetime


class Itinerary(BaseModel):
    legs: list[FlightRecord]
    departure: datetime
    arrival: datetime
    duration_minutes: int
