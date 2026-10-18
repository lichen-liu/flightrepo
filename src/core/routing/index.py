"""Per-airport departure arrays: bisect windows instead of rescanning schedules."""

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from heapq import merge

from core.models import FlightStatus
from core.routing.snapshot import flight_key


def departure_key(flight):
    return flight.departure_lower, *flight_key(flight)


class FlightIndex:
    def __init__(self, records, policy, horizon, *, minimum_legs=None, earliest_arrival=None):
        self.minimum_legs = minimum_legs or {}
        self.earliest_arrival = earliest_arrival or {}
        grouped = defaultdict(list)
        for flight in records.values():
            if flight.blocked_for_search:
                continue
            grouped[flight.origin_iata].append(flight)
        self.by_origin = {
            origin: tuple(sorted(rows, key=departure_key)) for origin, rows in grouped.items()
        }
        self.times = {
            origin: tuple(f.departure_lower for f in rows)
            for origin, rows in self.by_origin.items()
        }
        reverse = defaultdict(set)
        for flight in records.values():
            if flight.blocked_for_search:
                continue
            if flight.status == FlightStatus.CANCELLED or flight.arrival_upper > horizon:
                continue
            if flight.origin_iata in policy.avoid or flight.destination_iata in policy.avoid:
                continue
            reverse[flight.destination_iata].add(flight.origin_iata)
            for arrival in policy.arrivals_by_departure.get(flight.origin_iata, ()):
                reverse[flight.destination_iata].add(arrival)
        self.reverse = reverse

    def first_departure(self, previous, window, policy, seen, horizon):
        candidates, _ = self.onward(previous, window, policy, horizon)
        try:
            return next(
                (
                    flight.departure_lower
                    for flight in candidates
                    if policy.assess(previous, flight, window, seen, horizon).reason == "accepted"
                ),
                None,
            )
        finally:
            candidates.close()

    def for_horizon(self, horizon, window, *, start=None):
        return self

    def origin_size(self, origin):
        return len(self.by_origin.get(origin, ()))

    def window(
        self, origin, lower, upper, *, upper_inclusive=True, destinations=None, arrival_horizon=None
    ):
        rows, times = self.by_origin.get(origin, ()), self.times.get(origin, ())
        a = bisect_left(times, lower)
        b = bisect_right(times, upper) if upper_inclusive else bisect_left(times, upper)
        b = max(a, b)
        selected = map(rows.__getitem__, range(a, b))
        if arrival_horizon is not None:
            selected = (flight for flight in selected if flight.arrival_upper <= arrival_horizon)
        if destinations is not None:
            selected = (flight for flight in selected if flight.destination_iata in destinations)
        return selected, len(rows) - (b - a)

    def onward(self, previous, window, policy, horizon, *, destinations=None):
        groups, skipped = [], 0
        for origin in policy.origins(previous.destination_iata):
            bounds = policy.bounds(previous, origin, window, horizon)
            rows, excluded = (
                self.window(origin, *bounds, destinations=destinations, arrival_horizon=horizon)
                if bounds
                else ((), self.origin_size(origin))
            )
            groups.append(rows)
            skipped += excluded
        return merge(*groups, key=departure_key), skipped

    def minimum_legs_to(self, destination):
        # Ignore timing/visited airports. This is only a safe lower bound for pruning.
        distance, queue = {destination: 0}, deque([destination])
        while queue:
            airport = queue.popleft()
            for origin in sorted(self.reverse.get(airport, ())):
                if origin not in distance:
                    distance[origin] = distance[airport] + 1
                    queue.append(origin)
        return distance
