"""Request-local SQL graph: read connection windows, then hydrate selected flights."""

import json
from collections import defaultdict
from datetime import timedelta
from math import inf, nextafter
from sys import intern

from core.connections import ConnectionWindow
from core.routing.index import FlightIndex
from core.routing.models import ENGINE_VERSION
from core.routing.policy import ConnectionPolicy
from core.routing.projection import ProjectedSnapshot, RoutingEdge
from core.routing.search import RouteCalculation, calculate_routes, route_score
from core.routing.snapshot import digest, flight_key
from core.storage import EPOCH, utc_us
from server.archive_queries import airport_pairs
from server.reachability import TimeEdge, reverse_reachable_flights


class DatabaseFlightIndex(FlightIndex):
    """The same routing interface as an in-memory index, backed by one read snapshot.

    Airport adjacency is deliberately relaxed: even cancelled, unreviewed and
    out-of-window facts may supply a lower bound. They can cause extra work, but
    cannot remove a valid journey. Actual candidates always use searchable_legs.
    """

    def __init__(self, connection, request, policy, basis, start, airports, transfers, revision):
        self.connection, self.request, self.basis = connection, request, basis
        self.policy, self.start = policy, start
        self.airports, self.transfers, self.revision = airports, transfers, revision
        self._tail_horizon = None
        self._tail_start = None
        self._tail_index = None
        self._tail_minimum = None
        self.rows_loaded = 0
        self.origins = set()
        self.airport_count = connection.execute("SELECT count(*) FROM airports").fetchone()[0]
        self.reverse = defaultdict(set)
        for origin, destination in airport_pairs(connection):
            if origin in policy.avoid or destination in policy.avoid:
                continue
            self.reverse[destination].add(origin)
            self.reverse[destination].update(policy.arrivals_by_departure.get(origin, ()))
        self.avoid = policy.avoid

    def for_horizon(self, horizon, window, *, start=None):
        start = max(self.start, start) if start is not None else self.start
        if (
            self._tail_index is not None
            and self._tail_start <= start
            and self._tail_horizon >= horizon
            and self._tail_minimum == window.minimum_minutes
        ):
            return self._tail_index
        # Reuse one compact temporal graph across the visible continuation
        # options. Rounding up only retains extra facts; routing still assesses
        # each trial's exact deadline. Replacing the cache bounds its lifetime.
        day = 86_400_000_000
        rounded = min(utc_us(self.request.horizon_end), ((utc_us(horizon) + day - 1) // day) * day)
        deadline = EPOCH + timedelta(microseconds=rounded)
        snapshot, loaded = _ranked_window(
            self.connection,
            self.request,
            self.airports,
            self.transfers,
            self.basis,
            deadline,
            self.revision,
            start=start,
            reachable=tuple(self.minimum_legs_to(self.request.destination)),
            window=window,
        )
        self.rows_loaded += loaded
        self._tail_index = FlightIndex(
            snapshot.records(),
            self.policy,
            deadline,
            minimum_legs=snapshot.minimum_legs,
            earliest_arrival=snapshot.earliest_arrival,
        )
        self._tail_horizon, self._tail_minimum = deadline, window.minimum_minutes
        self._tail_start = start
        return self._tail_index

    def origin_size(self, origin):
        # Counting all departures solely for a skipped-candidate statistic would
        # undo the indexed-window query. SQL-filtered rows are never decoded.
        return 0

    def window(
        self, origin, lower, upper, *, upper_inclusive=True, destinations=None, arrival_horizon=None
    ):
        if destinations is not None and not destinations:
            return iter(()), 0
        self.origins.add(origin)
        operator = "<=" if upper_inclusive else "<"
        params = {
            "basis": self.basis,
            "origin": origin,
            "lower": utc_us(lower),
            "upper": utc_us(upper),
            "horizon": utc_us(arrival_horizon or self.request.horizon_end),
            "destination": self.request.destination,
            "avoid": json.dumps(sorted(self.avoid)),
            "destinations": json.dumps(sorted(destinations)) if destinations is not None else None,
        }
        destination_clause = (
            "AND l.destination_iata IN (SELECT value FROM json_each(:destinations))"
            if destinations is not None
            else ""
        )
        query = f"""SELECT s.code,l.source_record_key,l.origin_iata,l.destination_iata,
                          l.departure_lower_us,l.departure_upper_us,
                          l.arrival_lower_us,l.arrival_upper_us
                   FROM searchable_legs l JOIN data_sources s ON s.id=l.source_id
                   WHERE l.time_basis=:basis AND l.origin_iata=:origin
                     AND l.departure_lower_us>=:lower
                     AND l.departure_lower_us{operator}:upper
                     AND l.departure_upper_us{operator}:upper
                     AND l.arrival_upper_us<=:horizon AND l.origin_iata<>:destination
                     AND NOT EXISTS (SELECT 1 FROM json_each(:avoid)
                         WHERE value IN (l.origin_iata,l.destination_iata))
                   {destination_clause}
                   ORDER BY l.departure_lower_us,s.code,l.source_record_key"""

        def flights():
            # Nested onward reads use independent cursors on the same transaction.
            # Streaming avoids keeping a second copy of each departure window.
            with_cursor = self.connection.execute(query, params)
            try:
                for row in with_cursor:
                    self.rows_loaded += 1
                    yield RoutingEdge.from_row(
                        (intern(row[0]), row[1], intern(row[2]), intern(row[3]), *row[4:]),
                        self.basis,
                    )
            finally:
                with_cursor.close()

        return flights(), 0


def project_routes(
    connection, request, airports, transfers, basis, *, anchors=(), revision=None, start=None
):
    policy = ConnectionPolicy(
        airports,
        transfers,
        allow_airport_change=request.allow_airport_change,
        avoid_airports=request.avoid_airports,
    )
    index = DatabaseFlightIndex(
        connection,
        request,
        policy,
        basis,
        start if start is not None else request.departure_start,
        airports,
        transfers,
        revision,
    )
    records = {}
    for flight in anchors:
        if flight.has_time_basis(basis):
            records[flight_key(flight)] = (
                flight.model_copy(update={"time_basis": basis}) if basis != "scheduled" else flight
            )
    return ProjectedSnapshot(
        tuple(records.values()), airports, transfers, basis, 0, index=index, data_revision=revision
    )


def _ranked_window(
    connection,
    request,
    airports,
    transfers,
    basis,
    horizon,
    revision,
    *,
    start=None,
    window=None,
    reachable=None,
):
    """A feasible top-page incumbent proves later arrivals cannot rank on this page."""
    params = dict(
        basis=basis,
        start=utc_us(start if start is not None else request.departure_start),
        horizon=utc_us(horizon),
        origin=getattr(request, "origin", None),
        destination=request.destination,
        departure_end=utc_us(request.departure_end) if hasattr(request, "departure_end") else None,
        avoid=json.dumps(request.avoid_airports),
        reachable=json.dumps(sorted(reachable)) if reachable is not None else None,
    )
    reachable_clause = (
        "AND l.destination_iata IN (SELECT value FROM json_each(:reachable))"
        if reachable is not None
        else ""
    )
    rows = [
        TimeEdge(intern(row[0]), row[1], intern(row[2]), intern(row[3]), *row[4:])
        for row in connection.execute(
            f"""SELECT s.code,l.source_record_key,l.origin_iata,l.destination_iata,
                      l.departure_lower_us,l.departure_upper_us,l.arrival_lower_us,l.arrival_upper_us
               FROM searchable_legs l JOIN data_sources s ON s.id=l.source_id
               WHERE l.time_basis=:basis AND l.departure_lower_us>=:start
                 AND l.departure_lower_us<:horizon AND l.arrival_upper_us<=:horizon
                 AND l.arrival_lower_us>=:start AND l.arrival_lower_us<=:horizon
                 AND l.origin_iata<>:destination AND (:origin IS NULL OR l.destination_iata<>:origin)
                 AND (:origin IS NULL OR l.origin_iata<>:origin OR l.departure_upper_us<:departure_end)
                 AND NOT EXISTS (SELECT 1 FROM json_each(:avoid)
                                 WHERE value IN (l.origin_iata,l.destination_iata))
                 {reachable_clause}""",
            params,
        )
    ]
    loaded = len(rows)
    policy = ConnectionPolicy(
        airports,
        transfers,
        allow_airport_change=request.allow_airport_change,
        avoid_airports=request.avoid_airports,
    )
    window = window or ConnectionWindow(
        minimum_minutes=request.minimum_connection_minutes,
        maximum_minutes=request.maximum_connection_minutes,
    )
    reachable = reverse_reachable_flights(
        rows, request.destination, policy, window, params["start"]
    )
    rows = list(reachable.edges.values())
    minimum_legs = reachable.leg_distance
    del reachable
    bounds = reverse_reachable_flights(
        rows, request.destination, policy, window, params["start"], earliest=True
    ).arrival_bound
    arrivals = {value: EPOCH + timedelta(microseconds=value) for value in set(bounds.values())}
    return ProjectedSnapshot(
        tuple(RoutingEdge.from_row(row, basis) for row in rows),
        airports,
        transfers,
        basis,
        len({row.origin for row in rows}),
        minimum_legs=minimum_legs,
        earliest_arrival={key: arrivals[value] for key, value in bounds.items()},
        data_revision=revision,
    ), loaded


def calculate_database_routes(
    connection, request, airports, transfers, basis, *, revision, include_replay_identity
):
    snapshot = project_routes(connection, request, airports, transfers, basis, revision=revision)
    if request.ranking == "fewest_legs" or request.limits.max_candidates is not None:
        return calculate_routes(
            request, snapshot, best_first=True, include_replay_identity=include_replay_identity
        )
    retained = request.offset + request.limit + 1
    seed_request = request.model_copy(
        update={"ranking": "fewest_legs", "offset": 0, "limit": retained}
    )
    seed = calculate_routes(
        seed_request, snapshot, best_first=True, include_replay_identity=include_replay_identity
    )
    if len(seed.paths) < retained:
        # Fewer than a whole page plus its sentinel exist: the seed contains
        # every feasible route, so reranking these is already the exact answer.
        paths = sorted(seed.paths, key=lambda item: route_score(item.legs, request.ranking))
        seed.diagnostics.ranking = request.ranking
        if seed.diagnostics.request_id:
            seed.diagnostics.request_id = digest(
                {
                    "engine": ENGINE_VERSION,
                    "snapshot": seed.diagnostics.snapshot_id,
                    "query": request.model_dump(mode="json"),
                }
            )
        seed.diagnostics.result_limit_reached = False
        return RouteCalculation(
            request,
            paths[request.offset : request.offset + request.limit],
            seed.airports,
            seed.diagnostics,
        )
    if request.ranking == "earliest_arrival":
        horizon = max(item.legs[-1].arrival_upper for item in seed.paths)
    else:
        elapsed = max(route_score(item.legs, "shortest_elapsed")[0] for item in seed.paths)
        duration = timedelta(seconds=nextafter(elapsed, inf))
        horizon = request.horizon_end
        if duration < horizon - request.departure_end:
            horizon = request.departure_end + duration
    bounded, loaded = _ranked_window(
        connection,
        request,
        airports,
        transfers,
        basis,
        horizon,
        revision,
        reachable=tuple(snapshot.index.minimum_legs_to(request.destination)),
    )
    result = calculate_routes(
        request, bounded, best_first=True, include_replay_identity=include_replay_identity
    )
    result.diagnostics.stats.rows_loaded = seed.diagnostics.stats.rows_loaded + loaded
    result.diagnostics.stats.candidates_checked += seed.diagnostics.stats.candidates_checked
    result.diagnostics.stats.states_expanded += seed.diagnostics.stats.states_expanded
    return result
