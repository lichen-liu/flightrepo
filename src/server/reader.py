"""Read-only flight queries for serving clients; never initializes or mutates data."""

import json
from datetime import datetime
from pathlib import Path

from core.connections import Airport, AirportTransfer, RebuildRequest, RebuiltPlan
from core.models import FlightRecord, RouteSummary
from core.planning import rebuild_from_snapshot, validate_prefix
from core.routing.models import RouteSearchRequest, RoutingError
from core.routing.policy import ConnectionPolicy
from core.routing.results import RouteSearchResult
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot, flight_key
from core.storage import SQLiteConnection, utc_text
from core.timezones import resolve_local
from server.web_models import (
    LocalRouteSearchRequest,
    LocalRouteSearchResult,
    WebCatalog,
)


class FlightReader(SQLiteConnection):
    def __init__(self, path: Path):
        super().__init__(path, read_only=True)

    def stats(self) -> dict:
        with self.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
            providers = dict(
                connection.execute(
                    "SELECT provider, COUNT(*) FROM flights GROUP BY provider"
                ).fetchall()
            )
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        return {
            "data_file": str(self.path),
            "flights": count,
            "providers": providers,
            "journal_mode": mode,
        }

    @staticmethod
    def _record(row):
        data = dict(row)
        data["codeshares"] = json.loads(data["codeshares"])
        return FlightRecord.model_validate(data)

    def all_records(self) -> list[FlightRecord]:
        with self.connect() as connection:
            return [
                self._record(row)
                for row in connection.execute("SELECT * FROM flights ORDER BY departure_scheduled")
            ]

    @staticmethod
    def _catalog(connection):
        airports = tuple(
            Airport.model_validate(dict(row))
            for row in connection.execute("SELECT * FROM airports ORDER BY iata")
        )
        transfers = tuple(
            AirportTransfer.model_validate(dict(row))
            for row in connection.execute(
                "SELECT * FROM airport_transfers ORDER BY arrival_iata, departure_iata"
            )
        )
        return airports, transfers

    def _routing_rows(self, connection, request, airports, transfers, *, prefix=(), anchors=()):
        """Load only airports reachable within the leg bound, in deterministic order.

        Onward windows are conservative: an airport first considered for a short
        connection can also be reached later by another path. The pure index
        applies exact gap bounds. All SELECTs run in the caller's one snapshot.
        """
        is_rebuild = bool(prefix)
        start = prefix[-1].arrival_scheduled if is_rebuild else request.departure_start
        remaining = request.max_legs - len(prefix) if is_rebuild else request.max_legs
        policy = ConnectionPolicy(
            airports,
            transfers,
            allow_airport_change=request.allow_airport_change,
            avoid_airports=request.avoid_airports,
        )
        frontier = (
            set(policy.origins(prefix[-1].destination_iata)) if is_rebuild else {request.origin}
        )
        if is_rebuild and prefix[-1].destination_iata == request.destination:
            frontier = set()
        records = {flight_key(f): f for f in anchors}
        queried, loaded, truncated = set(), 0, False
        for depth in range(remaining):
            batch = []
            for origin in sorted(frontier - queried - {request.destination}):
                queried.add(origin)
                first = not is_rebuild and depth == 0
                end = request.departure_end if first else request.horizon_end
                operator = "<" if first else "<="
                # Anchors are exact user selections, separately fetched and retained.
                count = request.limits.max_records - loaded + len(anchors) + 1
                rows = connection.execute(
                    f"""SELECT * FROM flights
                    WHERE origin_iata=? AND departure_scheduled >= ? AND departure_scheduled {operator} ?
                    AND arrival_scheduled <= ? AND status != 'cancelled'
                    ORDER BY departure_scheduled, provider, provider_id LIMIT ?""",
                    (
                        origin,
                        utc_text(start),
                        utc_text(end),
                        utc_text(request.horizon_end),
                        count,
                    ),
                )
                for row in rows:
                    key = row["provider"], row["provider_id"]
                    if key not in records:
                        if loaded == request.limits.max_records:
                            truncated = True
                            break
                        records[key] = self._record(row)
                        loaded += 1
                    batch.append(records[key])
                if truncated:
                    break
            if truncated:
                break
            frontier = {
                origin
                for f in batch
                if f.destination_iata != request.destination
                and f.destination_iata not in policy.avoid
                for origin in policy.origins(f.destination_iata)
            }
        return RoutingSnapshot(
            tuple(records.values()), airports, transfers, truncated, len(queried)
        )

    def rebuild_snapshot(self, request: RebuildRequest) -> RoutingSnapshot:
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._catalog(connection)
            prefix_rows = []
            for ref in request.prefix:
                row = connection.execute(
                    "SELECT * FROM flights WHERE provider=? AND provider_id=?",
                    (ref.provider, ref.provider_id),
                ).fetchone()
                if row is None:
                    raise RoutingError(
                        "prefix_missing",
                        "a selected flight is missing from this snapshot",
                        context={"flight_ref": [ref.provider, ref.provider_id]},
                    )
                prefix_rows.append(self._record(row))
            validate_prefix(request, RoutingSnapshot(tuple(prefix_rows), airports, transfers))
            anchors = list(prefix_rows)
            if request.chosen_next is not None:
                row = connection.execute(
                    "SELECT * FROM flights WHERE provider=? AND provider_id=?",
                    (request.chosen_next.provider, request.chosen_next.provider_id),
                ).fetchone()
                if row is not None:
                    anchors.append(self._record(row))
            return self._routing_rows(
                connection,
                request,
                airports,
                transfers,
                prefix=prefix_rows,
                anchors=anchors,
            )

    def rebuild(self, request: RebuildRequest) -> RebuiltPlan:
        return rebuild_from_snapshot(request, self.rebuild_snapshot(request))

    def routing_snapshot(self, request: RouteSearchRequest) -> RoutingSnapshot:
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._catalog(connection)
            return self._routing_rows(connection, request, airports, transfers)

    def search_itineraries(self, request: RouteSearchRequest) -> RouteSearchResult:
        return search_routes(request, self.routing_snapshot(request))

    def search_local(self, request: LocalRouteSearchRequest) -> LocalRouteSearchResult:
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._catalog(connection)
            query = request.utc_request(airports)
            snapshot = self._routing_rows(connection, query, airports, transfers)
        by_code = {a.iata: a for a in airports}
        result = search_routes(query, snapshot)
        return LocalRouteSearchResult(
            **result.model_dump(),
            departure_timezone=by_code[query.origin].timezone,
            arrival_timezone=(
                by_code[query.destination].timezone if query.destination in by_code else None
            ),
        )

    def resolve_time(
        self,
        airport: str,
        local_time: datetime,
        fold: int | None = None,
        expected_timezone: str | None = None,
    ) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM airports WHERE iata=?", (airport,)).fetchone()
        exact = Airport.model_validate(dict(row)) if row is not None else None
        if exact is None:
            raise RoutingError(
                "airport_metadata_missing",
                "Airport timezone metadata is unavailable.",
                context={"airport": airport},
            )
        if expected_timezone is not None and exact.timezone != expected_timezone:
            raise RoutingError(
                "airport_timezone_changed",
                "Airport timezone changed. Review the time and retry.",
                context={
                    "airport": airport,
                    "expected_timezone": expected_timezone,
                    "actual_timezone": exact.timezone,
                },
            )
        return {
            "airport": airport,
            "timezone": exact.timezone,
            "utc": resolve_local(local_time, exact.timezone, fold=fold),
        }

    def web_catalog(self) -> WebCatalog:
        with self.connect() as connection:
            connection.execute("BEGIN")
            facts = dict(
                connection.execute("""SELECT COUNT(*) flight_count,
                COALESCE(SUM(status = 'cancelled'), 0) cancelled_count,
                MIN(CASE WHEN status != 'cancelled' THEN departure_scheduled END) first_departure,
                MAX(CASE WHEN status != 'cancelled' THEN departure_scheduled END) last_departure,
                MIN(fetched_at) oldest_fact_refresh, MAX(fetched_at) newest_fact_refresh FROM flights""").fetchone()
            )
            facts["providers"] = [
                r[0]
                for r in connection.execute(
                    "SELECT DISTINCT provider FROM flights ORDER BY provider"
                )
            ]
            rows = list(
                connection.execute("""WITH codes(iata) AS (
                SELECT iata FROM airports UNION SELECT origin_iata FROM flights UNION SELECT destination_iata FROM flights)
                SELECT codes.iata, a.name, a.city_id, a.timezone, a.latitude, a.longitude
                FROM codes LEFT JOIN airports a ON a.iata=codes.iata ORDER BY codes.iata LIMIT 1001""")
            )
        return WebCatalog(
            airports=[dict(r) for r in rows[:1000]],
            airports_truncated=len(rows) > 1000,
            dataset=facts,
        )

    def airports(
        self, city_id: str | None = None, query: str = "", limit: int = 500
    ) -> list[Airport]:
        clauses, params = [], []
        if city_id:
            clauses.append("city_id = ?")
            params.append(city_id)
        if query:
            clauses.append("(iata = ? OR name LIKE ?)")
            params.extend((query.upper(), "%" + query + "%"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM airports" + where + " ORDER BY city_id, iata LIMIT ?",
                [*params, limit],
            )
            return [Airport.model_validate(dict(row)) for row in rows]

    def airport_catalog(self) -> dict:
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._catalog(connection)
        return {
            "airports": [a.model_dump(mode="json") for a in airports],
            "transfers": [t.model_dump(mode="json") for t in transfers],
        }

    @staticmethod
    def _where(start, end, origin, destination, include_cancelled=False):
        clauses = ["departure_scheduled >= ?", "departure_scheduled < ?"]
        params = [utc_text(start), utc_text(end)]
        for name, value in (("origin_iata", origin), ("destination_iata", destination)):
            if value:
                clauses.append(f"{name} = ?")
                params.append(value.upper())
        if not include_cancelled:
            clauses.append("status != 'cancelled'")
        return " AND ".join(clauses), params

    def search(
        self,
        start: datetime,
        end: datetime,
        origin: str | None = None,
        destination: str | None = None,
        include_cancelled: bool = False,
        limit: int = 500,
    ) -> list[FlightRecord]:
        where, params = self._where(start, end, origin, destination, include_cancelled)
        with self.connect() as connection:
            return [
                self._record(row)
                for row in connection.execute(
                    f"SELECT * FROM flights WHERE {where} ORDER BY departure_scheduled LIMIT ?",
                    [*params, limit],
                )
            ]

    def routes(
        self,
        start: datetime,
        end: datetime,
        origin: str | None = None,
        destination: str | None = None,
        limit: int = 500,
    ) -> list[RouteSummary]:
        where, params = self._where(start, end, origin, destination)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT origin_iata, destination_iata, COUNT(*) flight_count,
                GROUP_CONCAT(DISTINCT airline_iata) airlines, MIN(departure_scheduled) first_departure,
                MAX(departure_scheduled) last_departure FROM flights WHERE {where}
                GROUP BY origin_iata, destination_iata
                ORDER BY flight_count DESC, origin_iata, destination_iata LIMIT ?""",
                [*params, limit],
            )
            return [
                RouteSummary(
                    origin_iata=row["origin_iata"],
                    destination_iata=row["destination_iata"],
                    flight_count=row["flight_count"],
                    airlines=sorted(filter(None, (row["airlines"] or "").split(","))),
                    first_departure=datetime.fromisoformat(row["first_departure"]),
                    last_departure=datetime.fromisoformat(row["last_departure"]),
                )
                for row in rows
            ]
