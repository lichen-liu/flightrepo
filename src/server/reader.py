"""Flight queries over durable facts and backend-managed recoverable itinerary chunks."""

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core.connections import Airport, AirportTransfer, RebuildRequest, RebuiltPlan
from core.itinerary_cache import ItineraryCache
from core.models import FlightRecord, RouteSummary
from core.planning import rebuild_from_snapshot, validate_prefix
from core.routing.models import RouteSearchRequest, RoutingError, airport_code
from core.routing.policy import ConnectionPolicy
from core.routing.results import RouteSearchResult
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot, flight_key
from core.storage import SQLiteConnection, utc_text
from core.timezones import resolve_local
from server.web_models import (
    AvailableDates,
    LocalRouteSearchRequest,
    LocalRouteSearchResult,
    WebCatalog,
)


class FlightReader(SQLiteConnection):
    def __init__(self, path: Path, *, cache=None):
        super().__init__(path, read_only=True)
        self.cache = cache or ItineraryCache(path)

    def stats(self) -> dict:
        catalog = self.cache.catalog()
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
            "catalogue_flights": sum(chunk.flight_count for chunk in catalog.chunks)
            if catalog
            else 0,
        }

    @staticmethod
    def _record(row):
        data = dict(row)
        data["codeshares"] = json.loads(data["codeshares"])
        return FlightRecord.model_validate(data)

    def _window(
        self,
        connection,
        catalog,
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
    ):
        """Merge durable facts and immutable downloaded chunks before applying a limit."""
        clauses, params = [], []
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
            ("departure_scheduled", ">=", start),
            ("departure_scheduled", "<=" if inclusive_end else "<", end),
            ("arrival_scheduled", "<=", arrival_by),
        ):
            if value is not None:
                clauses.append(f"{name} {operator} ?")
                params.append(utc_text(value))
        if not include_cancelled:
            clauses.append("status != 'cancelled'")
        query = "SELECT * FROM flights" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        query += " ORDER BY departure_scheduled, provider, provider_id"
        local_query = query + (" LIMIT ?" if limit else "")
        records = {
            flight_key(f): f
            for f in (
                self._record(row)
                for row in connection.execute(local_query, [*params, limit] if limit else params)
            )
        }
        if catalog is None:
            return list(records.values())
        chunks = sorted(catalog.chunks, key=lambda chunk: (chunk.departure_start, chunk.sha256))

        def key(f):
            return f.departure_scheduled, f.provider, f.provider_id

        for chunk in chunks:
            if (
                origin is not None
                and chunk.origin != origin
                or destination is not None
                and destination not in chunk.destinations
                or provider is not None
                and provider != chunk.provider
                or start is not None
                and chunk.departure_end <= start
                or end is not None
                and (chunk.departure_start > end if inclusive_end else chunk.departure_start >= end)
            ):
                continue
            if (
                limit
                and len(records) >= limit
                and chunk.departure_start > max(records.values(), key=key).departure_scheduled
            ):
                break
            with self.cache.access(chunk) as remote:
                accepted = 0
                for row in remote.execute(query, params):
                    identity = row["provider"], row["provider_id"]
                    # A manual edit/import always overrides its remote version,
                    # even when the edit moves that version outside this query.
                    if connection.execute(
                        "SELECT 1 FROM flights WHERE provider=? AND provider_id=?", identity
                    ).fetchone():
                        continue
                    if connection.execute(
                        "SELECT 1 FROM itinerary_cache_deleted WHERE provider=? AND provider_id=?",
                        identity,
                    ).fetchone():
                        continue
                    record = self._record(row)
                    if identity in records and records[identity] != record:
                        raise RoutingError(
                            "conflicting_flight_identity",
                            "Remote catalogue contains conflicting flight identities",
                        )
                    records[identity] = record
                    accepted += 1
                    if limit and accepted >= limit:
                        break
            if limit and len(records) > limit:
                records = {flight_key(f): f for f in sorted(records.values(), key=key)[:limit]}
        return sorted(records.values(), key=key)

    def overview(self) -> dict:
        """Aggregate database coverage in one read snapshot without loading flights."""
        catalog = self.cache.catalog()
        with self.connect() as connection:
            connection.execute("BEGIN")
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            totals = dict(
                connection.execute("""SELECT COUNT(*) AS flights,
                COUNT(DISTINCT airline_iata) AS airlines,
                MIN(departure_scheduled) AS first_departure,
                MAX(departure_scheduled) AS last_departure,
                MIN(fetched_at) AS oldest_fetch, MAX(fetched_at) AS latest_fetch
                FROM flights""").fetchone()
            )
            for key, query in {
                "routes": "SELECT COUNT(*) FROM (SELECT DISTINCT origin_iata, destination_iata FROM flights)",
                "flight_airports": "SELECT COUNT(*) FROM (SELECT origin_iata FROM flights UNION SELECT destination_iata FROM flights)",
                "catalog_airports": "SELECT COUNT(*) FROM airports",
                "transfers": "SELECT COUNT(*) FROM airport_transfers",
                "missing_airports": "SELECT COUNT(*) FROM (SELECT origin_iata AS iata FROM flights UNION SELECT destination_iata FROM flights) WHERE iata NOT IN (SELECT iata FROM airports)",
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
        if catalog:
            totals["catalogue_flights"] = sum(chunk.flight_count for chunk in catalog.chunks)
            totals["catalogue_chunks"] = len(catalog.chunks)
        return {"data_file": str(self.path.resolve()), "journal_mode": journal_mode, **totals}

    def all_records(self) -> list[FlightRecord]:
        return list(self.iter_records())

    def iter_records(self):
        """Stream chronological days so a full export does not require the whole DB in RAM."""
        catalog = self.cache.catalog()
        with self.connect() as connection:
            connection.execute("BEGIN")
            days = {
                date.fromisoformat(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT substr(departure_scheduled,1,10) FROM flights"
                )
            }
            if catalog:
                for chunk in catalog.chunks:
                    if not chunk.cancelled_count:
                        days.update(chunk.dates_utc)
                        continue
                    day = chunk.departure_start.date()
                    while datetime.combine(day, time.min, UTC) < chunk.departure_end:
                        days.add(day)
                        if day == date.max:
                            break
                        day += timedelta(days=1)
            for day in sorted(days):
                start = datetime.combine(day, time.min, UTC)
                yield from self._window(
                    connection,
                    catalog,
                    start=start,
                    end=start + timedelta(days=1) if day < date.max else None,
                )

    def flight(self, provider: str, provider_id: str) -> FlightRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM flights WHERE provider=? AND provider_id=?",
                (provider, provider_id),
            ).fetchone()
        if row is None:
            remote = self.cache.flight(provider, provider_id)
            if remote is None:
                raise ValueError(f"Flight not found: {provider}/{provider_id}")
            return remote
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
    ):
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if start is not None and end is not None and end <= start:
            raise ValueError("end must follow start")
        catalog = self.cache.catalog()
        with self.connect() as connection:
            connection.execute("BEGIN")
            return self._window(
                connection,
                catalog,
                origin=origin,
                destination=destination,
                provider=provider,
                status=status,
                start=start,
                end=end,
                limit=limit,
            )

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

    def _routing_rows(
        self, connection, request, airports, transfers, *, prefix=(), anchors=(), catalog=None
    ):
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
                # Anchors are exact user selections, separately fetched and retained.
                count = request.limits.max_records - loaded + len(anchors) + 1
                rows = self._window(
                    connection,
                    catalog,
                    origin=origin,
                    start=start,
                    end=end,
                    inclusive_end=not first,
                    arrival_by=request.horizon_end,
                    include_cancelled=False,
                    limit=count,
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
        return RoutingSnapshot(
            tuple(records.values()), airports, transfers, truncated, len(queried)
        )

    def rebuild_snapshot(self, request: RebuildRequest) -> RoutingSnapshot:
        catalog = self.cache.catalog()
        remote_refs = (
            {
                (ref.provider, ref.provider_id): self.cache.flight(
                    ref.provider, ref.provider_id, catalog=catalog
                )
                for ref in (
                    *request.prefix,
                    *((request.chosen_next,) if request.chosen_next else ()),
                )
            }
            if catalog
            else {}
        )
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._catalog(connection)
            prefix_rows = []
            for ref in request.prefix:
                row = connection.execute(
                    "SELECT * FROM flights WHERE provider=? AND provider_id=?",
                    (ref.provider, ref.provider_id),
                ).fetchone()
                remote = remote_refs.get((ref.provider, ref.provider_id))
                if row is None and remote is None:
                    raise RoutingError(
                        "prefix_missing",
                        "a selected flight is missing from this snapshot",
                        context={"flight_ref": [ref.provider, ref.provider_id]},
                    )
                prefix_rows.append(self._record(row) if row is not None else remote)
            validate_prefix(request, RoutingSnapshot(tuple(prefix_rows), airports, transfers))
            anchors = list(prefix_rows)
            if request.chosen_next is not None:
                row = connection.execute(
                    "SELECT * FROM flights WHERE provider=? AND provider_id=?",
                    (request.chosen_next.provider, request.chosen_next.provider_id),
                ).fetchone()
                if row is not None:
                    anchors.append(self._record(row))
                elif (
                    remote_refs.get((request.chosen_next.provider, request.chosen_next.provider_id))
                    is not None
                ):
                    anchors.append(
                        remote_refs[(request.chosen_next.provider, request.chosen_next.provider_id)]
                    )
            return self._routing_rows(
                connection,
                request,
                airports,
                transfers,
                prefix=prefix_rows,
                anchors=anchors,
                catalog=catalog,
            )

    def rebuild(self, request: RebuildRequest) -> RebuiltPlan:
        return rebuild_from_snapshot(request, self.rebuild_snapshot(request))

    def routing_snapshot(self, request: RouteSearchRequest) -> RoutingSnapshot:
        catalog = self.cache.catalog()
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._catalog(connection)
            return self._routing_rows(connection, request, airports, transfers, catalog=catalog)

    def search_itineraries(self, request: RouteSearchRequest) -> RouteSearchResult:
        return search_routes(request, self.routing_snapshot(request))

    def search_local(self, request: LocalRouteSearchRequest) -> LocalRouteSearchResult:
        catalog = self.cache.catalog()
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._catalog(connection)
            query = request.utc_request(airports)
            snapshot = self._routing_rows(connection, query, airports, transfers, catalog=catalog)
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
        self.cache.catalog()
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
        catalog = self.cache.catalog()
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
        if catalog:
            facts["catalogue_flight_count"] = sum(chunk.flight_count for chunk in catalog.chunks)
            facts["providers"] = sorted(
                set(facts["providers"]) | {chunk.provider for chunk in catalog.chunks}
            )
            for name, attribute, choose in (
                ("first_departure", "first_departure", min),
                ("last_departure", "last_departure", max),
                ("oldest_fact_refresh", "oldest_fact_refresh", min),
                ("newest_fact_refresh", "newest_fact_refresh", max),
            ):
                values = [
                    getattr(chunk, attribute)
                    for chunk in catalog.chunks
                    if getattr(chunk, attribute) is not None
                ]
                if facts[name] is not None:
                    values.append(datetime.fromisoformat(facts[name]))
                if values:
                    facts[name] = choose(values)
            airports = {row["iata"]: dict(row) for row in rows}
            for chunk in catalog.chunks:
                for code in (chunk.origin, *chunk.destinations):
                    airports.setdefault(code, {"iata": code})
            rows = [airports[code] for code in sorted(airports)][:1001]
        return WebCatalog(
            airports=[dict(r) for r in rows[:1000]],
            airports_truncated=len(rows) > 1000,
            dataset=facts,
        )

    def available_dates(self, origin: str, clock: str = "local") -> AvailableDates:
        if clock not in ("local", "UTC"):
            raise ValueError("clock must be local or UTC")
        origin = airport_code(origin)
        catalog = self.cache.catalog()
        with self.connect() as connection:
            connection.execute("BEGIN")
            zone = "UTC"
            if clock == "local":
                row = connection.execute(
                    "SELECT timezone FROM airports WHERE iata=?", (origin,)
                ).fetchone()
                if row is None:
                    raise RoutingError(
                        "airport_metadata_missing", "Load airport timezone metadata or choose UTC."
                    )
                zone = row[0]
            rows = connection.execute(
                "SELECT departure_scheduled FROM flights WHERE origin_iata=? AND status != 'cancelled'",
                (origin,),
            )
            timezone = ZoneInfo(zone)
            dates = sorted(
                {datetime.fromisoformat(row[0]).astimezone(timezone).date() for row in rows}
            )
            if catalog:
                remote_dates = set()
                for chunk in catalog.chunks:
                    if chunk.origin != origin:
                        continue
                    edited = connection.execute(
                        "SELECT 1 FROM flights WHERE provider=? LIMIT 1", (chunk.provider,)
                    ).fetchone()
                    deleted = connection.execute(
                        "SELECT 1 FROM itinerary_cache_deleted WHERE provider=? LIMIT 1",
                        (chunk.provider,),
                    ).fetchone()
                    if edited or deleted or clock != "UTC" and chunk.origin_timezone != zone:
                        facts = self._window(
                            connection,
                            catalog,
                            provider=chunk.provider,
                            origin=origin,
                            start=chunk.departure_start,
                            end=chunk.departure_end,
                            include_cancelled=False,
                        )
                        remote_dates.update(
                            f.departure_scheduled.astimezone(timezone).date() for f in facts
                        )
                    elif clock == "UTC":
                        remote_dates.update(chunk.dates_utc)
                    elif chunk.origin_timezone == zone:
                        remote_dates.update(chunk.dates_local)
                dates = sorted(set(dates) | remote_dates)
        return AvailableDates(origin=origin, timezone=zone, dates=dates)

    def airports(
        self, city_id: str | None = None, query: str = "", limit: int = 500
    ) -> list[Airport]:
        self.cache.catalog()
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
        self.cache.catalog()
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
        catalog = self.cache.catalog()
        with self.connect() as connection:
            connection.execute("BEGIN")
            return self._window(
                connection,
                catalog,
                start=start,
                end=end,
                origin=origin.upper() if origin else None,
                destination=destination.upper() if destination else None,
                include_cancelled=include_cancelled,
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
        catalog = self.cache.catalog()
        if catalog:
            with self.connect() as connection:
                connection.execute("BEGIN")
                records = self._window(
                    connection,
                    catalog,
                    start=start,
                    end=end,
                    origin=origin.upper() if origin else None,
                    destination=destination.upper() if destination else None,
                    include_cancelled=False,
                )
            pairs = {}
            for record in records:
                pairs.setdefault((record.origin_iata, record.destination_iata), []).append(record)
            return [
                RouteSummary(
                    origin_iata=a,
                    destination_iata=b,
                    flight_count=len(flights),
                    airlines=sorted({f.airline_iata for f in flights if f.airline_iata}),
                    first_departure=min(f.departure_scheduled for f in flights),
                    last_departure=max(f.departure_scheduled for f in flights),
                )
                for (a, b), flights in sorted(
                    pairs.items(), key=lambda item: (-len(item[1]), item[0])
                )[:limit]
            ]
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
