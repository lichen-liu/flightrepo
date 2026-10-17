"""Time-compatible reverse edges used to avoid loading unrelated full records."""

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from heapq import heapify, heappop, heappush
from itertools import count
from typing import NamedTuple


class TimeEdge(NamedTuple):
    provider: str
    provider_id: str
    origin: str
    destination: str
    departure_lower: int
    departure_upper: int
    arrival_lower: int
    arrival_upper: int


class ReachableFlights(dict):
    """Reachable identities plus complete suffixes to seed bounded snapshots."""

    def __init__(self):
        super().__init__()
        self.distance = {}
        self.leg_distance = {}
        self.arrival_bound = {}
        self.terminal = set()
        self.edges = {}
        self.following = {}

    def preferred_paths(self, origin, start, end, max_legs, via=()):
        paths = []
        for key in sorted(self.get(origin, ())):
            first = self.edges[key]
            if first.departure_lower < start or first.departure_upper >= end:
                continue
            seen, path, progress = {origin}, [], 0
            while key is not None:
                edge = self.edges[key]
                if edge.destination in seen or (
                    path and edge.origin in seen and path[-1].destination != edge.origin
                ):
                    break
                for code in (edge.origin, edge.destination):
                    if progress < len(via) and code == via[progress]:
                        progress += 1
                seen.update((edge.origin, edge.destination))
                path.append(edge)
                key = self.following[key]
            else:
                if (max_legs is None or len(path) <= max_legs) and progress == len(via):
                    paths.append(path)
        paths.sort(
            key=lambda path: (
                len(path),
                path[-1].arrival_upper - path[0].departure_lower,
                path[0].departure_lower,
                [(edge.provider, edge.provider_id) for edge in path],
            )
        )
        return paths


def reverse_reachable_flights(edges, destination, policy, window, start, *, earliest=False):
    """Return a conservative superset of every feasible suffix to destination.

    Flight intervals and connection/transfer allowances match routing. Airport
    cycles, via constraints and leg counts are deliberately left to the engine:
    ignoring those here can only retain extra edges, never remove a valid route.
    """
    incoming = defaultdict(list)
    for edge in edges:
        if edge.origin not in policy.avoid and edge.destination not in policy.avoid:
            incoming[edge.destination].append(edge)
    for rows in incoming.values():
        rows.sort(key=lambda edge: edge.arrival_upper)
    times = {airport: [edge.arrival_upper for edge in rows] for airport, rows in incoming.items()}
    successors = {airport: list(range(len(rows) + 1)) for airport, rows in incoming.items()}

    def find(links, index):
        while links[index] != index:
            links[index] = links[links[index]]
            index = links[index]
        return index

    terminals = incoming.get(destination, ())
    serial = count()
    queue = (
        [(edge.arrival_upper, next(serial), edge, 1) for edge in terminals]
        if earliest
        else deque((edge, 1) for edge in terminals)
    )
    if earliest:
        heapify(queue)
    if destination in successors:
        # Terminal edges are already enqueued. A visited edge never needs to be
        # inspected again for another suffix, even at very busy hubs.
        links = successors[destination]
        for index in range(len(links) - 1):
            links[index] = len(links) - 1
    identities = ReachableFlights()
    identities.terminal = {(edge.provider, edge.provider_id) for edge in terminals}
    identities.following = {key: None for key in identities.terminal}
    while queue:
        if earliest:
            arrival, _, following, distance = heappop(queue)
        else:
            following, distance = queue.popleft()
        following_key = (following.provider, following.provider_id)
        identities.edges[following_key] = following
        identities.leg_distance[following_key] = distance
        if earliest:
            identities.arrival_bound[following_key] = arrival
        identities.setdefault(following.origin, set()).add(
            (following.provider, following.provider_id)
        )
        identities.distance.setdefault(following.origin, distance)
        arrivals = (following.origin, *policy.arrivals_by_departure.get(following.origin, ()))
        for airport in arrivals:
            rows = incoming.get(airport)
            if not rows:
                continue
            rule = policy.minimum(airport, following.origin, window)
            if rule is None:
                continue
            lower = (
                start
                if window.maximum_minutes is None
                else following.departure_upper - window.maximum_minutes * 60_000_000
            )
            upper = following.departure_lower - rule[0] * 60_000_000
            links = successors[airport]
            index = find(links, bisect_left(times[airport], lower))
            stop = bisect_right(times[airport], upper)
            while index < stop:
                previous = rows[index]
                if previous.arrival_lower >= lower:
                    if earliest:
                        heappush(queue, (arrival, next(serial), previous, distance + 1))
                    else:
                        queue.append((previous, distance + 1))
                    identities.following[(previous.provider, previous.provider_id)] = following_key
                    links[index] = find(links, index + 1)
                index = find(links, index + 1)
    return identities
