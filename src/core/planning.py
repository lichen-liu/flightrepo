"""Exact-prefix continuation planning using the shared schedule routing rules."""

from dataclasses import dataclass
from datetime import datetime

from core.connections import (
    ConnectionWindow,
    ContinuationOption,
    PlanConnection,
    RebuildRequest,
    RebuiltPlan,
)
from core.models import FlightRecord
from core.routing.index import departure_key
from core.routing.models import (
    RoutingError,
    SearchDiagnostics,
    required_via_airports,
)
from core.routing.policy import Assessment, ConnectionPolicy
from core.routing.projection import ProjectedSnapshot, RoutingEdge
from core.routing.session import RoutingSession, SearchBudgetReached
from core.routing.snapshot import RoutingSnapshot, flight_key
from core.routing.tail import earliest_tail


def rejection_context(flight: FlightRecord, assessment) -> dict:
    return {
        "provider": flight.provider,
        "provider_id": flight.provider_id,
        "departure_utc": flight.departure_lower.isoformat(),
        "wait_minutes": assessment.wait_minutes,
        "minimum_minutes": assessment.minimum_minutes,
    }


def validate_prefix(
    request: RebuildRequest, snapshot: RoutingSnapshot
) -> tuple[list[FlightRecord], list[PlanConnection], set[str], ConnectionPolicy]:
    snapshot = snapshot.with_time_basis(request.time_basis)
    records = snapshot.records()
    try:
        prefix = [records[(ref.provider, ref.provider_id)] for ref in request.prefix]
    except KeyError as error:
        raise RoutingError(
            "prefix_missing",
            "a selected flight is missing from this snapshot",
            context={"flight_ref": error.args[0]},
        ) from error
    if request.max_legs is not None and len(prefix) > request.max_legs:
        raise RoutingError("prefix_too_long", "prefix exceeds max_legs")
    if prefix[0].origin_iata in required_via_airports(request.via_airport):
        raise RoutingError("invalid_via_airport", "via_airport must be an intermediate airport")
    if (
        request.horizon_end < prefix[-1].arrival_upper
        or request.horizon_end == prefix[-1].arrival_upper
        and prefix[-1].destination_iata != request.destination
    ):
        raise RoutingError("invalid_horizon", "horizon_end must follow the selected arrival")
    policy = ConnectionPolicy(
        snapshot.airports,
        snapshot.transfers,
        allow_airport_change=request.allow_airport_change,
        avoid_airports=request.avoid_airports,
    )
    # Gap changes apply to the edited connection, not retained earlier choices.
    retained_window = ConnectionWindow(minimum_minutes=0)
    seen, connections, previous = {prefix[0].origin_iata}, [], None
    for flight in prefix:
        if previous and previous.destination_iata == request.destination:
            raise RoutingError(
                "prefix_beyond_destination",
                "selected prefix continues beyond the destination",
            )
        assessment = policy.assess(previous, flight, retained_window, seen, request.horizon_end)
        if assessment.reason != "accepted":
            raise RoutingError(
                "prefix_" + assessment.reason,
                f"invalid selected prefix: {assessment.reason}",
                context=rejection_context(flight, assessment),
            )
        if assessment.connection:
            connections.append(assessment.connection)
        seen.update((flight.origin_iata, flight.destination_iata))
        previous = flight
    return prefix, connections, seen, policy


@dataclass
class PlannedOption:
    flight: FlightRecord | RoutingEdge
    connection: PlanConnection
    tail: list
    tail_connections: list
    complete: bool


@dataclass
class PlanCalculation:
    legs: list
    connections: list
    complete: bool
    options: list
    airports: list
    data_truncated: bool
    search_truncated: bool
    horizon_end: datetime
    message: str
    options_total: int | None
    options_truncated: bool
    diagnostics: SearchDiagnostics
    next_option_offset: int | None = None
    data_revision: str | None = None

    @property
    def flights(self):
        return [*self.legs, *(f for item in self.options for f in (item.flight, *item.tail))]

    def materialize(self, records=None):
        def detail(flight):
            return records[flight_key(flight)] if records is not None else flight

        values = dict(vars(self))
        values["legs"] = [detail(flight) for flight in self.legs]
        values["options"] = [
            ContinuationOption(
                flight=detail(item.flight),
                connection=item.connection,
                tail=[detail(flight) for flight in item.tail],
                tail_connections=item.tail_connections,
                complete=item.complete,
            )
            for item in self.options
        ]
        return RebuiltPlan(**values)


def calculate_rebuild(
    request: RebuildRequest,
    snapshot: RoutingSnapshot | ProjectedSnapshot,
    *,
    include_replay_identity=True,
) -> PlanCalculation:
    snapshot = snapshot.with_time_basis(request.time_basis)
    prefix, connections, visited, policy = validate_prefix(request, snapshot)
    session = RoutingSession(
        snapshot,
        request,
        policy,
        "next_departure_then_tail_arrival_legs_identity",
        include_replay_identity=include_replay_identity,
    )
    later_window = ConnectionWindow(minimum_minutes=request.window.minimum_minutes)
    prefix_via = session.via_progress(prefix)
    memo, options, total, chosen = {}, [], 0, None

    def best_tail(*args):
        return earliest_tail(session, *args, later_window, memo)

    def option(flight, assessment):
        return PlannedOption(
            flight=flight,
            connection=assessment.connection,
            tail=[],
            tail_connections=[],
            complete=(
                flight.destination_iata == request.destination
                and session.via_satisfied(session.advance_via(prefix_via, flight))
            ),
        )

    def fill_tail(item):
        next_seen = visited | {item.flight.origin_iata, item.flight.destination_iata}
        next_via = session.advance_via(prefix_via, item.flight)
        if next_via is None:
            session.record(prefix, item.flight, Assessment("required_via_out_of_order"))
        elif item.flight.destination_iata == request.destination and not session.via_satisfied(
            next_via
        ):
            # A feasible explicit next leg can remain a dead-end option. It must
            # never be ranked as a complete route that satisfies the required via.
            session.record(prefix, item.flight, Assessment("required_via_not_visited"))
        tail = best_tail(
            item.flight,
            session.max_legs - len(prefix) - 1,
            next_seen,
            [*prefix, item.flight],
            next_via,
        )
        if tail is not None:
            item.tail, item.tail_connections, item.complete = (
                list(tail[0]),
                list(tail[1]),
                True,
            )
            session.stats.routes_found += 1

    if request.chosen_next is not None:
        if prefix[-1].destination_iata == request.destination or len(prefix) == session.max_legs:
            raise RoutingError("no_next_leg", "selected prefix has no editable next leg")
        selected = session.records.get(
            (request.chosen_next.provider, request.chosen_next.provider_id)
        )
        if selected is None:
            raise RoutingError(
                "chosen_next_missing",
                "chosen next flight is not loaded; cannot validate it",
            )
        if request.next_airport is not None and selected.destination_iata != request.next_airport:
            raise RoutingError(
                "chosen_next_airport_mismatch",
                "chosen next flight does not arrive at next_airport",
                context={
                    "next_airport": request.next_airport,
                    "destination_iata": selected.destination_iata,
                    "flight_ref": flight_key(selected),
                },
            )
        assessment = policy.assess(
            prefix[-1], selected, request.window, visited, request.horizon_end
        )
        if assessment.reason != "accepted":
            raise RoutingError(
                "chosen_next_" + assessment.reason,
                f"chosen next flight rejected: {assessment.reason}",
                context=rejection_context(selected, assessment),
            )
        chosen = option(selected, assessment)

    try:
        # Evaluate an explicit choice first so a budget cannot silently erase it.
        if chosen is not None:
            session.take()
            session.record(
                prefix,
                chosen.flight,
                policy.assess(
                    prefix[-1],
                    chosen.flight,
                    request.window,
                    visited,
                    request.horizon_end,
                ),
            )
            fill_tail(chosen)
        if prefix[-1].destination_iata != request.destination and len(prefix) < session.max_legs:
            for flight in session.onward(prefix[-1], request.window):
                if (
                    request.chosen_next is not None
                    and chosen is not None
                    and flight_key(chosen.flight) == flight_key(flight)
                ):
                    item = chosen
                else:
                    session.take()
                    if (
                        request.next_airport is not None
                        and flight.destination_iata != request.next_airport
                    ):
                        session.record(prefix, flight, Assessment("next_airport_mismatch"))
                        continue
                    assessment = policy.assess(
                        prefix[-1], flight, request.window, visited, request.horizon_end
                    )
                    session.record(prefix, flight, assessment)
                    if assessment.reason != "accepted":
                        continue
                    item = option(flight, assessment)
                    visible = (
                        request.option_offset
                        <= total
                        < request.option_offset + request.option_limit
                    )
                    if visible or request.chosen_next is None and chosen is None:
                        fill_tail(item)
                total += 1
                if (
                    request.option_offset
                    <= total - 1
                    < request.option_offset + request.option_limit
                ):
                    options.append(item)
                if request.chosen_next is None and chosen is None and item.complete:
                    chosen = item
    except SearchBudgetReached:
        pass
    # Include the selected continuation even when it is outside the option page.
    if chosen is not None and all(
        flight_key(o.flight) != flight_key(chosen.flight) for o in options
    ):
        options.append(chosen)
    options.sort(key=lambda item: departure_key(item.flight))
    legs = [*prefix, *([chosen.flight, *chosen.tail] if chosen else [])]
    links = [
        *connections,
        *([chosen.connection, *chosen.tail_connections] if chosen else []),
    ]
    final_via = session.via_progress(legs)
    complete = legs[-1].destination_iata == request.destination and session.via_satisfied(final_via)
    more = total > request.option_offset + request.option_limit
    diagnostics = session.diagnostics(result_limit_reached=more)
    partial = not diagnostics.exhaustive
    message = (
        "Later legs updated" if complete else "No complete continuation in the loaded search window"
    )
    if final_via is None:
        message = "Selected flights visit via airports out of order; change an earlier connection"
    elif prefix[-1].destination_iata == request.destination:
        message = (
            "Selected prefix already reaches destination"
            if complete
            else "Selected prefix reaches destination without all required via airports"
        )
    elif legs[-1].destination_iata == request.destination and not complete:
        message = "Selected flights reach destination without all required via airports"
    elif request.max_legs is not None and len(prefix) == request.max_legs:
        message = "Maximum leg count reached; destination not reached"
    if partial and final_via is None:
        message += ". Search is partial."
    elif partial:
        message = (
            "Route reaches destination; search is partial"
            if complete
            else "Search is partial; a complete continuation may not have been examined"
        )
    shown_flights = [*legs, *(f for item in options for f in (item.flight, *item.tail))]
    used_airports = {
        request.destination,
        *(code for f in shown_flights for code in (f.origin_iata, f.destination_iata)),
    }
    return PlanCalculation(
        legs=legs,
        connections=links,
        complete=complete,
        options=options,
        airports=sorted(
            (a for a in snapshot.airports if a.iata in used_airports),
            key=lambda a: a.iata,
        ),
        data_truncated=snapshot.data_truncated,
        search_truncated=session.search_truncated,
        horizon_end=request.horizon_end,
        message=message,
        options_total=None if session.search_truncated else total,
        options_truncated=total > len(options) or session.search_truncated,
        diagnostics=diagnostics,
        next_option_offset=(request.option_offset + request.option_limit)
        if more and not session.search_truncated
        else None,
    )


def rebuild_from_snapshot(request: RebuildRequest, snapshot: RoutingSnapshot) -> RebuiltPlan:
    return calculate_rebuild(request, snapshot).materialize()
