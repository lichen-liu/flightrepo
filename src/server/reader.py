"""Flight queries over the shared local itinerary database."""

import hashlib
import json
import sqlite3
from bisect import bisect_left
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from core.admission import ADMITTED_SQL
from core.config import get_settings
from core.connections import Airport, AirportTransfer, ConnectionWindow, RebuildRequest, RebuiltPlan
from core.database_roles import require_serving
from core.models import FlightRecord, RouteSummary
from core.planning import calculate_rebuild, validate_prefix
from core.quality import review_recommended_sql
from core.routing.models import (
    RouteSearchRequest,
    RoutingError,
    airport_code,
    required_via_airports,
)
from core.routing.policy import ConnectionPolicy
from core.routing.results import RouteSearchResult
from core.routing.snapshot import RoutingSnapshot, flight_key
from core.storage import EPOCH, SQLiteConnection, flight_record, utc_text, utc_us
from core.timezones import resolve_local
from server.reachability import TimeEdge, reverse_reachable_flights
from server.routing_projection import calculate_database_routes, project_routes
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


class ReadQueryTimeout(sqlite3.OperationalError):
    """The serving read exceeded its configured SQLite work deadline."""


class FlightReader(SQLiteConnection):
    def __init__(self, path: Path, *, serving_only=False, query_timeout_seconds=None):
        super().__init__(path, read_only=True)
        self._catalog_lock = Lock()
        self._catalog_cache = {}
        self._bootstrap_lock = Lock()
        self._bootstrap_cache = None
        self.serving_only = serving_only
        self.query_timeout_seconds = (
            get_settings().read_query_timeout_seconds
            if serving_only and query_timeout_seconds is None
            else query_timeout_seconds
        )

    @contextmanager
    def connect(self):
        with super().connect() as connection:
            if self.serving_only:
                require_serving(connection)
            if self.query_timeout_seconds is None:
                yield connection
                return
            deadline = monotonic() + self.query_timeout_seconds
            connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            try:
                yield connection
            except sqlite3.OperationalError as error:
                if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
                    raise ReadQueryTimeout("Database read exceeded its time limit") from error
                raise
            finally:
                connection.set_progress_handler(None, 0)

    def _catalog_revision(self):
        revision = []
        database_path = self.path.resolve()
        for path in (
            database_path,
            Path(str(database_path) + "-wal"),
            Path(str(database_path) + "-journal"),
        ):
            try:
                stat = path.stat()
                revision.append(
                    None
                    if path != database_path and stat.st_size == 0
                    else (
                        stat.st_dev,
                        stat.st_ino,
                        stat.st_size,
                        stat.st_mtime_ns,
                        stat.st_ctime_ns,
                    )
                )
            except FileNotFoundError:
                revision.append(None)
        return tuple(revision)

    def data_revision(self):
        return hashlib.sha256(repr(self._catalog_revision()).encode()).hexdigest()

    def check_page_revision(self, expected):
        revision = self.data_revision()
        if expected is not None and expected != revision:
            raise RoutingError("data_changed", "Flight data changed. Start a new search.")
        return revision

    @staticmethod
    def _search_horizon(connection, request, basis):
        if request.horizon_end is not None:
            return request
        row = connection.execute(
            "SELECT arrival_upper_us FROM searchable_legs WHERE destination_iata=? AND time_basis=? "
            "ORDER BY arrival_upper_us DESC LIMIT 1",
            (request.destination, basis),
        ).fetchone()
        horizon = (
            max(request.departure_end, EPOCH + timedelta(microseconds=row[0]))
            if row
            else request.departure_end
        )
        return request.model_copy(update={"horizon_end": horizon})

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

    _record = staticmethod(flight_record)

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
        identities=None,
        offset=0,
    ):
        """Query shared facts with typed time bounds and deterministic limits."""
        if identities is not None and not identities:
            return []
        clauses, params = [], []
        if identities is not None:
            clauses.append(
                "(provider,provider_id) IN (SELECT json_extract(value,'$[0]'),"
                "json_extract(value,'$[1]') FROM json_each(?))"
            )
            params.append(json.dumps(sorted(identities)))
        if needs_review:
            clauses.append(review_recommended_sql())
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

        local_query = query + (" LIMIT ? OFFSET ?" if limit else "")
        return [
            selected(row)
            for row in connection.execute(
                local_query, [*params, limit, min(offset, 2**63 - 1)] if limit else params
            )
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
                "named_airports": "SELECT COUNT(*) FROM airports WHERE name IS NOT NULL",
                "catalog_airports": "SELECT COUNT(*) FROM airports WHERE name IS NOT NULL AND city_id IS NOT NULL AND timezone IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL",
                "transfers": "SELECT COUNT(*) FROM airport_transfers",
                "missing_airports": "SELECT COUNT(*) FROM (SELECT origin_iata AS iata FROM flights UNION SELECT destination_iata FROM flights) WHERE iata NOT IN (SELECT iata FROM airports WHERE name IS NOT NULL AND city_id IS NOT NULL AND timezone IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL)",
            }.items():
                totals[key] = connection.execute(query).fetchone()[0]
            totals["reference_carriers"] = (
                connection.execute("SELECT COUNT(*) FROM carriers").fetchone()[0]
                if connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name='carriers'"
                ).fetchone()
                else 0
            )
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

    def _destination_candidates(self, connection, request, policy, basis):
        """Use bounded, lightweight edge data before loading full flight records."""
        limit = max(100_000, request.limits.max_records * 20, request.limits.max_candidates or 0)
        rows = connection.execute(
            "SELECT s.code,l.source_record_key,l.origin_iata,l.destination_iata,"
            "l.departure_lower_us,l.departure_upper_us,l.arrival_lower_us,l.arrival_upper_us "
            "FROM searchable_legs l JOIN data_sources s ON s.id=l.source_id "
            "WHERE l.time_basis=? AND l.departure_lower_us>=? AND l.arrival_upper_us<=? LIMIT ?",
            (basis, utc_us(request.departure_start), utc_us(request.horizon_end), limit + 1),
        ).fetchall()
        if len(rows) > limit:
            # An incomplete reverse graph cannot safely exclude anything. The
            # existing bounded loader and its partial-result diagnostics apply.
            return None
        return reverse_reachable_flights(
            (TimeEdge(*row) for row in rows),
            request.destination,
            policy,
            ConnectionWindow(
                minimum_minutes=request.minimum_connection_minutes,
                maximum_minutes=request.maximum_connection_minutes,
            ),
            utc_us(request.departure_start),
        )

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
        identities = (
            None if is_rebuild else self._destination_candidates(connection, request, policy, basis)
        )
        frontier = (
            set(policy.origins(prefix[-1].destination_iata)) if is_rebuild else {request.origin}
        )
        if is_rebuild and prefix[-1].destination_iata == request.destination:
            frontier = set()
        records = {flight_key(f): f for f in anchors}
        queried, loaded, truncated = set(), 0, False
        preferred = set()
        if identities is not None:
            seed_budget = max(1, request.limits.max_records // 2)
            for path in identities.preferred_paths(
                request.origin,
                utc_us(start),
                utc_us(request.departure_end),
                request.max_legs,
                required_via_airports(request.via_airport),
            )[: request.limit]:
                keys = {(edge.provider, edge.provider_id) for edge in path}
                if len(preferred | keys) <= seed_budget:
                    preferred.update(keys)
            seed = set(preferred)
            for key in sorted(identities.terminal):
                if len(seed) == seed_budget:
                    break
                seed.add(key)
            # Keep destination arrivals in the snapshot before a busy early hub
            # consumes the record budget. Otherwise valid prefixes can all look
            # disconnected simply because their final legs were never loaded.
            terminal_rows = self._window(
                connection,
                start=start,
                end=request.horizon_end,
                inclusive_end=True,
                arrival_by=request.horizon_end,
                include_cancelled=False,
                time_basis=basis,
                identities=seed,
                limit=seed_budget,
            )
            records.update((flight_key(row), row) for row in terminal_rows)
            loaded = len(records)
        seeded = dict(records)
        depth = 0
        while remaining is None or depth < remaining:
            pending_origins = frontier - queried - {request.destination}
            if not pending_origins:
                break
            batch = []
            for origin in sorted(
                pending_origins,
                key=lambda origin: (
                    identities.distance.get(origin, float("inf")) if identities is not None else 0,
                    origin,
                ),
            ):
                queried.add(origin)
                first = not is_rebuild and depth == 0
                end = request.departure_end if first else request.horizon_end
                # Anchors are exact user selections, separately fetched and retained.
                duplicates = sum(f.origin_iata == origin for f in seeded.values())
                count = request.limits.max_records - loaded + duplicates + 1
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
                    identities=None if identities is None else identities.get(origin, ()),
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
        if truncated and preferred and preferred <= seeded.keys():
            # Preserve complete time-compatible journeys when arbitrary prefixes
            # would exhaust search work before reaching their loaded final legs.
            # This remains explicitly partial; omitted routes can rank better.
            records = seeded
        return RoutingSnapshot(
            tuple(records.values()), airports, transfers, truncated, len(queried)
        )

    @staticmethod
    def _review_counts(connection):
        return dict(
            connection.execute(f"""SELECT
            (SELECT COUNT(DISTINCT leg_id) FROM searchable_legs) searchable_flights,
            COUNT(*) FILTER (WHERE {review_recommended_sql()}) needs_review_count FROM flights""").fetchone()
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
        revision = self.check_page_revision(request.data_revision)
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._routing_catalog(connection, request)
            if request.data_revision is not None:
                self.check_page_revision(request.data_revision)
            keys = {(ref.provider, ref.provider_id) for ref in request.prefix}
            if request.chosen_next is not None:
                keys.add((request.chosen_next.provider, request.chosen_next.provider_id))
            anchors = {flight_key(f): f for f in self._window(connection, identities=keys)}
            try:
                prefix = tuple(anchors[(ref.provider, ref.provider_id)] for ref in request.prefix)
            except KeyError as error:
                raise RoutingError(
                    "prefix_missing",
                    "a selected flight is missing from this snapshot",
                    context={"flight_ref": error.args[0]},
                ) from error
            normalized, _, _, _ = validate_prefix(
                request,
                RoutingSnapshot(prefix, airports, transfers),
            )
            basis = request.time_basis or normalized[0].effective_time_basis
            snapshot = project_routes(
                connection,
                request,
                airports,
                transfers,
                basis,
                anchors=tuple(anchors.values()),
                start=normalized[-1].arrival_upper,
                revision=revision,
            )
            hydrated = {
                key: f.model_copy(update={"time_basis": basis}) if basis != "scheduled" else f
                for key, f in anchors.items()
                if f.has_time_basis(basis)
            }

            calculation = calculate_rebuild(
                request, snapshot, include_replay_identity=not self.serving_only
            )
            missing = [f for f in calculation.flights if flight_key(f) not in hydrated]
            hydrated.update(self._hydrate_flights(connection, missing, basis))
            result = calculation.materialize(hydrated)
            result.data_revision = revision
            result.diagnostics.stats.records_hydrated = len(hydrated)
            shown = [
                *result.legs,
                *(f for item in result.options for f in (item.flight, *item.tail)),
            ]
            codes = {request.destination}
            codes.update(code for f in shown for code in (f.origin_iata, f.destination_iata))
            result.airports = self._hydrate_airports(connection, codes)
            return result

    def routing_snapshot(self, request: RouteSearchRequest) -> RoutingSnapshot:
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._catalog(connection)
            basis = request.time_basis or self._default_basis(connection)
            request = self._search_horizon(connection, request, basis)
            return self._routing_rows(connection, request, airports, transfers)

    def search_itineraries(self, request: RouteSearchRequest) -> RouteSearchResult:
        revision = self.check_page_revision(request.data_revision)
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._routing_catalog(connection, request)
            return self._projected_search(
                connection, request, airports, transfers, revision=revision
            )

    @staticmethod
    def _routing_catalog(connection, request):
        endpoints = [request.destination]
        if hasattr(request, "origin"):
            endpoints.append(request.origin)
        airports = tuple(
            Airport.model_validate(dict(row))
            for row in connection.execute(
                "SELECT iata,city_id,timezone FROM airports "
                "WHERE (:changes AND city_id IS NOT NULL) OR iata IN (SELECT value FROM json_each(:endpoints))",
                {"changes": request.allow_airport_change, "endpoints": json.dumps(endpoints)},
            )
        )
        transfers = (
            tuple(
                AirportTransfer.model_validate(dict(row))
                for row in connection.execute(
                    "SELECT * FROM airport_transfers ORDER BY arrival_iata,departure_iata"
                )
            )
            if request.allow_airport_change
            else ()
        )
        return airports, transfers

    def _hydrate_flights(self, connection, flights, basis):
        keys = {flight_key(flight) for flight in flights}
        if not keys:
            return {}
        rows = self._window(connection, identities=keys)
        records = {
            flight_key(row): row.model_copy(update={"time_basis": basis})
            if basis != "scheduled"
            else row
            for row in rows
        }
        if records.keys() != keys:
            raise RoutingError(
                "routing_details_missing", "A selected flight's details are unavailable"
            )
        for flight in flights:
            detail = records[flight_key(flight)]
            if any(
                getattr(detail, field) != getattr(flight, field)
                for field in (
                    "origin_iata",
                    "destination_iata",
                    "departure_lower",
                    "departure_upper",
                    "arrival_lower",
                    "arrival_upper",
                )
            ):
                raise RoutingError(
                    "routing_projection_mismatch",
                    "Selected flight details differ from routing facts",
                )
        return records

    @staticmethod
    def _hydrate_airports(connection, codes):
        if not codes:
            return []
        return [
            Airport.model_validate(dict(row))
            for row in connection.execute(
                "SELECT * FROM airports WHERE iata IN (SELECT value FROM json_each(?)) ORDER BY iata",
                (json.dumps(sorted(codes)),),
            )
        ]

    def _projected_search(self, connection, request, airports, transfers, *, revision):
        if request.data_revision is not None:
            self.check_page_revision(request.data_revision)
        basis = request.time_basis or self._default_basis(connection)
        request = self._search_horizon(connection, request, basis)
        calculation = calculate_database_routes(
            connection,
            request,
            airports,
            transfers,
            basis,
            revision=revision,
            include_replay_identity=not self.serving_only,
        )
        hydrated = self._hydrate_flights(connection, calculation.flights, basis)
        result = calculation.materialize(hydrated)
        result.data_revision = revision
        result.diagnostics.stats.records_hydrated = len(hydrated)
        codes = {request.origin, request.destination}
        codes.update(
            code for f in hydrated.values() for code in (f.origin_iata, f.destination_iata)
        )
        result.airports = self._hydrate_airports(connection, codes)
        return result

    def search_local(self, request: LocalRouteSearchRequest) -> LocalRouteSearchResult:
        revision = self.check_page_revision(request.data_revision)
        with self.connect() as connection:
            connection.execute("BEGIN")
            airports, transfers = self._routing_catalog(connection, request)
            query = request.utc_request(airports)
            result = self._projected_search(
                connection, query, airports, transfers, revision=revision
            )
        by_code = {a.iata: a for a in airports}
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

    def readiness(self):
        """Check readable storage and the serving contract without scanning flight facts."""
        with self.connect() as connection:
            connection.execute("BEGIN")
            require_serving(connection)
            contract = connection.execute(
                "SELECT contract,version FROM schema_metadata WHERE id=1"
            ).fetchone()
            if tuple(contract or ()) != ("flight-legs", 1):
                raise sqlite3.DatabaseError("Unsupported flight schema contract")
            # Preparing the public read view verifies its tables and columns.
            # Dataset counts and default time basis belong to the catalog API.
            connection.execute("SELECT leg_id,time_basis FROM searchable_legs LIMIT 0")
        return dict(
            status="ready",
            schema_contract="flight-legs",
            schema_version=1,
        )

    def web_catalog(self, *, include_airports=True) -> WebCatalog:
        """Reuse unchanged statistics; writers invalidate through DB/WAL file metadata."""
        with self._catalog_lock:
            revision = self._catalog_revision()
            cached = self._catalog_cache.get(include_airports)
            if not include_airports and (not cached or cached[0] != revision):
                cached = self._catalog_cache.get(True)
            if cached and cached[0] == revision:
                return cached[1].model_copy(
                    deep=True,
                    update={}
                    if include_airports
                    else {"airports": [], "airports_truncated": False},
                )
            result = self._read_web_catalog(include_airports=include_airports)
            if revision == self._catalog_revision():
                self._catalog_cache[include_airports] = (revision, result.model_copy(deep=True))
            return result

    def _read_web_catalog(self, *, include_airports) -> WebCatalog:
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
            # Check admission a bounded number of times per fact. Repeating a
            # flattened eligibility expression in each aggregate multiplies JSON
            # evidence checks across every time basis on multi-million-row files.
            complete = " OR ".join(f"({condition})" for condition in conditions.values())
            facts = dict(
                connection.execute(f"""SELECT COUNT(*) total_record_count,
                COUNT(*) FILTER (WHERE status <> 'cancelled' AND ({complete}) AND {ADMITTED_SQL}) flight_count,
                COALESCE(SUM({review_recommended_sql()}),0) needs_review_count,
                COALESCE(SUM(status = 'cancelled'),0) cancelled_count,
                MIN(last_seen_us) oldest_fact_refresh, MAX(last_seen_us) newest_fact_refresh
                FROM flight_legs f WHERE NOT EXISTS (
                    SELECT 1 FROM flight_tombstones t
                    WHERE t.source_id=f.source_id AND t.source_record_key=f.source_record_key
                )""").fetchone()
            )
            for basis in conditions:
                row = connection.execute(
                    "SELECT COUNT(*),MIN(departure_lower_us),MAX(departure_lower_us) FROM searchable_legs WHERE time_basis=?",
                    (basis,),
                ).fetchone()
                facts[f"{basis}_count"], facts[f"{basis}_first"], facts[f"{basis}_last"] = row
            for key in ("oldest_fact_refresh", "newest_fact_refresh"):
                if facts[key] is not None:
                    facts[key] = EPOCH + timedelta(microseconds=facts[key])
            facts["providers"] = [
                r[0]
                for r in connection.execute(
                    "SELECT s.code FROM data_sources s WHERE EXISTS "
                    "(SELECT 1 FROM flight_legs f WHERE f.source_id=s.id AND NOT EXISTS "
                    "(SELECT 1 FROM flight_tombstones t WHERE t.source_id=f.source_id "
                    "AND t.source_record_key=f.source_record_key)) ORDER BY s.code"
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
            rows = (
                list(
                    connection.execute("""WITH active_codes(iata) AS (
                SELECT origin_iata FROM flights UNION SELECT destination_iata FROM flights)
                SELECT a.*
                FROM airports a ORDER BY CASE WHEN a.iata IN active_codes THEN 0 ELSE 1 END,a.iata LIMIT 1001""")
                )
                if include_airports
                else []
            )
        return WebCatalog(
            airports=sorted((dict(r) for r in rows[:1000]), key=lambda row: row["iata"]),
            airports_truncated=len(rows) > 1000,
            dataset=facts,
            suggested_route=dict(suggestion) if suggestion else None,
        )

    def web_bootstrap(self) -> WebBootstrap:
        """Load search defaults and suggestions without archive-wide statistics."""
        with self._bootstrap_lock:
            revision = self._catalog_revision()
            if self._bootstrap_cache and self._bootstrap_cache[0] == revision:
                return self._bootstrap_cache[1].model_copy(deep=True)
            result = self._read_web_bootstrap()
            if revision == self._catalog_revision():
                self._bootstrap_cache = (revision, result.model_copy(deep=True))
            return result

    def _read_web_bootstrap(self) -> WebBootstrap:
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
                SELECT a.*
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
        onward_days: int | None = None,
    ) -> SearchAvailability:
        origin, destination = airport_code(origin), airport_code(destination)
        if onward_days is not None and onward_days < 0:
            raise ValueError("Arrival window must be nonnegative")
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

        latest_arrival = max((value[1] for value in arrivals), default=None)

        def bounds(day, last=None):
            try:
                start = resolve_local(datetime.combine(day, time.min), zone)
                end = resolve_local(
                    datetime.combine((last or day) + timedelta(days=1), time.min), zone
                )
                upper = (
                    (end + timedelta(days=onward_days))
                    if onward_days is not None
                    else max(
                        end,
                        (
                            EPOCH
                            + timedelta(
                                microseconds=latest_arrival
                                if latest_arrival is not None
                                else utc_us(end)
                            )
                        ),
                    )
                )
                return start, upper
            except (OverflowError, ValueError) as error:
                raise RoutingError(
                    "invalid_local_dates", "Departure date is outside the supported range."
                ) from error

        def inbound_count(start, end, *, first_only=False):
            lower, upper = utc_us(start), utc_us(end)
            index = bisect_left(arrivals, (lower, -1))
            count = 0
            for offset in range(index, bisect_left(arrivals, (upper + 1, -1))):
                _, latest = arrivals[offset]
                count += latest <= upper
                if first_only and count:
                    return count
            return count

        dates = [day for day in sorted(outbound) if inbound_count(*bounds(day), first_only=True)]
        selected = departure_date if departure_date in dates else next(iter(dates), departure_date)
        last = (
            departure_last_date
            if departure_last_date in dates and selected and departure_last_date >= selected
            else None
        )
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
        if not codes:
            raise ValueError("Choose at least one airport for coverage.")
        counts = {
            code: dict(inbound=0, outbound=0, stored_inbound=0, stored_outbound=0) for code in codes
        }
        with self.connect() as connection:
            connection.execute("BEGIN")
            basis = time_basis or self._default_basis(connection)
            if basis not in ("scheduled", "observed", "actual"):
                raise ValueError("Unknown flight time basis")
            for table, prefix in (("flight_legs", "stored_"), ("searchable_legs", "")):
                for column, direction in (
                    ("origin_iata", "outbound"),
                    ("destination_iata", "inbound"),
                ):
                    where = (
                        "f.time_basis=?"
                        if table == "searchable_legs"
                        else (
                            "NOT EXISTS (SELECT 1 FROM flight_tombstones t WHERE t.source_id=f.source_id "
                            "AND t.source_record_key=f.source_record_key)"
                        )
                    )
                    params = [json.dumps(codes)] + ([basis] if table == "searchable_legs" else [])
                    for code, count in connection.execute(
                        f"SELECT {column},COUNT(*) FROM {table} f WHERE {column} IN "
                        f"(SELECT value FROM json_each(?)) AND {where} GROUP BY {column}",
                        params,
                    ):
                        counts[code][prefix + direction] = count
        return AirportCoverage(
            time_basis=basis,
            airports=[AirportTraffic(airport=code, **counts[code]) for code in codes],
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
        self, city_id: str | None = None, query: str = "", limit: int = 500, offset: int = 0
    ) -> list[Airport]:
        clauses, params = [], []
        if city_id:
            clauses.append("city_id = ?")
            params.append(city_id)
        with self.connect() as connection:
            if query:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(airports)")}
                matches = ["iata = ?", "name LIKE ?"]
                params.extend((query.upper(), "%" + query + "%"))
                if "icao" in columns:
                    matches.extend(["icao = ?", "municipality LIKE ?"])
                    params.extend((query.upper(), "%" + query + "%"))
                clauses.append("(" + " OR ".join(matches) + ")")
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            order = "city_id,iata"
            if query:
                order = "CASE WHEN iata=? THEN 0 ELSE 1 END," + order
                params.append(query.upper())
            rows = connection.execute(
                "SELECT * FROM airports" + where + f" ORDER BY {order} LIMIT ? OFFSET ?",
                [*params, limit, min(offset, 2**63 - 1)],
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
        offset: int = 0,
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
                offset=offset,
            )

    def routes(
        self,
        start: datetime,
        end: datetime,
        origin: str | None = None,
        destination: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[RouteSummary]:
        where, params = self._where(start, end, origin, destination)
        where += f" AND {ADMITTED_SQL}"
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT origin_iata, destination_iata, COUNT(*) flight_count,
                GROUP_CONCAT(DISTINCT airline_iata) airlines, MIN(departure) first_departure,
                MAX(departure) last_departure FROM flights WHERE {where}
                GROUP BY origin_iata, destination_iata
                ORDER BY flight_count DESC, origin_iata, destination_iata LIMIT ? OFFSET ?""",
                [*params, limit, min(offset, 2**63 - 1)],
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
