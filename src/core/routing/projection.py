"""Compact routing facts, deliberately separate from display/source metadata."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cached_property
from typing import ClassVar

from core.models import FlightStatus
from core.routing.models import RoutingError
from core.routing.snapshot import flight_key

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RoutingEdge:
    provider: str
    provider_id: str
    origin_iata: str
    destination_iata: str
    departure_lower: datetime
    departure_upper: datetime
    arrival_lower: datetime
    arrival_upper: datetime
    time_basis: str
    # SQL searchable_legs supplies only admitted, non-cancelled evidence.
    blocked_for_search: ClassVar[bool] = False
    needs_review: ClassVar[bool] = False
    status: ClassVar[FlightStatus] = FlightStatus.UNKNOWN

    @classmethod
    def from_row(cls, row, basis):
        provider, key, origin, destination, dl, du, al, au = row
        departure = EPOCH + timedelta(microseconds=dl)
        arrival = EPOCH + timedelta(microseconds=al)
        return cls(
            provider,
            key,
            origin,
            destination,
            departure,
            departure if du == dl else EPOCH + timedelta(microseconds=du),
            arrival,
            arrival if au == al else EPOCH + timedelta(microseconds=au),
            basis,
        )

    @property
    def effective_time_basis(self):
        return self.time_basis

    def has_time_basis(self, basis):
        return basis == self.time_basis


@dataclass(frozen=True)
class ProjectedSnapshot:
    flights: tuple
    airports: tuple
    transfers: tuple
    time_basis: str
    origins_queried: int
    data_truncated: bool = False
    minimum_legs: dict = field(default_factory=dict)
    earliest_arrival: dict = field(default_factory=dict)

    def with_time_basis(self, basis):
        if basis not in (None, self.time_basis):
            raise RoutingError("projection_time_basis_mismatch", "Reload the requested time basis")
        return self

    @cached_property
    def _records(self):
        return {flight_key(flight): flight for flight in self.flights}

    def records(self):
        return self._records

    @cached_property
    def snapshot_id(self):
        # Hash only calculation inputs. Airline names, codeshares and source
        # evidence are fetched after routing and never enter this large graph.
        value = hashlib.sha256(b"routing-projection-v1\n")
        value.update(
            json.dumps(
                {
                    "basis": self.time_basis,
                    "airports": [
                        (a.iata, a.city_id) for a in sorted(self.airports, key=lambda a: a.iata)
                    ],
                    "transfers": [
                        t.model_dump()
                        for t in sorted(
                            self.transfers, key=lambda t: (t.arrival_iata, t.departure_iata)
                        )
                    ],
                    "truncated": self.data_truncated,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        for key, flight in sorted(self.records().items()):
            value.update(
                json.dumps(
                    (
                        key,
                        flight.origin_iata,
                        flight.destination_iata,
                        flight.departure_lower.isoformat(),
                        flight.departure_upper.isoformat(),
                        flight.arrival_lower.isoformat(),
                        flight.arrival_upper.isoformat(),
                        flight.needs_review,
                        flight.status,
                    ),
                    separators=(",", ":"),
                ).encode()
            )
            value.update(b"\n")
        return value.hexdigest()
