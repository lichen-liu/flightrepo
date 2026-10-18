"""Find one exact earliest continuation without enumerating every suffix."""

from collections import defaultdict
from datetime import timedelta
from heapq import heappop, heappush
from itertools import count

from core.routing.policy import Assessment
from core.routing.session import SearchBudgetReached
from core.routing.snapshot import flight_key


def earliest_tail(session, previous, remaining, seen, path, via_progress, window, memo):
    """Return the earliest tail, then fewest legs and lexical flight identity.

    Later connections have no maximum wait. At the same airport and via progress,
    an earlier arrival can wait for a later label's departures. It dominates that
    label only if it also uses no more legs/airports and wins any identity tie.
    This rule is unsuitable for a finite maximum wait; the edited first gap is
    assessed separately by the caller.
    """
    assert window.maximum_minutes is None and window.not_before is None
    destination = session.request.destination
    horizon = session.request.horizon_end
    if via_progress is None:
        return None
    if previous.destination_iata == destination:
        return ((), ()) if session.via_satisfied(via_progress) else None
    if remaining <= 0 or not session.reachable(previous.destination_iata, remaining):
        return None
    key = (flight_key(previous), remaining, tuple(sorted(seen)), via_progress)
    if key in memo:
        session.stats.memo_hits += 1
        return memo[key]
    start = session.index.first_departure(previous, window, session.policy, seen, horizon)
    if start is None:
        memo[key] = None
        return None
    span = timedelta(days=2)
    while True:
        deadline = start + span if span < horizon - start else horizon
        best = _within_horizon(
            session, previous, remaining, seen, path, via_progress, window, deadline, start
        )
        if best is not None or session.search_truncated or deadline == horizon:
            if not session.search_truncated:
                memo[key] = best
            return best
        # A successful trial is globally earliest: flights arriving after its
        # deadline cannot improve it. Empty trials expand to the full requested
        # horizon; the trial width never excludes a journey from the answer.
        span *= 2


def _within_horizon(session, previous, remaining, seen, path, via_progress, window, horizon, start):
    destination = session.request.destination
    index = session.index.for_horizon(horizon, window, start=start)
    distance = index.minimum_legs_to(destination)
    labels, pending, serial = defaultdict(list), [], count()
    best, best_score = None, None
    streams = []

    def dominates(a, b):
        return a[0] <= b[0] and a[1] <= b[1] and a[2] <= b[2] and (a[1] < b[1] or a[3] <= b[3])

    def enqueue(current, available, visited, progress, tail, connections):
        if available <= 0 or distance.get(current.destination_iata, available + 1) > available:
            return
        label = (
            current.arrival_upper,
            len(tail),
            frozenset(visited),
            tuple(flight_key(f) for f in tail),
        )
        group = labels[current.destination_iata, progress]
        if any(dominates(other, label) for other in group):
            session.stats.memo_hits += 1
            return
        group[:] = [other for other in group if not dominates(label, other)]
        group.append(label)
        state = current, available, visited, progress, tail, connections, label
        bound = index.earliest_arrival.get(flight_key(current), current.arrival_upper)
        heappush(pending, (bound, next(serial), True, state))

    def assess(state, flight):
        current, available, visited, progress, tail, connections, _ = state
        session.take()
        decision = session.policy.assess(current, flight, window, visited, horizon)
        next_progress = session.advance_via(progress, flight)
        if decision.reason == "accepted" and next_progress is None:
            decision = Assessment("required_via_out_of_order")
        if (
            decision.reason == "accepted"
            and flight.destination_iata == destination
            and not session.via_satisfied(next_progress)
        ):
            decision = Assessment("required_via_not_visited")
        session.record([*path, *tail], flight, decision)
        if decision.reason != "accepted":
            return None
        return (
            flight,
            available - 1,
            visited | {flight.origin_iata, flight.destination_iata},
            next_progress,
            (*tail, flight),
            (*connections, decision.connection),
        )

    def advance(state, candidates):
        flight = next(candidates, None)
        if flight is not None and (best_score is None or flight.departure_lower < best_score[0]):
            # Flights are ordered by departure, so this bounds the whole unread
            # stream even when a later flight is faster than the next one.
            heappush(
                pending, (flight.departure_lower, next(serial), False, (state, candidates, flight))
            )

    enqueue(previous, remaining, seen, via_progress, (), ())
    try:
        while pending:
            lower, _, expand, payload = heappop(pending)
            if best_score is not None and lower > best_score[0]:
                break
            state = payload if expand else payload[0]
            current, available, visited, progress, tail, connections, label = state
            if label not in labels[current.destination_iata, progress]:
                continue
            if not expand:
                _, candidates, flight = payload
                child = assess(state, flight)
                if child is not None:
                    enqueue(*child)
                advance(state, candidates)
                continue
            session.stats.states_expanded += 1
            deadline = best_score[0] if best_score else horizon
            for flight in session.onward(
                current,
                window,
                horizon=deadline,
                destinations=frozenset((destination,)),
                index=index,
            ):
                child = assess(state, flight)
                if child is None:
                    continue
                score = flight.arrival_upper, len(child[4]), tuple(flight_key(f) for f in child[4])
                if best_score is None or score < best_score:
                    best_score, best = score, (child[4], child[5])
            if available >= 2:
                destinations = frozenset(
                    code
                    for code, steps in distance.items()
                    if code != destination and steps < available
                )
                if destinations:
                    candidates = session.onward(
                        current,
                        window,
                        horizon=best_score[0] if best_score else horizon,
                        destinations=destinations,
                        index=index,
                    )
                    streams.append(candidates)
                    advance(state, candidates)
    except SearchBudgetReached:
        pass
    finally:
        for candidates in streams:
            candidates.close()
    return best
