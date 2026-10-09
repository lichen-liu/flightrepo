from datetime import date, datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class FlightStatus(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    LANDED = "landed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class FlightRecord(BaseModel):
    provider: str
    provider_id: str
    flight_number: str
    airline_iata: str | None = None
    origin_iata: str = Field(min_length=3, max_length=3)
    destination_iata: str = Field(min_length=3, max_length=3)
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
        return value.strip().upper()

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
        airports = sorted({v.strip().upper() for v in values})
        if any(len(code) != 3 or not code.isalpha() for code in airports):
            raise ValueError("airports must be three-letter IATA codes")
        return airports

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
