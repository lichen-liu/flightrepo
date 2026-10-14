"""Request-local limits, counters and a bounded explanation trace."""

from core.connections import RebuildRequest
from core.routing.index import FlightIndex
from core.routing.models import (
    ENGINE_VERSION,
    RouteSearchRequest,
    RoutingError,
    SearchDiagnostics,
    SearchStats,
    TraceEvent,
    required_via_airports,
)
from core.routing.policy import ConnectionPolicy
from core.routing.snapshot import RoutingSnapshot, digest, flight_key


class SearchBudgetReached(Exception):
    pass


class RoutingSession:
    def __init__(
        self,
        snapshot: RoutingSnapshot,
        request: RouteSearchRequest | RebuildRequest,
        policy: ConnectionPolicy,
        ranking: str,
    ):
        self.snapshot, self.request, self.policy, self.ranking = (
            snapshot,
            request,
            policy,
            ranking,
        )
        self.records = snapshot.records()
        anchors = len(getattr(request, "prefix", ())) + int(
            getattr(request, "chosen_next", None) is not None
        )
        if len(self.records) > request.limits.max_records + anchors:
            raise RoutingError(
                "snapshot_too_large",
                "supplied snapshot exceeds the record budget; load a bounded snapshot",
            )
        self.index = FlightIndex(self.records, policy, request.horizon_end)
        self.distance = self.index.minimum_legs_to(request.destination)
        self.stats = SearchStats(
            rows_loaded=len(self.records), origins_queried=snapshot.origins_queried
        )
        self.events, self.search_truncated = [], False
        self.required_via = required_via_airports(request.via_airport)

    def take(self):
        if self.stats.candidates_checked >= self.request.limits.max_candidates:
            self.search_truncated = True
            raise SearchBudgetReached()
        self.stats.candidates_checked += 1

    def record(self, path, candidate, assessment):
        if assessment.reason != "accepted":
            self.stats.rejections[assessment.reason] = (
                self.stats.rejections.get(assessment.reason, 0) + 1
            )
        if not self.request.trace:
            return
        if len(self.events) >= self.request.limits.max_trace_events:
            self.stats.trace_events_omitted += 1
            return
        self.events.append(
            TraceEvent(
                path=[flight_key(f) for f in path],
                candidate=flight_key(candidate),
                decision=assessment.reason,
                wait_minutes=assessment.wait_minutes,
                minimum_minutes=assessment.minimum_minutes,
            )
        )

    def onward(self, previous, window):
        candidates, skipped = self.index.onward(
            previous, window, self.policy, self.request.horizon_end
        )
        self.stats.candidates_skipped_by_time += skipped
        return candidates

    def reachable(self, airport, remaining):
        return self.distance.get(airport, remaining + 1) <= remaining

    def advance_via(self, progress, flight):
        if progress is None:
            return None
        # At a transfer, the previous arrival precedes this departure airport.
        for code in (flight.origin_iata, flight.destination_iata):
            if progress < len(self.required_via):
                if code == self.required_via[progress]:
                    progress += 1
                elif code in self.required_via[progress + 1 :]:
                    return None
        return progress

    def via_progress(self, legs):
        progress = 0
        for flight in legs:
            progress = self.advance_via(progress, flight)
        return progress

    def via_satisfied(self, progress):
        return progress == len(self.required_via)

    def diagnostics(self, *, result_limit_reached=False):
        reasons = []
        if self.snapshot.data_truncated:
            reasons.append("data_limit")
        if self.search_truncated:
            reasons.append("candidate_limit")
        snapshot_id = self.snapshot.snapshot_id
        return SearchDiagnostics(
            snapshot_id=snapshot_id,
            request_id=digest(
                {
                    "engine": ENGINE_VERSION,
                    "snapshot": snapshot_id,
                    "query": self.request.model_dump(mode="json"),
                }
            ),
            ranking=self.ranking,
            exhaustive=not reasons,
            termination_reasons=reasons,
            result_limit_reached=result_limit_reached,
            stats=self.stats,
            events=self.events,
        )
