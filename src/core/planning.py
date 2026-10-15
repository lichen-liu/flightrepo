"""Exact-prefix continuation planning using the shared schedule routing rules."""

from collections.abc import Sequence
from datetime import timedelta

from core.connections import (
    Airport,
    AirportTransfer,
    ConnectionWindow,
    ContinuationOption,
    PlanConnection,
    RebuildRequest,
    RebuiltPlan,
)
from core.models import FlightRecord
from core.routing.index import departure_key
from core.routing.models import (
    MAX_DEPARTURE_DAYS,
    MAX_ONWARD_DAYS,
    RoutingError,
    required_via_airports,
)
from core.routing.policy import Assessment, ConnectionPolicy
from core.routing.session import RoutingSession, SearchBudgetReached
from core.routing.snapshot import RoutingSnapshot, flight_key


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
    if len(prefix) > request.max_legs:
        raise RoutingError("prefix_too_long", "prefix exceeds max_legs")
    if prefix[0].origin_iata in required_via_airports(request.via_airport):
        raise RoutingError("invalid_via_airport", "via_airport must be an intermediate airport")
    if (
        request.horizon_end < prefix[-1].arrival_upper
        or request.horizon_end == prefix[-1].arrival_upper
        and prefix[-1].destination_iata != request.destination
    ):
        raise RoutingError("invalid_horizon", "horizon_end must follow the selected arrival")
    # A retained first flight may lie at the start of a full search window.
    maximum_span = MAX_DEPARTURE_DAYS + MAX_ONWARD_DAYS
    if request.horizon_end - prefix[0].departure_lower > timedelta(days=maximum_span):
        raise RoutingError(
            "invalid_horizon",
            f"horizon must be within {maximum_span} days of first departure",
        )
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


def rebuild_from_snapshot(request: RebuildRequest, snapshot: RoutingSnapshot) -> RebuiltPlan:
    snapshot = snapshot.with_time_basis(request.time_basis)
    prefix, connections, visited, policy = validate_prefix(request, snapshot)
    session = RoutingSession(
        snapshot, request, policy, "next_departure_then_tail_arrival_legs_identity"
    )
    later_window = ConnectionWindow(minimum_minutes=request.window.minimum_minutes)
    prefix_via = session.via_progress(prefix)
    memo, options, total, chosen = {}, [], 0, None

    def best_tail(previous, remaining, seen, path, via_progress):
        if via_progress is None:
            return None
        if previous.destination_iata == request.destination:
            return ((), ()) if session.via_satisfied(via_progress) else None
        if remaining == 0 or not session.reachable(previous.destination_iata, remaining):
            return None
        key = (flight_key(previous), remaining, tuple(sorted(seen)), via_progress)
        if key in memo:
            session.stats.memo_hits += 1
            return memo[key]
        session.stats.states_expanded += 1
        best, best_score = None, None
        try:
            for flight in session.onward(previous, later_window):
                session.take()
                assessment = policy.assess(
                    previous, flight, later_window, seen, request.horizon_end
                )
                next_seen = seen | {flight.origin_iata, flight.destination_iata}
                next_via = session.advance_via(via_progress, flight)
                if assessment.reason == "accepted" and next_via is None:
                    assessment = Assessment("required_via_out_of_order")
                if (
                    assessment.reason == "accepted"
                    and flight.destination_iata == request.destination
                    and not session.via_satisfied(next_via)
                ):
                    assessment = Assessment("required_via_not_visited")
                session.record(path, flight, assessment)
                if assessment.reason != "accepted":
                    continue
                tail = best_tail(
                    flight,
                    remaining - 1,
                    next_seen,
                    [*path, flight],
                    next_via,
                )
                if tail is None:
                    continue
                candidate = ((flight, *tail[0]), (assessment.connection, *tail[1]))
                score = (
                    candidate[0][-1].arrival_upper,
                    len(candidate[0]),
                    tuple(flight_key(f) for f in candidate[0]),
                )
                if best_score is None or score < best_score:
                    best, best_score = candidate, score
        except SearchBudgetReached:
            pass
        if not session.search_truncated:
            memo[key] = best
        return best

    def option(flight, assessment):
        return ContinuationOption(
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
            request.max_legs - len(prefix) - 1,
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
        if prefix[-1].destination_iata == request.destination or len(prefix) == request.max_legs:
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
        if prefix[-1].destination_iata != request.destination and len(prefix) < request.max_legs:
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
                    fill_tail(item)
                total += 1
                if len(options) < request.option_limit:
                    options.append(item)
                if request.chosen_next is None and chosen is None and item.complete:
                    chosen = item
    except SearchBudgetReached:
        pass
    # Include the selected continuation even when it is outside the option page.
    if chosen is not None and all(
        flight_key(o.flight) != flight_key(chosen.flight) for o in options
    ):
        if len(options) == request.option_limit:
            options.pop()
        options.append(chosen)
    options.sort(key=lambda item: departure_key(item.flight))
    legs = [*prefix, *([chosen.flight, *chosen.tail] if chosen else [])]
    links = [
        *connections,
        *([chosen.connection, *chosen.tail_connections] if chosen else []),
    ]
    final_via = session.via_progress(legs)
    complete = legs[-1].destination_iata == request.destination and session.via_satisfied(final_via)
    diagnostics = session.diagnostics(result_limit_reached=total > request.option_limit)
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
    elif len(prefix) == request.max_legs:
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
    return RebuiltPlan(
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
    )


def rebuild_plan(
    request: RebuildRequest,
    records: Sequence[FlightRecord],
    airports: Sequence[Airport],
    transfers: Sequence[AirportTransfer],
    *,
    data_truncated: bool = False,
) -> RebuiltPlan:
    """Compatibility entry point for callers supplying an in-memory snapshot."""
    return rebuild_from_snapshot(
        request,
        RoutingSnapshot(tuple(records), tuple(airports), tuple(transfers), data_truncated),
    )
