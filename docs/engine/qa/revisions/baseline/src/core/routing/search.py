"""Enumerate bounded simple paths; retain exact top-k under an explicit ranking."""
from core.connections import ConnectionWindow
from core.routing.policy import Assessment, ConnectionPolicy
from core.routing.models import RouteSearchRequest
from core.routing.results import RouteItinerary, RouteSearchResult
from core.routing.session import RoutingSession, SearchBudgetReached
from core.routing.snapshot import RoutingSnapshot, digest, flight_key


def route_score(legs, ranking):
    elapsed = (legs[-1].arrival_scheduled - legs[0].departure_scheduled).total_seconds()
    identity = tuple(flight_key(f) for f in legs)
    arrival, departure, count = legs[-1].arrival_scheduled, legs[0].departure_scheduled, len(legs)
    if ranking == 'shortest_elapsed':
        return elapsed, count, arrival, departure, identity
    if ranking == 'earliest_arrival':
        return arrival, count, elapsed, departure, identity
    return count, elapsed, departure, arrival, identity


def search_routes(request: RouteSearchRequest, snapshot: RoutingSnapshot) -> RouteSearchResult:
    policy = ConnectionPolicy(snapshot.airports, snapshot.transfers,
                              allow_airport_change=request.allow_airport_change,
                              avoid_airports=request.avoid_airports)
    session = RoutingSession(snapshot, request, policy, request.ranking)
    window = ConnectionWindow(minimum_minutes=request.minimum_connection_minutes,
                              maximum_minutes=request.maximum_connection_minutes)
    best = []

    def walk(legs, connections, seen):
        session.stats.states_expanded += 1
        if legs:
            candidates = session.onward(legs[-1], window)
        else:
            candidates, skipped = session.index.window(request.origin, request.departure_start,
                                                       request.departure_end, upper_inclusive=False)
            session.stats.candidates_skipped_by_time += skipped
        for flight in candidates:
            session.take()
            decision = policy.assess(legs[-1] if legs else None, flight, window, seen, request.horizon_end)
            if decision.reason == 'accepted' and not session.reachable(flight.destination_iata, request.max_legs-len(legs)-1):
                decision = Assessment('destination_unreachable_within_legs')
            session.record(legs, flight, decision)
            if decision.reason != 'accepted':
                continue
            next_legs = [*legs, flight]
            links = [*connections, *([decision.connection] if decision.connection else [])]
            if flight.destination_iata == request.destination:
                session.stats.routes_found += 1
                # At most limit+1 paths are retained; completed paths never stop enumeration.
                best.append((route_score(next_legs, request.ranking), next_legs, links))
                best.sort(key=lambda item: item[0])
                if len(best) > request.limit:
                    best.pop()
            elif len(next_legs) < request.max_legs:
                walk(next_legs, links, seen | {flight.origin_iata, flight.destination_iata})

    try:
        walk([], [], {request.origin})
    except SearchBudgetReached:
        pass
    items = [RouteItinerary(itinerary_id=digest([flight_key(f) for f in legs]), legs=legs,
                            connections=links, departure=legs[0].departure_scheduled,
                            arrival=legs[-1].arrival_scheduled,
                            duration_minutes=int((legs[-1].arrival_scheduled-legs[0].departure_scheduled).total_seconds()/60))
             for _, legs, links in best]
    return RouteSearchResult(query=request, items=items,
                             diagnostics=session.diagnostics(result_limit_reached=session.stats.routes_found > request.limit))
