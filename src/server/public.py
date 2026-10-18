"""Allowlisted public contracts; source provenance never passes this boundary."""

import hashlib
import hmac
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.connections import Airport, RebuildRequest
from core.models import FlightRecord, FlightStatus
from core.routing.models import Ranking, RoutingError, TimeBasis, ViaAirports
from server.web_models import CatalogAirport, LocalRouteSearchResult, WebCatalog


class PublicRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flight_id: str = Field(pattern=r"^flight_[1-9][0-9]{0,18}_[0-9a-f]{32}$")


class PublicRebuildRequest(RebuildRequest):
    prefix: list[PublicRef] = Field(min_length=1)
    chosen_next: PublicRef | None = None


class PublicLabel(BaseModel):
    code: str
    severity: Literal["info", "caution", "warning"]
    label: str
    detail: str


class PublicQuality(BaseModel):
    level: Literal["standard", "noted", "uncertain", "conflict"]
    labels: list[PublicLabel]


class PublicFlight(BaseModel):
    flight_id: str
    flight_number: str | None
    airline_iata: str | None
    callsign: str | None
    operator_icao: str | None
    operator_name: str | None
    aircraft_type_code: str | None
    origin_iata: str
    destination_iata: str
    departure_scheduled: datetime | None
    arrival_scheduled: datetime | None
    departure_actual: datetime | None
    arrival_actual: datetime | None
    departure_actual_kind: str | None
    arrival_actual_kind: str | None
    departure_lower: datetime | None
    departure_upper: datetime | None
    arrival_lower: datetime | None
    arrival_upper: datetime | None
    time_basis: TimeBasis
    status: FlightStatus
    codeshares: list[str]
    quality: PublicQuality


class PublicConnection(BaseModel):
    arrival_iata: str
    departure_iata: str
    wait_minutes: int
    minimum_minutes: int
    airport_change: bool


class PublicItinerary(BaseModel):
    legs: list[PublicFlight]
    departure: datetime
    arrival: datetime
    duration_minutes: int


class PublicRouteItinerary(PublicItinerary):
    itinerary_id: str
    connections: list[PublicConnection]


class PublicStats(BaseModel):
    routes_found: int


class PublicDiagnostics(BaseModel):
    exhaustive: bool
    termination_reasons: list[str]
    result_limit_reached: bool
    stats: PublicStats


class PublicQuery(BaseModel):
    origin: str
    destination: str
    departure_start: datetime
    departure_end: datetime
    horizon_end: datetime
    max_legs: int | None
    minimum_connection_minutes: int
    maximum_connection_minutes: int | None
    allow_airport_change: bool
    via_airport: ViaAirports
    avoid_airports: list[str]
    ranking: Ranking
    limit: int
    time_basis: TimeBasis | None = None
    offset: int = 0


class PublicSearchResult(BaseModel):
    query: PublicQuery
    items: list[PublicRouteItinerary]
    diagnostics: PublicDiagnostics
    airports: list[Airport]
    next_offset: int | None = None
    data_revision: str | None = None


class PublicLocalSearchResult(PublicSearchResult):
    departure_timezone: str
    arrival_timezone: str | None


class PublicOption(BaseModel):
    flight: PublicFlight
    connection: PublicConnection
    tail: list[PublicFlight]
    tail_connections: list[PublicConnection]
    complete: bool


class PublicPlan(BaseModel):
    legs: list[PublicFlight]
    connections: list[PublicConnection]
    complete: bool
    options: list[PublicOption]
    data_truncated: bool
    search_truncated: bool
    horizon_end: datetime
    message: str
    options_total: int | None
    options_truncated: bool
    diagnostics: PublicDiagnostics | None
    next_option_offset: int | None = None
    data_revision: str | None = None
    airports: list[Airport]


class PublicDataset(BaseModel):
    flight_count: int
    total_record_count: int
    needs_review_count: int
    cancelled_count: int
    first_departure: datetime | None
    last_departure: datetime | None
    coverage: Literal["unknown"]
    time_bases: dict[str, int]
    default_time_basis: TimeBasis
    data_mode: Literal["synthetic", "real", "mixed"]


class PublicCatalog(BaseModel):
    airports: list[CatalogAirport]
    airports_truncated: bool
    dataset: PublicDataset
    suggested_route: dict | None


def identity(row_id, provider, key):
    # The row locator makes resolution indexed, while the identity fingerprint
    # prevents a reused row ID after replacement/reset from selecting another leg.
    digest = hashlib.sha256(json.dumps([provider, key]).encode()).hexdigest()[:32]
    return f"flight_{row_id}_{digest}"


def references(store, records):
    keys = {(r.provider, r.provider_id) for r in records}
    result = {}
    with store.connect() as c:
        # Bound parameters even for large continuation option lists.
        for provider in sorted({p for p, _ in keys}):
            remaining = [key for source, key in keys if source == provider]
            for offset in range(0, len(remaining), 200):
                group = remaining[offset : offset + 200]
                rows = c.execute(
                    "SELECT f.id,s.code,f.source_record_key FROM data_sources s "
                    "JOIN flight_legs f ON f.source_id=s.id WHERE s.code=? "
                    "AND f.source_record_key IN (" + ",".join("?" for _ in group) + ")",
                    [provider, *group],
                )
                for row_id, source, key in rows:
                    result[(source, key)] = identity(row_id, source, key)
    if result.keys() != keys:
        raise RoutingError("flight_changed", "Flight data changed. Search again.")
    return result


def resolve_request(store, request):
    refs = [*request.prefix, *([request.chosen_next] if request.chosen_next else [])]
    resolved = {}
    with store.connect() as c:
        c.execute("BEGIN")
        for ref in refs:
            locator = int(ref.flight_id.split("_")[1])
            if locator > 2**63 - 1:
                raise RoutingError("flight_missing", "A selected flight is no longer available.")
            row = c.execute(
                "SELECT s.code,f.source_record_key FROM flight_legs f "
                "JOIN data_sources s ON s.id=f.source_id WHERE f.id=? AND NOT EXISTS "
                "(SELECT 1 FROM flight_tombstones t WHERE t.source_id=f.source_id "
                "AND t.source_record_key=f.source_record_key)",
                (locator,),
            ).fetchone()
            if row is None or not hmac.compare_digest(ref.flight_id, identity(locator, *row)):
                raise RoutingError("flight_missing", "A selected flight is no longer available.")
            resolved[ref.flight_id] = dict(provider=row[0], provider_id=row[1])
    data = request.model_dump(exclude={"prefix", "chosen_next"})
    return RebuildRequest.model_validate(
        data
        | dict(
            prefix=[resolved[r.flight_id] for r in request.prefix],
            chosen_next=resolved[request.chosen_next.flight_id] if request.chosen_next else None,
            trace=False,
        )
    )


def quality(record):
    level = record.quality["level"]
    labels = []
    if level == "conflict":
        labels.append(
            dict(
                code="timing_conflict",
                severity="warning",
                label="Timing conflict",
                detail="This itinerary has conflicting airport and timing information. It may be wrong.",
            )
        )
    elif level == "uncertain":
        labels.append(
            dict(
                code="uncertain_itinerary",
                severity="caution",
                label="Uncertain itinerary",
                detail="Some itinerary information is uncertain. Check it before relying on this route.",
            )
        )
    if record.effective_time_basis == "observed":
        labels.append(
            dict(
                code="approximate_times",
                severity="info",
                label="Approximate times",
                detail="Times are ranges rather than exact departure or arrival timestamps.",
            )
        )
    return PublicQuality(level=level, labels=labels)


def flight(record, refs):
    data = {
        key: getattr(record, key)
        for key in PublicFlight.model_fields
        if key
        not in {
            "flight_id",
            "time_basis",
            "quality",
            "departure_lower",
            "departure_upper",
            "arrival_lower",
            "arrival_upper",
        }
    }
    for event in ("departure", "arrival"):
        for side in ("lower", "upper"):
            try:
                data[f"{event}_{side}"] = getattr(record, f"{event}_{side}")
            except ValueError:
                data[f"{event}_{side}"] = None
    return PublicFlight(
        **data,
        flight_id=refs[(record.provider, record.provider_id)],
        time_basis=record.effective_time_basis,
        quality=quality(record),
    )


def diagnostics(value):
    return PublicDiagnostics.model_validate(value.model_dump()) if value is not None else None


def to_public(store, value):
    """Convert known result types only; never recursively serialize internal objects."""
    from core.connections import RebuiltPlan
    from core.routing.results import RouteSearchResult

    if isinstance(value, WebCatalog):
        data = value.dataset.model_dump()
        providers = data.get("providers", [])
        data["data_mode"] = (
            "synthetic" if providers == ["demo"] else "mixed" if "demo" in providers else "real"
        )
        return PublicCatalog(
            **(value.model_dump() | {"dataset": PublicDataset.model_validate(data)})
        )
    if isinstance(value, list):
        records = [
            r for item in value for r in ([item] if isinstance(item, FlightRecord) else item.legs)
        ]
    elif isinstance(value, RouteSearchResult):
        records = [r for item in value.items for r in item.legs]
    elif isinstance(value, RebuiltPlan):
        records = [
            *value.legs,
            *(r for option in value.options for r in [option.flight, *option.tail]),
        ]
    else:
        raise TypeError("Unsupported public result")
    refs = references(store, records) if records else {}

    def itinerary(item):
        data = {k: getattr(item, k) for k in ("departure", "arrival", "duration_minutes")}
        data["legs"] = [flight(r, refs) for r in item.legs]
        if hasattr(item, "connections"):
            return PublicRouteItinerary(
                **data,
                itinerary_id=item.itinerary_id,
                connections=[
                    PublicConnection.model_validate(c.model_dump()) for c in item.connections
                ],
            )
        return PublicItinerary(**data)

    if isinstance(value, list):
        return [
            flight(item, refs) if isinstance(item, FlightRecord) else itinerary(item)
            for item in value
        ]
    if isinstance(value, RouteSearchResult):
        data = dict(
            query=PublicQuery.model_validate(value.query.model_dump()),
            items=[itinerary(i) for i in value.items],
            diagnostics=diagnostics(value.diagnostics),
            airports=value.airports,
            next_offset=value.next_offset,
            data_revision=value.data_revision,
        )
        if isinstance(value, LocalRouteSearchResult):
            return PublicLocalSearchResult(
                **data,
                departure_timezone=value.departure_timezone,
                arrival_timezone=value.arrival_timezone,
            )
        return PublicSearchResult(**data)
    data = value.model_dump(exclude={"legs", "options", "diagnostics", "connections"})
    return PublicPlan(
        **data,
        legs=[flight(r, refs) for r in value.legs],
        connections=[PublicConnection.model_validate(c.model_dump()) for c in value.connections],
        diagnostics=diagnostics(value.diagnostics),
        options=[
            PublicOption(
                flight=flight(o.flight, refs),
                connection=PublicConnection.model_validate(o.connection.model_dump()),
                tail=[flight(r, refs) for r in o.tail],
                tail_connections=[
                    PublicConnection.model_validate(c.model_dump()) for c in o.tail_connections
                ],
                complete=o.complete,
            )
            for o in value.options
        ],
    )
