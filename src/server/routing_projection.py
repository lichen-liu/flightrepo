"""Read only routing columns; SQL removes unrelated airport components."""

import json
from datetime import timedelta
from sys import intern

from core.connections import ConnectionWindow
from core.routing.policy import ConnectionPolicy
from core.routing.projection import ProjectedSnapshot, RoutingEdge
from core.routing.snapshot import flight_key
from core.storage import EPOCH, utc_us
from server.reachability import TimeEdge, reverse_reachable_flights


def project_routes(connection, request, airports, transfers, basis, *, prefix=(), anchors=()):
    policy = ConnectionPolicy(
        airports,
        transfers,
        allow_airport_change=request.allow_airport_change,
        avoid_airports=request.avoid_airports,
    )
    start = prefix[-1].arrival_upper if prefix else request.departure_start
    roots = policy.origins(prefix[-1].destination_iata) if prefix else (request.origin,)
    clauses = [
        "l.time_basis=:basis",
        "l.departure_lower_us>=:start",
        "l.arrival_upper_us<=:horizon",
        "l.origin_iata<>:destination",
    ]
    params = {
        "basis": basis,
        "start": utc_us(start),
        "horizon": utc_us(request.horizon_end),
        "destination": request.destination,
        "roots": json.dumps(roots),
        "transfers": json.dumps(sorted(policy.rules)),
    }
    if not prefix:
        clauses.extend(
            [
                "(l.origin_iata<>:origin OR l.departure_upper_us<:departure_end)",
                "l.destination_iata<>:origin",
            ]
        )
        params.update(origin=request.origin, departure_end=utc_us(request.departure_end))
    if policy.avoid:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM json_each(:avoid) WHERE value IN (l.origin_iata,l.destination_iata))"
        )
        params["avoid"] = json.dumps(sorted(policy.avoid))
    # Reachability is conservative: transfer arcs can retain extra flights, but
    # exact timing, via order, cycles and transfer rules remain engine decisions.
    # UNION (not UNION ALL) makes each airport enter a traversal only once.
    query = f"""WITH RECURSIVE
        window AS MATERIALIZED (
            SELECT l.source_id,l.source_record_key,l.origin_iata,l.destination_iata,
                   l.departure_lower_us,l.departure_upper_us,l.arrival_lower_us,l.arrival_upper_us
            FROM searchable_legs l WHERE {" AND ".join(clauses)}
        ),
        links(a,b) AS MATERIALIZED (
            SELECT origin_iata,destination_iata FROM window
            UNION SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]')
                  FROM json_each(:transfers)
        ),
        forward(airport) AS (
            SELECT value FROM json_each(:roots)
            UNION SELECT b FROM links JOIN forward ON a=airport WHERE airport<>:destination
        ),
        backward(airport) AS (
            VALUES (:destination)
            UNION SELECT a FROM links JOIN backward ON b=airport
        )
        SELECT s.code,w.source_record_key,w.origin_iata,w.destination_iata,
               w.departure_lower_us,w.departure_upper_us,w.arrival_lower_us,w.arrival_upper_us
        FROM window w JOIN data_sources s ON s.id=w.source_id
        JOIN forward f ON f.airport=w.origin_iata
        {"" if prefix else "JOIN backward b ON b.airport=w.destination_iata"}
        ORDER BY s.code,w.source_record_key"""
    rows = []
    for row in connection.execute(query, params):
        values = (intern(row[0]), row[1], intern(row[2]), intern(row[3]), *row[4:])
        rows.append(TimeEdge(*values))
    minimum_legs = {}
    earliest_arrival = {}
    if not prefix:
        reachable = reverse_reachable_flights(
            rows,
            request.destination,
            policy,
            ConnectionWindow(
                minimum_minutes=request.minimum_connection_minutes,
                maximum_minutes=request.maximum_connection_minutes,
            ),
            utc_us(start),
        )
        rows = list(reachable.edges.values())
        minimum_legs = reachable.leg_distance
        del reachable
        bounds = reverse_reachable_flights(
            rows,
            request.destination,
            policy,
            ConnectionWindow(
                minimum_minutes=request.minimum_connection_minutes,
                maximum_minutes=request.maximum_connection_minutes,
            ),
            utc_us(start),
            earliest=True,
        ).arrival_bound
        arrivals = {value: EPOCH + timedelta(microseconds=value) for value in set(bounds.values())}
        earliest_arrival = {key: arrivals[value] for key, value in bounds.items()}
    edges = [RoutingEdge.from_row(row, basis) for row in rows]
    if anchors:
        records = {flight_key(edge): edge for edge in edges}
        for flight in anchors:
            if flight.has_time_basis(basis):
                records[flight_key(flight)] = (
                    flight.model_copy(update={"time_basis": basis})
                    if basis != "scheduled"
                    else flight
                )
        edges = list(records.values())
    return ProjectedSnapshot(
        tuple(edges),
        airports,
        transfers,
        basis,
        len({edge.origin_iata for edge in edges}),
        minimum_legs=minimum_legs,
        earliest_arrival=earliest_arrival,
    )
