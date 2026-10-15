"""Browser-facing inputs and dataset evidence. All calculations remain in core."""

from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

from core.connections import Airport
from core.routing.models import (
    Ranking,
    RouteSearchRequest,
    RoutingError,
    SearchLimits,
    TimeBasis,
    ViaAirports,
    airport_code,
    via_airports,
)
from core.routing.results import RouteSearchResult
from core.timezones import resolve_local


class LocalRouteSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: str
    destination: str
    via_airport: ViaAirports = None
    departure_date: date
    departure_last_date: date | None = None
    arrival_by_local: datetime | None = None
    arrival_fold: Literal[0, 1] | None = None
    departure_timezone: str | None = None
    arrival_timezone: str | None = None
    max_legs: int = Field(default=3, ge=1, le=4)
    minimum_connection_minutes: int = Field(default=45, ge=0, le=1440)
    maximum_connection_minutes: int | None = Field(default=360, ge=0, le=44640)
    allow_airport_change: bool = False
    avoid_airports: tuple[str, ...] = Field(default=(), max_length=50)
    ranking: Ranking = "fewest_legs"
    limit: int = Field(default=20, ge=1, le=100)
    trace: bool = False
    limits: SearchLimits = Field(default_factory=SearchLimits)
    time_basis: TimeBasis | None = None

    @field_validator("origin", "destination", mode="before")
    @classmethod
    def codes(cls, value):
        return airport_code(value)

    _via_codes = field_validator("via_airport", mode="before")(via_airports)

    def utc_request(self, airports: tuple[Airport, ...]) -> RouteSearchRequest:
        by_code = {a.iata: a for a in airports}
        required = ["origin"]
        if self.arrival_by_local is not None:
            required.append("destination")
        for field in required:
            if (
                getattr(self, field) not in by_code
                or by_code[getattr(self, field)].timezone is None
            ):
                raise RoutingError(
                    "airport_metadata_missing",
                    "Airport local time requires timezone metadata. Use UTC or import the airport catalog.",
                    context={"field": field, "airport": getattr(self, field)},
                )
        for field, code in (
            ("departure_timezone", self.origin),
            ("arrival_timezone", self.destination),
        ):
            expected = getattr(self, field)
            if expected is None or field == "arrival_timezone" and self.arrival_by_local is None:
                continue
            actual = by_code[code].timezone
            if expected != actual:
                raise RoutingError(
                    "airport_timezone_changed",
                    "Airport timezone changed. Review the dates and retry.",
                    context={
                        "airport": code,
                        "field": field,
                        "expected_timezone": expected,
                        "actual_timezone": actual,
                    },
                )
        first = self.departure_date
        last = self.departure_last_date or first
        if last < first:
            raise RoutingError(
                "invalid_local_dates",
                "Last departure date must be on or after the first.",
                context={"field": "departure_last_date"},
            )
        if (last - first).days >= 31:
            raise RoutingError(
                "invalid_local_dates",
                "Choose at most 31 departure dates.",
                context={"field": "departure_last_date"},
            )
        try:
            end = last + timedelta(days=1)
        except OverflowError as error:
            raise RoutingError(
                "invalid_local_dates", "Departure date is outside the supported range."
            ) from error
        zone = by_code[self.origin].timezone
        departure_end = resolve_local(
            datetime.combine(end, time.min), zone, field="departure_last_date"
        )
        try:
            horizon = (
                departure_end + timedelta(days=7)
                if self.arrival_by_local is None
                else resolve_local(
                    self.arrival_by_local,
                    by_code[self.destination].timezone,
                    fold=self.arrival_fold,
                    field="arrival_by_local",
                )
            )
        except OverflowError as error:
            raise RoutingError(
                "invalid_local_dates", "Departure dates are outside the supported search range."
            ) from error
        values = self.model_dump(
            exclude={
                "departure_date",
                "departure_last_date",
                "arrival_by_local",
                "arrival_fold",
                "departure_timezone",
                "arrival_timezone",
            }
        )
        return RouteSearchRequest(
            **values,
            departure_start=resolve_local(
                datetime.combine(first, time.min), zone, field="departure_date"
            ),
            departure_end=departure_end,
            horizon_end=horizon,
        )


class LocalRouteSearchResult(RouteSearchResult):
    departure_timezone: str
    arrival_timezone: str | None


class ResolveTimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    airport: str = Field(pattern=r"^[A-Z]{3}$")
    local_time: datetime
    fold: Literal[0, 1] | None = None
    expected_timezone: str | None = None


class ResolvedTime(BaseModel):
    airport: str
    timezone: str
    utc: datetime


class CatalogAirport(BaseModel):
    iata: str
    name: str | None = None
    city_id: str | None = None
    timezone: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class DatasetEvidence(BaseModel):
    flight_count: int
    total_record_count: int = 0
    needs_review_count: int = 0
    cancelled_count: int
    first_departure: datetime | None
    last_departure: datetime | None
    oldest_fact_refresh: datetime | None
    newest_fact_refresh: datetime | None
    providers: list[str]
    coverage: Literal["unknown"] = "unknown"
    time_bases: dict[str, int] = Field(default_factory=dict)
    default_time_basis: TimeBasis = "scheduled"
    sources: list[dict] = Field(default_factory=list)


class WebCatalog(BaseModel):
    airports: list[CatalogAirport]
    airports_truncated: bool
    dataset: DatasetEvidence
    suggested_route: dict | None = None


class AvailableDates(BaseModel):
    origin: str
    destination: str | None = None
    timezone: str
    dates: list[date]
    time_basis: TimeBasis = "scheduled"

    @model_serializer(mode="wrap")
    def legacy_schedule(self, handler):
        data = handler(self)
        if self.time_basis == "scheduled":
            data.pop("time_basis", None)
        if self.destination is None:
            data.pop("destination", None)
        return data
