"""Schedule feasibility rules shared by full search and continuation planning."""

from collections import defaultdict
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.connections import Airport, AirportTransfer, ConnectionWindow, PlanConnection
from core.models import FlightRecord, FlightStatus


@dataclass(frozen=True)
class Assessment:
    reason: str
    connection: PlanConnection | None = None
    wait_minutes: float | None = None
    minimum_minutes: int | None = None


class ConnectionPolicy:
    def __init__(
        self,
        airports: Iterable[Airport],
        transfers: Iterable[AirportTransfer],
        *,
        allow_airport_change: bool = False,
        avoid_airports: Iterable[str] = (),
    ):
        self.airports = {a.iata: a for a in airports}
        self.rules = {}
        self.avoid = frozenset(avoid_airports)
        if allow_airport_change:
            for rule in transfers:
                a, b = (
                    self.airports.get(rule.arrival_iata),
                    self.airports.get(rule.departure_iata),
                )
                if a and b and a.city_id is not None and a.city_id == b.city_id:
                    self.rules[rule.arrival_iata, rule.departure_iata] = rule
        origins, arrivals = defaultdict(set), defaultdict(set)
        for arrival, departure in self.rules:
            origins[arrival].update((arrival, departure))
            arrivals[departure].add(arrival)
        self.origin_groups = {
            a: tuple(sorted(values - self.avoid)) for a, values in origins.items()
        }
        self.arrivals_by_departure = {
            a: tuple(sorted(values - self.avoid)) for a, values in arrivals.items()
        }

    def origins(self, arrival_airport: str) -> tuple[str, ...]:
        return self.origin_groups.get(
            arrival_airport, () if arrival_airport in self.avoid else (arrival_airport,)
        )

    def minimum(
        self, arrival_airport: str, departure_airport: str, window: ConnectionWindow
    ) -> tuple[int, str] | None:
        if arrival_airport == departure_airport:
            return window.minimum_minutes, "user connection allowance"
        rule = self.rules.get((arrival_airport, departure_airport))
        if rule:
            return max(window.minimum_minutes, rule.minimum_minutes), rule.source
        return None

    def bounds(
        self,
        previous: FlightRecord,
        origin: str,
        window: ConnectionWindow,
        horizon: datetime,
    ) -> tuple[datetime, datetime] | None:
        rule = self.minimum(previous.destination_iata, origin, window)
        if not rule:
            return None
        if horizon - previous.arrival_upper < timedelta(minutes=rule[0]):
            return None
        lower = previous.arrival_upper + timedelta(minutes=rule[0])
        if window.not_before is not None:
            lower = max(lower, window.not_before)
        upper = horizon
        if (
            window.maximum_minutes is not None
            and timedelta(minutes=window.maximum_minutes) < horizon - previous.arrival_upper
        ):
            upper = previous.arrival_lower + timedelta(minutes=window.maximum_minutes)
        return lower, upper

    def assess(
        self,
        previous: FlightRecord | None,
        flight: FlightRecord,
        window: ConnectionWindow,
        seen: AbstractSet[str],
        horizon: datetime,
    ) -> Assessment:
        if flight.needs_review:
            return Assessment("airports_need_review")
        if flight.status == FlightStatus.CANCELLED:
            return Assessment("cancelled")
        if flight.arrival_upper > horizon:
            return Assessment("arrival_after_horizon")
        if flight.origin_iata in self.avoid or flight.destination_iata in self.avoid:
            return Assessment("avoided_airport")
        if flight.destination_iata in seen or (
            previous
            and flight.origin_iata != previous.destination_iata
            and flight.origin_iata in seen
        ):
            return Assessment("airport_cycle")
        if previous is None:
            return Assessment("accepted")
        rule = self.minimum(previous.destination_iata, flight.origin_iata, window)
        if rule is None:
            return Assessment("airport_change_not_permitted")
        minimum, source = rule
        wait = (flight.departure_lower - previous.arrival_upper).total_seconds() / 60
        if wait < minimum:
            return Assessment("connection_too_short", wait_minutes=wait, minimum_minutes=minimum)
        maximum_wait = (flight.departure_upper - previous.arrival_lower).total_seconds() / 60
        if window.maximum_minutes is not None and maximum_wait > window.maximum_minutes:
            return Assessment(
                "connection_too_long", wait_minutes=maximum_wait, minimum_minutes=minimum
            )
        if window.not_before is not None and flight.departure_lower < window.not_before:
            return Assessment(
                "before_departure_threshold", wait_minutes=wait, minimum_minutes=minimum
            )
        connection = PlanConnection(
            arrival_iata=previous.destination_iata,
            departure_iata=flight.origin_iata,
            wait_minutes=int(wait),
            minimum_minutes=minimum,
            airport_change=previous.destination_iata != flight.origin_iata,
            source=source,
        )
        return Assessment("accepted", connection, wait, minimum)
