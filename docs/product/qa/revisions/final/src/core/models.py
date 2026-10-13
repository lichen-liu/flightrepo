from datetime import date, datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator
from core.routing.models import airport_code


class FlightStatus(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    LANDED = "landed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class FlightRecord(BaseModel):
    provider: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    flight_number: str = Field(min_length=1)
    airline_iata: str | None = None
    origin_iata: str = Field(pattern=r'^[A-Z]{3}$')
    destination_iata: str = Field(pattern=r'^[A-Z]{3}$')
    departure_scheduled: datetime
    arrival_scheduled: datetime
    departure_actual: datetime | None = None
    arrival_actual: datetime | None = None
    status: FlightStatus = FlightStatus.UNKNOWN
    codeshares: list[str] = Field(default_factory=list)
    fetched_at: datetime

    @field_validator("origin_iata", "destination_iata", mode="before")
    @classmethod
    def uppercase_airport(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError('airport codes must be strings')
        return value.strip().upper()

    @field_validator('flight_number', mode='before')
    @classmethod
    def nonblank_number(cls, value):
        if not isinstance(value, str):
            raise ValueError('flight number must be a string')
        return value.strip()

    @model_validator(mode="after")
    def sensible_leg(self) -> "FlightRecord":
        for field in ("departure_scheduled", "arrival_scheduled", "departure_actual", "arrival_actual", "fetched_at"):
            value = getattr(self, field)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{field} must include a UTC offset")
                setattr(self, field, value.astimezone(timezone.utc))
        if self.origin_iata == self.destination_iata:
            raise ValueError("origin and destination must differ")
        if self.arrival_scheduled <= self.departure_scheduled:
            raise ValueError("arrival must be after departure")
        return self


class IngestRequest(BaseModel):
    airports: list[str] = Field(min_length=1, max_length=50)
    start: date
    end: date

    @field_validator("airports", mode="before")
    @classmethod
    def normalize_airports(cls, values: list[str]) -> list[str]:
        if not isinstance(values, (list, tuple, set)):
            raise ValueError('airports must be a list of IATA codes')
        return sorted({airport_code(v) for v in values})

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
