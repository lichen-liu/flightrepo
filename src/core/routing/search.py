"""Enumerate bounded simple paths; retain exact top-k under an explicit ranking."""

from dataclasses import dataclass
from heapq import heappush, heapreplace

from core.connections import ConnectionWindow
from core.routing.models import RouteSearchRequest
from core.routing.policy import Assessment, ConnectionPolicy
from core.routing.results import RouteItinerary, RouteSearchResult
from core.routing.session import RoutingSession, SearchBudgetReached
from core.routing.snapshot import RoutingSnapshot, digest, flight_key


@dataclass(slots=True)
class RankedPath:
    score: tuple
    legs: list
    connections: list

    def __lt__(self, other):
        # Reverse only the heap comparison: its root is the worst retained path.
        return self.score > other.score


def route_score(legs, ranking):
    elapsed = (legs[-1].arrival_upper - legs[0].departure_lower).total_seconds()
    identity = tuple(flight_key(f) for f in legs)
    arrival, departure, count = (
        legs[-1].arrival_upper,
        legs[0].departure_lower,
        len(legs),
    )
    if ranking == "shortest_elapsed":
        return elapsed, count, arrival, departure, identity
    if ranking == "earliest_arrival":
        return arrival, count, elapsed, departure, identity
    return count, elapsed, departure, arrival, identity


def search_routes(request: RouteSearchRequest, snapshot: RoutingSnapshot) -> RouteSearchResult:
    snapshot = snapshot.with_time_basis(request.time_basis)
    policy = ConnectionPolicy(
        snapshot.airports,
        snapshot.transfers,
        allow_airport_change=request.allow_airport_change,
        avoid_airports=request.avoid_airports,
    )
    session = RoutingSession(snapshot, request, policy, request.ranking)
    window = ConnectionWindow(
        minimum_minutes=request.minimum_connection_minutes,
        maximum_minutes=request.maximum_connection_minutes,
    )
    best = []

    def walk(legs, connections, seen, via_progress):
        session.stats.states_expanded += 1
        if legs:
            candidates = session.onward(legs[-1], window)
        else:
            candidates, skipped = session.index.window(
                request.origin,
                request.departure_start,
                request.departure_end,
                upper_inclusive=False,
            )
            session.stats.candidates_skipped_by_time += skipped
        for flight in candidates:
            session.take()
            decision = policy.assess(
                legs[-1] if legs else None, flight, window, seen, request.horizon_end
            )
            if not legs and flight.departure_upper >= request.departure_end:
                decision = Assessment("departure_after_window")
            next_seen = seen | {flight.origin_iata, flight.destination_iata}
            next_via = session.advance_via(via_progress, flight)
            if decision.reason == "accepted" and next_via is None:
                decision = Assessment("required_via_out_of_order")
            if (
                decision.reason == "accepted"
                and (
                    flight.destination_iata == request.destination
                    or len(legs) + 1 == request.max_legs
                )
                and not session.via_satisfied(next_via)
            ):
                decision = Assessment("required_via_not_visited")
            if decision.reason == "accepted" and not session.reachable(
                flight.destination_iata, request.max_legs - len(legs) - 1
            ):
                decision = Assessment("destination_unreachable_within_legs")
            session.record(legs, flight, decision)
            if decision.reason != "accepted":
                continue
            next_legs = [*legs, flight]
            links = [
                *connections,
                *([decision.connection] if decision.connection else []),
            ]
            if flight.destination_iata == request.destination:
                session.stats.routes_found += 1
                item = RankedPath(route_score(next_legs, request.ranking), next_legs, links)
                if len(best) < request.limit:
                    heappush(best, item)
                elif item.score < best[0].score:
                    heapreplace(best, item)
            elif len(next_legs) < request.max_legs:
                walk(
                    next_legs,
                    links,
                    next_seen,
                    next_via,
                )

    try:
        walk([], [], {request.origin}, 0)
    except SearchBudgetReached:
        pass
    items = [
        RouteItinerary(
            itinerary_id=digest([flight_key(f) for f in legs]),
            legs=legs,
            connections=links,
            departure=legs[0].departure_lower,
            arrival=legs[-1].arrival_upper,
            duration_minutes=int(
                (legs[-1].arrival_upper - legs[0].departure_lower).total_seconds() / 60
            ),
        )
        for item in sorted(best, key=lambda item: item.score)
        for legs, links in [(item.legs, item.connections)]
    ]
    used_airports = {request.origin, request.destination}
    used_airports.update(
        code
        for item in items
        for flight in item.legs
        for code in (flight.origin_iata, flight.destination_iata)
    )
    return RouteSearchResult(
        query=request,
        items=items,
        airports=sorted(
            (a for a in snapshot.airports if a.iata in used_airports),
            key=lambda a: a.iata,
        ),
        diagnostics=session.diagnostics(
            result_limit_reached=session.stats.routes_found > request.limit
        ),
    )
