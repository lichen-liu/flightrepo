"""Per-airport departure arrays: bisect windows instead of rescanning schedules."""

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from heapq import merge

from core.models import FlightStatus
from core.routing.snapshot import flight_key


def departure_key(flight):
    return flight.departure_scheduled, *flight_key(flight)


class FlightIndex:
    def __init__(self, records, policy, horizon):
        grouped = defaultdict(list)
        for flight in records.values():
            grouped[flight.origin_iata].append(flight)
        self.by_origin = {
            origin: tuple(sorted(rows, key=departure_key)) for origin, rows in grouped.items()
        }
        self.times = {
            origin: tuple(f.departure_scheduled for f in rows)
            for origin, rows in self.by_origin.items()
        }
        reverse = defaultdict(set)
        for flight in records.values():
            if flight.status == FlightStatus.CANCELLED or flight.arrival_scheduled > horizon:
                continue
            if flight.origin_iata in policy.avoid or flight.destination_iata in policy.avoid:
                continue
            reverse[flight.destination_iata].add(flight.origin_iata)
            for arrival in policy.arrivals_by_departure.get(flight.origin_iata, ()):
                reverse[flight.destination_iata].add(arrival)
        self.reverse = reverse

    def window(self, origin, lower, upper, *, upper_inclusive=True):
        rows, times = self.by_origin.get(origin, ()), self.times.get(origin, ())
        a = bisect_left(times, lower)
        b = bisect_right(times, upper) if upper_inclusive else bisect_left(times, upper)
        b = max(a, b)
        return map(rows.__getitem__, range(a, b)), len(rows) - (b - a)

    def onward(self, previous, window, policy, horizon):
        groups, skipped = [], 0
        for origin in policy.origins(previous.destination_iata):
            bounds = policy.bounds(previous, origin, window, horizon)
            rows, excluded = (
                self.window(origin, *bounds)
                if bounds
                else ((), len(self.by_origin.get(origin, ())))
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
