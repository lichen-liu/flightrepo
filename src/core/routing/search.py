"""Enumerate bounded simple paths; retain exact top-k under an explicit ranking."""

from dataclasses import dataclass
from heapq import heappop, heappush, heapreplace
from itertools import count

from core.connections import ConnectionWindow
from core.routing.models import RouteSearchRequest, SearchDiagnostics
from core.routing.policy import Assessment, ConnectionPolicy
from core.routing.projection import ProjectedSnapshot
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


@dataclass
class RouteCalculation:
    query: RouteSearchRequest
    paths: list
    airports: list
    diagnostics: SearchDiagnostics

    @property
    def flights(self):
        return [flight for item in self.paths for flight in item.legs]

    def materialize(self, records=None):
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
            for item in self.paths
            for legs, links in [
                (
                    [records[flight_key(f)] for f in item.legs]
                    if records is not None
                    else item.legs,
                    item.connections,
                )
            ]
        ]
        return RouteSearchResult(
            query=self.query,
            items=items,
            airports=self.airports,
            diagnostics=self.diagnostics,
        )


def calculate_routes(
    request: RouteSearchRequest,
    snapshot: RoutingSnapshot | ProjectedSnapshot,
    *,
    best_first=False,
    include_replay_identity=True,
) -> RouteCalculation:
    snapshot = snapshot.with_time_basis(request.time_basis)
    policy = ConnectionPolicy(
        snapshot.airports,
        snapshot.transfers,
        allow_airport_change=request.allow_airport_change,
        avoid_airports=request.avoid_airports,
    )
    session = RoutingSession(
        snapshot,
        request,
        policy,
        request.ranking,
        include_replay_identity=include_replay_identity,
    )
    window = ConnectionWindow(
        minimum_minutes=request.minimum_connection_minutes,
        maximum_minutes=request.maximum_connection_minutes,
    )
    best = []

    def frame(legs, connections, seen, via_progress):
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
        return legs, connections, seen, via_progress, iter(candidates)

    def extend(legs, connections, seen, via_progress, flight):
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
                flight.destination_iata == request.destination or len(legs) + 1 == session.max_legs
            )
            and not session.via_satisfied(next_via)
        ):
            decision = Assessment("required_via_not_visited")
        if decision.reason == "accepted" and not session.reachable(
            flight.destination_iata, session.max_legs - len(legs) - 1
        ):
            decision = Assessment(
                "destination_unreachable"
                if request.max_legs is None
                else "destination_unreachable_within_legs"
            )
        session.record(legs, flight, decision)
        if decision.reason != "accepted":
            return None
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
        elif len(next_legs) < session.max_legs:
            return next_legs, links, next_seen, next_via
        return None

    def lower_score(legs):
        # Projected graphs supply time-compatible suffix bounds. Other snapshots
        # use geographic bounds. Both ignore cycles and required vias, which can
        # only make these optimistic compared with a valid complete journey.
        earliest = getattr(snapshot, "earliest_arrival", {}).get(
            flight_key(legs[-1]),
            legs[-1].arrival_upper,
        )
        elapsed = (earliest - legs[0].departure_lower).total_seconds()
        suffix = getattr(snapshot, "minimum_legs", {}).get(flight_key(legs[-1]))
        minimum_legs = (
            len(legs) - 1 + suffix
            if suffix is not None
            else len(legs) + session.distance.get(legs[-1].destination_iata, session.max_legs + 1)
        )
        arrival, departure = earliest, legs[0].departure_lower
        identity = tuple(flight_key(f) for f in legs)
        if request.ranking == "shortest_elapsed":
            return elapsed, minimum_legs, arrival, departure, identity
        if request.ranking == "earliest_arrival":
            return arrival, minimum_legs, elapsed, departure, identity
        return minimum_legs, elapsed, departure, arrival, identity

    try:
        if best_first:
            serial = count()
            pending = [((), next(serial), ([], [], {request.origin}, 0))]
            while pending:
                lower, _, state = heappop(pending)
                if len(best) == request.limit and lower >= best[0].score:
                    # No unexpanded path can improve the retained ranking.
                    break
                legs, connections, seen, via_progress, candidates = frame(*state)
                for flight in candidates:
                    child = extend(legs, connections, seen, via_progress, flight)
                    if child is not None:
                        bound = lower_score(child[0])
                        if len(best) < request.limit or bound < best[0].score:
                            heappush(pending, (bound, next(serial), child))
        else:
            stack = [frame([], [], {request.origin}, 0)]
            while stack:
                legs, connections, seen, via_progress, candidates = stack[-1]
                try:
                    flight = next(candidates)
                except StopIteration:
                    stack.pop()
                    continue
                child = extend(legs, connections, seen, via_progress, flight)
                if child is not None:
                    stack.append(frame(*child))
    except SearchBudgetReached:
        pass
    paths = sorted(best, key=lambda item: item.score)
    used_airports = {request.origin, request.destination}
    used_airports.update(
        code
        for item in paths
        for flight in item.legs
        for code in (flight.origin_iata, flight.destination_iata)
    )
    return RouteCalculation(
        query=request,
        paths=paths,
        airports=sorted(
            (a for a in snapshot.airports if a.iata in used_airports), key=lambda a: a.iata
        ),
        diagnostics=session.diagnostics(
            result_limit_reached=session.stats.routes_found > request.limit
        ),
    )


def search_routes(
    request: RouteSearchRequest, snapshot: RoutingSnapshot, *, best_first=False
) -> RouteSearchResult:
    return calculate_routes(request, snapshot, best_first=best_first).materialize()
