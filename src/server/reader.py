"""Flight queries over the shared local itinerary database."""

import json
from bisect import bisect_left
from datetime import date, datetime, time, timedelta
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from zoneinfo import ZoneInfo

from core.admission import ADMITTED_SQL, REVIEW_REQUIRED_SQL
from core.connections import Airport, AirportTransfer, RebuildRequest, RebuiltPlan
from core.models import FlightRecord, RouteSummary
from core.planning import rebuild_from_snapshot, validate_prefix
from core.routing.models import RouteSearchRequest, RoutingError, airport_code
from core.routing.policy import ConnectionPolicy
from core.routing.results import RouteSearchResult
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot, flight_key
from core.storage import EPOCH, SQLiteConnection, utc_text, utc_us
from core.timezones import resolve_local
from server.web_models import (
    AirportCoverage,
    AirportTraffic,
    AvailableDates,
    LocalRouteSearchRequest,
    LocalRouteSearchResult,
    SearchAvailability,
    WebBootstrap,
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
            contract, version = connection.execute(
                "SELECT contract,version FROM schema_metadata WHERE id=1"
            ).fetchone()
            bases = self._time_bases(connection)
        return {
            "data_file": str(self.path),
            "flights": count,
            "providers": providers,
            "journal_mode": mode,
            "schema_contract": contract,
            "schema_version": version,
            "time_bases": bases,
        }

    @staticmethod
    def _record(row):
        data = dict(row)
        data["codeshares"] = json.loads(data["codeshares"])
        data["review_reasons"] = json.loads(data.get("review_reasons", "[]"))
        return FlightRecord.model_validate(data)

    def _window(
        self,
        connection,
        *,
        origin=None,
        destination=None,
        provider=None,
        status=None,
        start=None,
        end=None,
        inclusive_end=False,
        arrival_by=None,
        include_cancelled=True,
        limit=None,
        time_basis=None,
        needs_review=False,
        searchable_only=False,
    ):
        """Query shared facts with typed time bounds and deterministic limits."""
        clauses, params = [], []
        if needs_review:
            clauses.append(REVIEW_REQUIRED_SQL)
        if searchable_only or time_basis is not None:
            clauses.append(ADMITTED_SQL)
        departure_column, arrival_column = "departure", "arrival"
        if time_basis is not None:
            departure_column = (
                "departure_observed_lower_us"
                if time_basis == "observed"
                else f"departure_{time_basis}_us"
            )
            arrival_column = (
                "arrival_observed_upper_us"
                if time_basis == "observed"
                else f"arrival_{time_basis}_us"
            )
            clauses.extend([f"{departure_column} IS NOT NULL", f"{arrival_column} IS NOT NULL"])
        for name, value in (
            ("origin_iata", origin),
            ("destination_iata", destination),
            ("provider", provider),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{name}=?")
                params.append(value)
        for name, operator, value in (
            (departure_column, ">=", start),
            (departure_column, "<=" if inclusive_end else "<", end),
            (arrival_column, "<=", arrival_by),
        ):
            if value is not None:
                clauses.append(f"{name} {operator} ?")
                params.append(utc_us(value) if time_basis else utc_text(value))
        if not include_cancelled:
            clauses.append("status != 'cancelled'")
        query = "SELECT * FROM flights" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        query += f" ORDER BY {departure_column}, provider, provider_id"

        def selected(row):
            record = self._record(row)
            return (
                record.model_copy(update={"time_basis": time_basis})
                if time_basis not in (None, "scheduled")
                else record
            )

        local_query = query + (" LIMIT ?" if limit else "")
        return [
            selected(row)
            for row in connection.execute(local_query, [*params, limit] if limit else params)
        ]

    def overview(self) -> dict:
        """Aggregate database coverage in one read snapshot without loading flights."""
        with self.connect() as connection:
            connection.execute("BEGIN")
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            totals = dict(
                connection.execute("""SELECT COUNT(*) AS flights,
                COUNT(DISTINCT airline_iata) AS airlines,
                MIN(departure) AS first_departure,
                MAX(departure) AS last_departure,
                MIN(fetched_at) AS oldest_fetch, MAX(fetched_at) AS latest_fetch
                FROM flights""").fetchone()
            )
            for key, query in {
                "routes": "SELECT COUNT(*) FROM (SELECT DISTINCT origin_iata, destination_iata FROM flights)",
                "flight_airports": "SELECT COUNT(*) FROM (SELECT origin_iata FROM flights UNION SELECT destination_iata FROM flights)",
                "catalog_airports": "SELECT COUNT(*) FROM airports WHERE name IS NOT NULL AND city_id IS NOT NULL AND timezone IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL",
                "transfers": "SELECT COUNT(*) FROM airport_transfers",
                "missing_airports": "SELECT COUNT(*) FROM (SELECT origin_iata AS iata FROM flights UNION SELECT destination_iata FROM flights) WHERE iata NOT IN (SELECT iata FROM airports WHERE name IS NOT NULL AND city_id IS NOT NULL AND timezone IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL)",
            }.items():
                totals[key] = connection.execute(query).fetchone()[0]
            for key, column in (("providers", "provider"), ("statuses", "status")):
                totals[key] = dict(
                    connection.execute(
                        f"SELECT {column}, COUNT(*) FROM flights GROUP BY {column} ORDER BY {column}"
                    ).fetchall()
                )
            totals["top_routes"] = [
                dict(row)
                for row in connection.execute("""
                SELECT origin_iata, destination_iata, COUNT(*) AS flights
                FROM flights GROUP BY origin_iata, destination_iata
                ORDER BY flights DESC, origin_iata, destination_iata LIMIT 5
            """)
            ]
            totals.update(self._review_counts(connection))
            totals["time_bases"] = self._time_bases(connection)
            totals["ingestion_batches"] = dict(
                connection.execute(
                    "SELECT state,COUNT(*) FROM ingestion_batches GROUP BY state"
                ).fetchall()
            )
            totals["schema_contract"], totals["schema_version"] = connection.execute(
                "SELECT contract,version FROM schema_metadata WHERE id=1"
            ).fetchone()
        return {"data_file": str(self.path.resolve()), "journal_mode": journal_mode, **totals}

    def all_records(self) -> list[FlightRecord]:
        return list(self.iter_records())

    def iter_records(self):
        """Stream a consistent ordered export without loading the whole DB into RAM."""
        with self.connect() as connection:
            connection.execute("BEGIN")
            for row in connection.execute(
                "SELECT * FROM flights ORDER BY departure IS NULL,departure,provider,provider_id"
            ):
                yield self._record(row)

    def flight(self, provider: str, provider_id: str) -> FlightRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM flights WHERE provider=? AND provider_id=?",
                (provider, provider_id),
            ).fetchone()
        if row is None:
            raise ValueError(f"Flight not found: {provider}/{provider_id}")
        return self._record(row)

    def list_flights(
        self,
        *,
        origin=None,
        destination=None,
        provider=None,
        status=None,
        start=None,
        end=None,
        limit=20,
        needs_review=False,
    ):
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if start is not None and end is not None and end <= start:
            raise ValueError("end must follow start")
        with self.connect() as connection:
            connection.execute("BEGIN")
            return self._window(
                connection,
                origin=origin,
                destination=destination,
                provider=provider,
                status=status,
                needs_review=needs_review,
                start=start,
                end=end,
                limit=limit,
            )

    @staticmethod
    def _catalog(connection):
        airports = tuple(
            Airport.model_validate(dict(row))
            for row in connection.execute(
                "SELECT * FROM airports WHERE name IS NOT NULL OR city_id IS NOT NULL OR timezone IS NOT NULL OR latitude IS NOT NULL ORDER BY iata"
            )
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
        basis = request.time_basis or (
            prefix[0].effective_time_basis if prefix else self._default_basis(connection)
        )
        if basis != "scheduled":
            prefix = tuple(f.model_copy(update={"time_basis": basis}) for f in prefix)
            anchors = tuple(
                f.model_copy(update={"time_basis": basis})
                for f in anchors
                if f.has_time_basis(basis)
            )
        start = prefix[-1].arrival_upper if is_rebuild else request.departure_start
        remaining = request.max_legs
        if is_rebuild and remaining is not None:
            remaining -= len(prefix)
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
        depth = 0
        while remaining is None or depth < remaining:
            pending_origins = frontier - queried - {request.destination}
            if not pending_origins:
                break
            batch = []
            for origin in sorted(pending_origins):
                queried.add(origin)
                first = not is_rebuild and depth == 0
                end = request.departure_end if first else request.horizon_end
                # Anchors are exact user selections, separately fetched and retained.
                count = request.limits.max_records - loaded + len(anchors) + 1
                rows = self._window(
                    connection,
                    origin=origin,
                    start=start,
                    end=end,
                    inclusive_end=not first,
                    arrival_by=request.horizon_end,
                    include_cancelled=False,
                    limit=count,
                    time_basis=basis,
                )
                for row in rows:
                    key = flight_key(row)
                    if key not in records:
                        if loaded == request.limits.max_records:
                            truncated = True
                            break
                        records[key] = row
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
            depth += 1
        return RoutingSnapshot(
            tuple(records.values()), airports, transfers, truncated, len(queried)
        )

    @staticmethod
    def _review_counts(connection):
        return dict(
            connection.execute(f"""SELECT
            (SELECT COUNT(DISTINCT leg_id) FROM searchable_legs) searchable_flights,
            COUNT(*) FILTER (WHERE {REVIEW_REQUIRED_SQL}) needs_review_count FROM flights""").fetchone()
        )

    @staticmethod
    def _time_bases(connection):
        counts = {
            basis: connection.execute(
                "SELECT COUNT(*) FROM searchable_legs WHERE time_basis=?", (basis,)
            ).fetchone()[0]
            for basis in ("scheduled", "observed", "actual")
        }
        return counts

    @classmethod
    def _default_basis(cls, connection):
        return next(
            (
                basis
                for basis in ("scheduled", "observed", "actual")
                if connection.execute(
                    "SELECT 1 FROM searchable_legs WHERE time_basis=? LIMIT 1", (basis,)
                ).fetchone()
            ),
            "scheduled",
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
        if exact is None or exact.timezone is None:
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
            # Aggregate typed storage directly. The compatibility view formats
            # every timestamp; doing that for a whole archive delays startup.
            conditions = {
                "scheduled": "departure_scheduled_us IS NOT NULL AND arrival_scheduled_us IS NOT NULL",
                "observed": "departure_observed_lower_us IS NOT NULL",
                "actual": "departure_actual_us IS NOT NULL AND arrival_actual_us IS NOT NULL",
            }
            columns = {
                basis: "departure_observed_lower_us"
                if basis == "observed"
                else f"departure_{basis}_us"
                for basis in conditions
            }
            aggregates = ",".join(
                f"COALESCE(SUM(eligible AND ({condition})),0) {basis}_count,"
                f"MIN(CASE WHEN eligible AND ({condition}) THEN {columns[basis]} END) {basis}_first,"
                f"MAX(CASE WHEN eligible AND ({condition}) THEN {columns[basis]} END) {basis}_last"
                for basis, condition in conditions.items()
            )
            complete = " OR ".join(f"({condition})" for condition in conditions.values())
            facts = dict(
                connection.execute(f"""WITH live AS (
                SELECT f.*, status <> 'cancelled' AND {ADMITTED_SQL} eligible
                FROM flight_legs f WHERE NOT EXISTS (
                    SELECT 1 FROM flight_tombstones t
                    WHERE t.source_id=f.source_id AND t.source_record_key=f.source_record_key
                )) SELECT COUNT(*) total_record_count,
                COALESCE(SUM(eligible AND ({complete})),0) flight_count,
                COALESCE(SUM({REVIEW_REQUIRED_SQL}),0) needs_review_count,
                COALESCE(SUM(status = 'cancelled'), 0) cancelled_count,
                MIN(last_seen_us) oldest_fact_refresh, MAX(last_seen_us) newest_fact_refresh,
                {aggregates} FROM live""").fetchone()
            )
            for key in ("oldest_fact_refresh", "newest_fact_refresh"):
                if facts[key] is not None:
                    facts[key] = EPOCH + timedelta(microseconds=facts[key])
            facts["providers"] = [
                r[0]
                for r in connection.execute(
                    "SELECT DISTINCT provider FROM flights ORDER BY provider"
                )
            ]
            facts["time_bases"] = {basis: facts.pop(f"{basis}_count") for basis in conditions}
            facts["default_time_basis"] = next(
                (basis for basis, count in facts["time_bases"].items() if count), "scheduled"
            )
            basis = facts["default_time_basis"]
            coverage = facts[f"{basis}_first"], facts[f"{basis}_last"]
            for name in conditions:
                facts.pop(f"{name}_first")
                facts.pop(f"{name}_last")
            facts["first_departure"], facts["last_departure"] = (
                EPOCH + timedelta(microseconds=value) if value is not None else None
                for value in coverage
            )
            facts["sources"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT code,name,source_url,license_expression,license_url,attribution FROM data_sources ORDER BY code"
                )
            ]
            suggestion = connection.execute(
                f"""SELECT origin_iata,destination_iata FROM flight_legs f
                WHERE status <> 'cancelled' AND ({conditions[basis]})
                AND {ADMITTED_SQL}
                AND NOT EXISTS (SELECT 1 FROM flight_tombstones t
                    WHERE t.source_id=f.source_id AND t.source_record_key=f.source_record_key)
                ORDER BY {columns[basis]},source_id,source_record_key LIMIT 1""",
            ).fetchone()
            rows = list(
                connection.execute("""WITH active_codes(iata) AS (
                SELECT origin_iata FROM flights UNION SELECT destination_iata FROM flights)
                SELECT a.iata, a.name, a.city_id, a.timezone, a.latitude, a.longitude
                FROM airports a ORDER BY CASE WHEN a.iata IN active_codes THEN 0 ELSE 1 END,a.iata LIMIT 1001""")
            )
        return WebCatalog(
            airports=sorted((dict(r) for r in rows[:1000]), key=lambda row: row["iata"]),
            airports_truncated=len(rows) > 1000,
            dataset=facts,
            suggested_route=dict(suggestion) if suggestion else None,
        )

    def web_bootstrap(self) -> WebBootstrap:
        """Load search defaults and suggestions without archive-wide statistics."""
        with self.connect() as connection:
            connection.execute("BEGIN")
            first = {}
            for basis in ("scheduled", "observed", "actual"):
                column = (
                    "departure_observed_lower_us"
                    if basis == "observed"
                    else f"departure_{basis}_us"
                )
                condition = (
                    "departure_observed_lower_us IS NOT NULL"
                    if basis == "observed"
                    else f"departure_{basis}_us IS NOT NULL AND arrival_{basis}_us IS NOT NULL"
                )
                row = connection.execute(
                    f"SELECT origin_iata,destination_iata,{column} FROM flight_legs f "
                    f"WHERE status <> 'cancelled' AND ({condition}) "
                    f"AND {ADMITTED_SQL} "
                    "AND NOT EXISTS (SELECT 1 FROM flight_tombstones t WHERE t.source_id=f.source_id "
                    "AND t.source_record_key=f.source_record_key) "
                    f"ORDER BY {column},source_id,source_record_key LIMIT 1",
                ).fetchone()
                if row:
                    first[basis] = row
            basis = next(iter(first), "scheduled")
            suggested = first.get(basis)
            rows = connection.execute("""WITH active_codes(iata) AS (
                SELECT origin_iata FROM flight_legs UNION SELECT destination_iata FROM flight_legs)
                SELECT a.iata,a.name,a.city_id,a.timezone,a.latitude,a.longitude
                FROM airports a ORDER BY CASE WHEN a.iata IN active_codes THEN 0 ELSE 1 END,a.iata
                LIMIT 1001""").fetchall()
        return WebBootstrap(
            airports=sorted((dict(row) for row in rows[:1000]), key=lambda row: row["iata"]),
            airports_truncated=len(rows) > 1000,
            default_time_basis=basis,
            available_time_bases=list(first),
            first_departure=(EPOCH + timedelta(microseconds=suggested[2])) if suggested else None,
            suggested_route={"origin_iata": suggested[0], "destination_iata": suggested[1]}
            if suggested
            else None,
        )

    def search_availability(
        self,
        origin: str,
        destination: str,
        clock="local",
        time_basis=None,
        departure_date: date | None = None,
        departure_last_date: date | None = None,
    ) -> SearchAvailability:
        origin, destination = airport_code(origin), airport_code(destination)
        if clock not in ("local", "UTC"):
            raise ValueError("clock must be local or UTC")
        with self.connect() as connection:
            connection.execute("BEGIN")
            basis = time_basis or self._default_basis(connection)
            if basis not in ("scheduled", "observed", "actual"):
                raise ValueError("Unknown flight time basis")
            zone = "UTC"
            if clock == "local":
                row = connection.execute(
                    "SELECT timezone FROM airports WHERE iata=?", (origin,)
                ).fetchone()
                if not row or not row[0]:
                    raise RoutingError(
                        "airport_metadata_missing", "Load airport timezone metadata or choose UTC."
                    )
                zone = row[0]
            timezone = ZoneInfo(zone)
            outbound = {}
            for lower, upper in connection.execute(
                "SELECT departure_lower_us,departure_upper_us FROM searchable_legs "
                "WHERE origin_iata=? AND time_basis=?",
                (origin, basis),
            ):
                day = (EPOCH + timedelta(microseconds=lower)).astimezone(timezone).date()
                if day == (EPOCH + timedelta(microseconds=upper)).astimezone(timezone).date():
                    outbound[day] = outbound.get(day, 0) + 1
            arrivals = sorted(
                (lower, upper)
                for lower, upper in connection.execute(
                    "SELECT arrival_lower_us,arrival_upper_us FROM searchable_legs "
                    "WHERE destination_iata=? AND time_basis=?",
                    (destination, basis),
                )
            )

        def bounds(day, last=None):
            try:
                start = resolve_local(datetime.combine(day, time.min), zone)
                end = resolve_local(
                    datetime.combine((last or day) + timedelta(days=1), time.min), zone
                )
                return start, end + timedelta(days=7)
            except (OverflowError, ValueError) as error:
                raise RoutingError(
                    "invalid_local_dates", "Departure date is outside the supported range."
                ) from error

        def inbound_count(start, end):
            lower, upper = utc_us(start), utc_us(end)
            index = bisect_left(arrivals, (lower, -1))
            count = 0
            for earliest, latest in arrivals[index:]:
                if earliest > upper:
                    break
                count += latest <= upper
            return count

        dates = [day for day in sorted(outbound) if inbound_count(*bounds(day))]
        selected = departure_date if departure_date in dates else next(iter(dates), departure_date)
        last = (
            departure_last_date
            if departure_last_date in dates and selected and departure_last_date >= selected
            else None
        )
        if last and (last - selected).days >= 31:
            raise RoutingError("invalid_local_dates", "Choose at most 31 departure dates.")
        start, end = bounds(selected, last) if selected else (None, None)
        return SearchAvailability(
            origin=origin,
            destination=destination,
            timezone=zone,
            time_basis=basis,
            dates=dates,
            selected_date=selected,
            outbound=outbound.get(selected, 0),
            inbound=inbound_count(start, end) if selected else 0,
            arrival_start=start,
            arrival_end=end,
        )

    def airport_coverage(self, airports: list[str], time_basis=None) -> AirportCoverage:
        """Exact-airport counts across all loaded dates, separate from route availability."""
        codes = list(dict.fromkeys(airport_code(code) for code in airports))
        if not 1 <= len(codes) <= 2:
            raise ValueError("Choose one or two airports for coverage.")
        with self.connect() as connection:
            connection.execute("BEGIN")
            basis = time_basis or self._default_basis(connection)
            if basis not in ("scheduled", "observed", "actual"):
                raise ValueError("Unknown flight time basis")
            placeholders = ",".join("?" for _ in codes)
            totals = ",".join(
                "COALESCE(SUM(destination_iata=?),0),COALESCE(SUM(origin_iata=?),0)" for _ in codes
            )
            params = [code for code in codes for _ in range(2)] + codes + codes
            where = f"(origin_iata IN ({placeholders}) OR destination_iata IN ({placeholders}))"
            stored = connection.execute(
                f"SELECT {totals} FROM flight_legs f WHERE {where} AND NOT EXISTS "
                "(SELECT 1 FROM flight_tombstones t WHERE t.source_id=f.source_id "
                "AND t.source_record_key=f.source_record_key)",
                params,
            ).fetchone()
            searchable = connection.execute(
                f"SELECT {totals} FROM searchable_legs WHERE {where} AND time_basis=?",
                [*params, basis],
            ).fetchone()
        return AirportCoverage(
            time_basis=basis,
            airports=[
                AirportTraffic(
                    airport=code,
                    inbound=searchable[2 * index],
                    outbound=searchable[2 * index + 1],
                    stored_inbound=stored[2 * index],
                    stored_outbound=stored[2 * index + 1],
                )
                for index, code in enumerate(codes)
            ],
        )

    @staticmethod
    def _nearby_departure_airports(connection, origin, radius_km=100):
        """Calendar coverage only; route searches still use their exact origin."""
        rows = connection.execute(
            "SELECT iata,latitude,longitude FROM airports "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        ).fetchall()
        center = next((row for row in rows if row[0] == origin), None)
        if center is None:
            return [origin]
        latitude, longitude = map(radians, center[1:])
        nearby = []
        for code, lat, lon in rows:
            lat, lon = radians(lat), radians(lon)
            haversine = (
                sin((lat - latitude) / 2) ** 2
                + cos(latitude) * cos(lat) * sin((lon - longitude) / 2) ** 2
            )
            distance = 2 * 6371 * asin(sqrt(min(1, haversine)))
            if distance <= radius_km:
                nearby.append(code)
        return [origin, *sorted(set(nearby) - {origin})]

    def available_dates(
        self,
        origin: str,
        clock: str = "local",
        time_basis=None,
        *,
        destination=None,
        include_nearby=False,
    ) -> AvailableDates:
        if clock not in ("local", "UTC"):
            raise ValueError("clock must be local or UTC")
        origin = airport_code(origin)
        destination = airport_code(destination) if destination is not None else None
        with self.connect() as connection:
            connection.execute("BEGIN")
            basis = time_basis or self._default_basis(connection)
            if basis not in ("scheduled", "observed", "actual"):
                raise ValueError("Unknown flight time basis")
            zone = "UTC"
            if clock == "local":
                row = connection.execute(
                    "SELECT timezone FROM airports WHERE iata=?", (origin,)
                ).fetchone()
                if row is None or row[0] is None:
                    raise RoutingError(
                        "airport_metadata_missing", "Load airport timezone metadata or choose UTC."
                    )
                zone = row[0]
            origins = (
                self._nearby_departure_airports(connection, origin) if include_nearby else [origin]
            )
            route_clause = " AND destination_iata=?" if destination is not None else ""
            route_params = [*origins, *([destination] if destination is not None else [])]
            placeholders = ",".join("?" for _ in origins)
            rows = connection.execute(
                "SELECT departure_lower_us,departure_upper_us,origin_iata FROM searchable_legs "
                f"WHERE origin_iata IN ({placeholders}){route_clause} AND time_basis=?",
                [*route_params, basis],
            )
            timezone = ZoneInfo(zone)

            def day(value):
                return (EPOCH + timedelta(microseconds=value)).astimezone(timezone).date()

            dates, contributing_origins = set(), set()
            for lower, upper, code in rows:
                date = day(lower)
                if date == day(upper):
                    dates.add(date)
                    contributing_origins.add(code)
        return AvailableDates(
            origin=origin,
            destination=destination,
            timezone=zone,
            dates=sorted(dates),
            time_basis=basis,
            departure_airports=(
                [code for code in origins if code in contributing_origins]
                if include_nearby
                else None
            ),
            nearby_radius_km=100 if include_nearby else None,
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
        clauses = ["departure >= ?", "departure < ?"]
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
        with self.connect() as connection:
            connection.execute("BEGIN")
            return self._window(
                connection,
                start=start,
                end=end,
                origin=origin.upper() if origin else None,
                destination=destination.upper() if destination else None,
                include_cancelled=include_cancelled,
                searchable_only=True,
                limit=limit,
            )

    def routes(
        self,
        start: datetime,
        end: datetime,
        origin: str | None = None,
        destination: str | None = None,
        limit: int = 500,
    ) -> list[RouteSummary]:
        where, params = self._where(start, end, origin, destination)
        where += f" AND {ADMITTED_SQL}"
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT origin_iata, destination_iata, COUNT(*) flight_count,
                GROUP_CONCAT(DISTINCT airline_iata) airlines, MIN(departure) first_departure,
                MAX(departure) last_departure FROM flights WHERE {where}
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
