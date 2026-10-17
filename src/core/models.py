from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from core.admission import blocked_for_search
from core.quality import describe_quality
from core.routing.models import airport_code, airport_codes, utc_instant


class FlightStatus(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    LANDED = "landed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RouteVerification(BaseModel):
    """An undated route-reference audit result, never a human or timing approval."""

    source: Literal["vrs"] = "vrs"
    match_kind: Literal["exact", "adjacent", "different", "incomplete", "missing"]
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    callsign: str | None = Field(default=None, min_length=1)
    operator_icao: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    operator_name: str | None = Field(default=None, min_length=1)
    reference_airports: list[str | None] | None = Field(default=None, max_length=64)
    origin_iata: str = Field(pattern=r"^[A-Z]{3}$")
    destination_iata: str = Field(pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def matched_identity(self):
        if self.match_kind in {"exact", "adjacent"} and not (self.callsign or "").strip():
            raise ValueError("A positive route match requires a callsign")
        return self


class FlightRecord(BaseModel):
    provider: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    flight_number: str | None = Field(default=None, min_length=1)
    airline_iata: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2}$")
    origin_iata: str = Field(pattern=r"^[A-Z]{3}$")
    destination_iata: str = Field(pattern=r"^[A-Z]{3}$")
    departure_scheduled: datetime | None = None
    arrival_scheduled: datetime | None = None
    departure_actual: datetime | None = None
    arrival_actual: datetime | None = None
    status: FlightStatus = FlightStatus.UNKNOWN
    codeshares: list[str] = Field(default_factory=list)
    fetched_at: datetime
    aircraft_type_code: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2,8}$")
    callsign: str | None = Field(default=None, min_length=1)
    operator_icao: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    operator_name: str | None = Field(default=None, min_length=1)
    evidence_kind: Literal[
        "source_reported", "inferred_ground_brackets", "inferred_airport_proximity", "manual"
    ] = "source_reported"
    review_reasons: list[
        Literal[
            "airborne_airport_estimate",
            "observation_gap",
            "callsign_variation",
            "implausible_airport_timing",
        ]
    ] = Field(default_factory=list)
    itinerary_reviewed: bool = False
    route_verification: RouteVerification | None = None

    @model_validator(mode="after")
    def current_operator_reference(self):
        import re

        callsign = (self.callsign or "").strip().upper()
        if (
            not self.operator_name
            or not self.operator_icao
            or not re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9]{0,4}", callsign)
            or callsign[:3] != self.operator_icao
        ):
            self.operator_name = self.operator_icao = None
        return self

    @property
    def current_route_reference(self):
        proof = self.route_verification
        return (
            proof
            if (
                proof is not None
                and proof.callsign == ((self.callsign or "").strip().upper() or None)
                and proof.origin_iata == self.origin_iata
                and proof.destination_iata == self.destination_iata
            )
            else None
        )

    @property
    def route_verified(self):
        proof = self.current_route_reference
        return bool(proof and proof.callsign and proof.match_kind in {"exact", "adjacent"})

    @property
    def quality(self):
        proof = self.current_route_reference
        data = describe_quality(
            reasons=self.review_reasons,
            evidence_kind=self.evidence_kind,
            reference_result=proof.match_kind if proof else None,
            reviewed=self.itinerary_reviewed,
            observed=self.effective_time_basis == "observed",
            aircraft_type=self.aircraft_type_code,
            provider=self.provider,
        )
        recommended = not self.itinerary_reviewed and data["level"] in {"uncertain", "conflict"}
        data.update(
            review_recommended=recommended,
            review_status="manual"
            if self.itinerary_reviewed
            else "recommended"
            if recommended
            else "not_requested",
        )
        return data

    @property
    def needs_review(self):
        return self.quality["review_recommended"]

    @property
    def blocked_for_search(self):
        return blocked_for_search(self.review_reasons, self.itinerary_reviewed)

    departure_observed_lower: datetime | None = None
    departure_observed_upper: datetime | None = None
    arrival_observed_lower: datetime | None = None
    arrival_observed_upper: datetime | None = None
    departure_actual_kind: Literal["gate_out", "takeoff", "unknown"] | None = None
    arrival_actual_kind: Literal["gate_in", "landing", "unknown"] | None = None
    time_basis: Literal["scheduled", "actual", "observed"] | None = None

    def has_time_basis(self, basis):
        if basis == "observed":
            return self.departure_observed_lower is not None
        return (
            getattr(self, f"departure_{basis}") is not None
            and getattr(self, f"arrival_{basis}") is not None
        )

    @property
    def effective_time_basis(self):
        if self.time_basis is not None:
            return self.time_basis
        if self.departure_scheduled is not None and self.arrival_scheduled is not None:
            return "scheduled"
        if self.departure_observed_lower is not None:
            return "observed"
        if self.departure_actual is not None or self.arrival_actual is not None:
            return "actual"
        return "scheduled"

    def _bound(self, event, side):
        basis = self.effective_time_basis
        name = f"{event}_observed_{side}" if basis == "observed" else f"{event}_{basis}"
        value = getattr(self, name)
        if value is None:
            raise ValueError(f"Flight has no complete {basis} time evidence")
        return value

    @property
    def departure_lower(self):
        return self._bound("departure", "lower")

    @property
    def departure_upper(self):
        return self._bound("departure", "upper")

    @property
    def arrival_lower(self):
        return self._bound("arrival", "lower")

    @property
    def arrival_upper(self):
        return self._bound("arrival", "upper")

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        quality = self.quality
        if quality["labels"]:
            data["quality"] = quality
        if self.route_verification is None:
            data.pop("route_verification", None)
        else:
            data["route_verified"] = self.route_verified
        if not self.review_reasons:
            data.pop("review_reasons", None)
        if not self.itinerary_reviewed:
            data.pop("itinerary_reviewed", None)
        if data.get("aircraft_type_code") is None:
            data.pop("aircraft_type_code", None)
        for name in (
            "callsign",
            "operator_icao",
            "operator_name",
            "departure_observed_lower",
            "departure_observed_upper",
            "arrival_observed_lower",
            "arrival_observed_upper",
            "departure_actual_kind",
            "arrival_actual_kind",
            "time_basis",
        ):
            if data.get(name) is None:
                data.pop(name, None)
        if self.evidence_kind == "source_reported":
            data.pop("evidence_kind", None)
        if self.effective_time_basis != "scheduled":
            data["time_basis"] = self.effective_time_basis
            if self.has_time_basis(self.effective_time_basis):
                for event in ("departure", "arrival"):
                    for side in ("lower", "upper"):
                        value = getattr(self, f"{event}_{side}")
                        data[f"{event}_{side}"] = (
                            value.isoformat() if isinstance(data["fetched_at"], str) else value
                        )
            if self.effective_time_basis == "actual":
                for event in ("departure", "arrival"):
                    if getattr(self, f"{event}_actual") is not None:
                        data[f"{event}_actual_kind"] = (
                            getattr(self, f"{event}_actual_kind") or "unknown"
                        )
        return data

    _codes = field_validator("origin_iata", "destination_iata", mode="before")(airport_code)

    @field_validator("airline_iata", mode="before")
    @classmethod
    def carrier_code(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("airline IATA code must be a string")
        return value.strip().upper() or None

    @field_validator("flight_number", mode="before")
    @classmethod
    def nonblank_number(cls, value):
        if value is None:
            return None
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
            "departure_observed_lower",
            "departure_observed_upper",
            "arrival_observed_lower",
            "arrival_observed_upper",
            "fetched_at",
        ):
            value = getattr(self, field)
            if value is not None:
                setattr(self, field, utc_instant(value))
        if self.origin_iata == self.destination_iata:
            raise ValueError("origin and destination must differ")
        for basis in ("scheduled", "actual"):
            departure, arrival = (
                getattr(self, f"departure_{basis}"),
                getattr(self, f"arrival_{basis}"),
            )
            if departure is not None and arrival is not None and arrival <= departure:
                raise ValueError("arrival must be after departure")
        for event in ("departure", "arrival"):
            if (
                getattr(self, f"{event}_actual_kind") is not None
                and getattr(self, f"{event}_actual") is None
            ):
                raise ValueError("actual event kind requires its corresponding time")
        observed = [
            getattr(self, name)
            for name in (
                "departure_observed_lower",
                "departure_observed_upper",
                "arrival_observed_lower",
                "arrival_observed_upper",
            )
        ]
        if any(v is not None for v in observed) and (
            any(v is None for v in observed)
            or not observed[0] <= observed[1] < observed[2] <= observed[3]
        ):
            raise ValueError("observed time bounds must be complete and ordered")
        if self.evidence_kind.startswith("inferred_") and observed[0] is None:
            raise ValueError("inferred itinerary evidence requires observed bounds")
        if not any(
            getattr(self, name) is not None
            for name in (
                "departure_scheduled",
                "arrival_scheduled",
                "departure_actual",
                "arrival_actual",
                "departure_observed_lower",
            )
        ):
            raise ValueError("a flight requires time evidence")
        if self.time_basis is None and self.effective_time_basis != "scheduled":
            self.time_basis = self.effective_time_basis
        if self.effective_time_basis == "actual":
            for event in ("departure", "arrival"):
                if (
                    getattr(self, f"{event}_actual") is not None
                    and getattr(self, f"{event}_actual_kind") is None
                ):
                    setattr(self, f"{event}_actual_kind", "unknown")
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
