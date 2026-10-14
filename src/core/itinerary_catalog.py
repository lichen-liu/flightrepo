"""Provider-neutral normalized flight chunks and their searchable coverage index."""

from datetime import date, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from core.connections import Airport, AirportTransfer
from core.models import FlightRecord
from core.routing.models import TimeBasis, airport_code, utc_instant

MAX_CHUNK_RECORDS = 20_000


def http_url(value):
    url = urlsplit(value)
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or url.fragment
    ):
        raise ValueError("Use an HTTP(S) URL without credentials or fragment")
    return value


class ItineraryChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    url: str
    encoding: Literal["json", "gzip"] = "gzip"
    bytes: int = Field(gt=0, le=64 * 1024**2)
    provider: str = Field(min_length=1)
    origin: str
    destinations: list[str] = Field(min_length=1)
    departure_start: datetime
    departure_end: datetime
    flight_count: int = Field(ge=1, le=MAX_CHUNK_RECORDS)
    cancelled_count: int = Field(ge=0)
    first_departure: datetime | None = None
    last_departure: datetime | None = None
    oldest_fact_refresh: datetime
    newest_fact_refresh: datetime
    origin_timezone: str | None = None
    dates_utc: list[date]
    dates_local: list[date]
    time_basis: TimeBasis = "scheduled"

    @model_serializer(mode="wrap")
    def legacy_schedule(self, handler):
        data = handler(self)
        if self.time_basis == "scheduled":
            data.pop("time_basis", None)
        return data

    _url = field_validator("url")(http_url)
    _origin = field_validator("origin", mode="before")(airport_code)
    _times = field_validator(
        "departure_start",
        "departure_end",
        "first_departure",
        "last_departure",
        "oldest_fact_refresh",
        "newest_fact_refresh",
    )(lambda value: utc_instant(value) if value is not None else None)

    @model_validator(mode="after")
    def ordered(self):
        if self.departure_end <= self.departure_start or self.cancelled_count > self.flight_count:
            raise ValueError("Invalid chunk departure window or cancellation count")
        if self.departure_end - self.departure_start > timedelta(days=31):
            raise ValueError("Chunk windows must be at most 31 days")
        if self.cancelled_count == self.flight_count:
            if (
                self.first_departure is not None
                or self.last_departure is not None
                or self.dates_utc
                or self.dates_local
            ):
                raise ValueError("Cancelled-only chunks cannot advertise departure dates")
        elif (
            self.first_departure is None
            or self.last_departure is None
            or (not self.dates_utc and self.time_basis != "observed")
        ):
            raise ValueError("Non-cancelled chunks require departure coverage")
        if self.first_departure is not None and not (
            self.departure_start <= self.first_departure <= self.last_departure < self.departure_end
        ):
            raise ValueError("Departure evidence must be inside the chunk window")
        if self.oldest_fact_refresh > self.newest_fact_refresh:
            raise ValueError("Invalid fact refresh window")
        if self.origin_timezone is None and self.dates_local:
            raise ValueError("Local dates require an origin timezone")
        last = self.departure_end - timedelta(microseconds=1)
        if any(not self.departure_start.date() <= day <= last.date() for day in self.dates_utc):
            raise ValueError("UTC dates fall outside chunk window")
        if self.origin_timezone:
            try:
                zone = ZoneInfo(self.origin_timezone)
            except KeyError as error:
                raise ValueError("Unknown origin timezone") from error
            if any(
                not self.departure_start.astimezone(zone).date()
                <= day
                <= last.astimezone(zone).date()
                for day in self.dates_local
            ):
                raise ValueError("Local dates fall outside chunk window")
        self.destinations = sorted({airport_code(code) for code in self.destinations})
        self.dates_utc = sorted(set(self.dates_utc))
        self.dates_local = sorted(set(self.dates_local))
        return self


class ItineraryCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal[1, 2] = 1
    airports: list[Airport] = Field(default_factory=list, max_length=20_000)
    transfers: list[AirportTransfer] = Field(default_factory=list, max_length=20_000)
    chunks: list[ItineraryChunk] = Field(max_length=100_000)

    @model_validator(mode="after")
    def unambiguous(self):
        seen, windows = set(), {}
        for chunk in self.chunks:
            if self.format_version == 1 and chunk.time_basis != "scheduled":
                raise ValueError("Observed/actual chunks require catalogue format version 2")
            if chunk.sha256 in seen:
                raise ValueError("Duplicate itinerary chunk")
            seen.add(chunk.sha256)
            windows.setdefault((chunk.provider, chunk.origin, chunk.time_basis), []).append(chunk)
        for chunks in windows.values():
            ordered = sorted(chunks, key=lambda chunk: chunk.departure_start)
            if any(
                left.departure_end > right.departure_start
                for left, right in zip(ordered, ordered[1:])
            ):
                raise ValueError("Itinerary chunk windows must not overlap for a provider/origin")
        by_code = {airport.iata: airport for airport in self.airports}
        if len(by_code) != len(self.airports):
            raise ValueError("Duplicate catalogue airport")
        for chunk in self.chunks:
            if chunk.origin_timezone is not None and (
                chunk.origin not in by_code
                or chunk.origin_timezone != by_code[chunk.origin].timezone
            ):
                raise ValueError("Chunk local dates must use catalogue airport timezone")
        for transfer in self.transfers:
            first, last = by_code.get(transfer.arrival_iata), by_code.get(transfer.departure_iata)
            if (
                first is None
                or last is None
                or first.city_id is None
                or first.city_id != last.city_id
            ):
                raise ValueError("Transfer airports must exist in the same city")
        if len({(t.arrival_iata, t.departure_iata) for t in self.transfers}) != len(self.transfers):
            raise ValueError("Duplicate catalogue transfer")
        return self


class ItineraryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flights: list[FlightRecord] = Field(min_length=1, max_length=MAX_CHUNK_RECORDS)
