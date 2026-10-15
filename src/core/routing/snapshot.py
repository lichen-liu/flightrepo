"""One supplied set of facts; its content digest identifies exactly what was read."""

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, Field

from core.connections import Airport, AirportTransfer
from core.models import FlightRecord
from core.routing.models import RoutingError


def flight_key(flight: FlightRecord) -> tuple[str, str]:
    return flight.provider, flight.provider_id


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class SnapshotDocument(BaseModel):
    flights: list[FlightRecord] = Field(max_length=20_005)
    airports: list[Airport]
    transfers: list[AirportTransfer]
    data_truncated: bool
    origins_queried: int = Field(ge=0)


@dataclass(frozen=True)
class RoutingSnapshot:
    flights: tuple[FlightRecord, ...]
    airports: tuple[Airport, ...] = ()
    transfers: tuple[AirportTransfer, ...] = ()
    data_truncated: bool = False
    origins_queried: int = 0

    def with_time_basis(self, basis):
        if basis is None:
            basis = next(
                (
                    b
                    for b in ("scheduled", "observed", "actual")
                    if any(f.has_time_basis(b) and f.status != "cancelled" for f in self.flights)
                ),
                "scheduled",
            )
        selected = tuple(
            f
            if basis == "scheduled" and f.time_basis is None
            else f.model_copy(update={"time_basis": basis})
            for f in self.flights
            if f.has_time_basis(basis)
        )
        if selected == self.flights:
            return self
        return RoutingSnapshot(
            selected, self.airports, self.transfers, self.data_truncated, self.origins_queried
        )

    def to_data(self):
        return SnapshotDocument(
            flights=list(self.records().values()),
            airports=list(self.airports),
            transfers=list(self.transfers),
            data_truncated=self.data_truncated,
            origins_queried=self.origins_queried,
        ).model_dump(mode="json")

    @classmethod
    def from_data(cls, data):
        facts = SnapshotDocument.model_validate(data)
        return cls(
            tuple(facts.flights),
            tuple(facts.airports),
            tuple(facts.transfers),
            facts.data_truncated,
            facts.origins_queried,
        )

    def __post_init__(self):
        for rows, key in [
            (self.airports, lambda a: a.iata),
            (self.transfers, lambda t: (t.arrival_iata, t.departure_iata)),
        ]:
            identities = {}
            for row in rows:
                identity = key(row)
                if identity in identities and identities[identity] != row:
                    raise RoutingError(
                        "conflicting_metadata_identity",
                        f"conflicting metadata for {identity}",
                    )
                identities[identity] = row

    def records(self) -> dict[tuple[str, str], FlightRecord]:
        records = {}
        for flight in self.flights:
            key = flight_key(flight)
            if key in records and records[key] != flight:
                raise RoutingError("conflicting_flight_identity", f"conflicting records for {key}")
            records[key] = flight
        return records

    @property
    def snapshot_id(self) -> str:
        return digest(
            {
                "flights": [f.model_dump(mode="json") for _, f in sorted(self.records().items())],
                "airports": [
                    a.model_dump(mode="json") for a in sorted(self.airports, key=lambda a: a.iata)
                ],
                "transfers": [
                    t.model_dump(mode="json")
                    for t in sorted(
                        self.transfers, key=lambda t: (t.arrival_iata, t.departure_iata)
                    )
                ],
                "data_truncated": self.data_truncated,
            }
        )
