# Routing engine

## Behavior and limits

The engine finds **schedule-feasible, simple airport paths** through exact dated flight records. It uses the selected scheduled, observed or actual time basis, excludes cancelled records, keeps provider identities, and checks configured transfer rules. It does not infer ticket protection, immigration, baggage transfer, inventory, published MCT or successful historical passenger connections from those records. Observed searches preserve complete uncertainty intervals when assessing waits.

- Initial departure is in `[departure_start, departure_end)`. All arrivals must be at or before the inclusive `horizon_end`. Inputs require UTC offsets and normalize to UTC; local wall-clock/DST interpretation is a separate client/boundary concern.
- Connection minima and maxima are inclusive. Same-airport allowance defaults to 45 minutes; changing airports needs opt-in, same-city metadata and an explicitly configured directed rule. The larger of the user minimum and rule minimum applies. These are supplied allowances, not guarantees.
- A path stops at the exact destination airport. It cannot revisit an airport, including returning through a ground transfer to an earlier departure airport. City membership does not merge physical airport vertices. Roundtrips/repeated-airport tours and ground-only arrival at the destination are outside this request grammar.
- Full search supports an optional flight-leg bound (`null` means unrestricted), avoided airports, and an explicit ranking. Simple paths cannot revisit airports, which supplies a finite graph-derived maximum. Departure windows, onward horizons and continuation spans have no fixed day ceiling. An omitted search horizon uses the latest available destination arrival (at least the end of the departure window). Explicit arrival horizons remain inclusive user filters. Via/avoid lists have no fixed count ceiling; duplicate and endpoint/cycle rules still apply.
- Rebuild retains and validates every prefix identity and cancellation status. Retained same-airport gaps must be nonnegative; changing the current minimum does not rewrite an earlier gap. Configured physical airport-change minima still apply. Airport-change permission is request-wide. The active maximum/not-before applies only to the edited gap; subsequent gaps use its minimum. Per-gap policies are a future extension.
- Rebuild keeps an explicit next flight even if no complete suffix is loaded. Budget exhaustion preserves a feasible suffix already found, labels search partial and avoids an optimality claim.

`complete` on a plan means it reaches the requested destination. `diagnostics.exhaustive` means the search/continuation calculation finished over the request's **loaded, scoped snapshot** without a data/candidate cutoff. It is not evidence of exhaustive provider coverage or commercial feasibility. An empty result with partial diagnostics is not proof that no route exists.

Database-backed search and connection editing have **no 10,000-flight loading cap**. SQL filters by time basis, departure/horizon bounds, avoided airports and airport reachability, then projects only flight identities, airport endpoints and time bounds. A time-compatible reverse traversal removes flights that cannot reach the destination and supplies a safe remaining-leg lower bound. Connection editing retains dead-end next-flight options. The core receives compact immutable edges, without airline/codeshare, aircraft, refresh or source-evidence metadata.

Only after calculation does the server fetch full details for the distinct flights and airports actually returned. Shared flights are fetched once in a batch. Explicit prefix/next-flight anchors are loaded for validation. Normalization, SQL projection, routing and result-detail reads share one SQLite read transaction, so a concurrent writer cannot mix old routing facts with newer display details. The browser receives completed journeys and display metadata; it does no route search.

The candidate-work budget remains 100,000 visits by default, with a hard maximum of 200,000. Results remain explicitly partial if that work budget is exhausted. Departure/horizon bounds and simple-path rules still apply. There is no SQL row `LIMIT` in database route projection. The caller-supplied full-record snapshot/replay path retains `max_records` (default 10,000; maximum 20,000) to bound explicit debug documents; ordinary database routing ignores that legacy full-record limit. CLI search uses SQL projection unless `--snapshot-out` explicitly requests a replayable full-record snapshot.

The search returns at most 100 ranked results. The default continuation option page has 100 entries (maximum 500), always including an explicit selection; `options_total` is unknown when the work budget interrupts enumeration. These budgets do not establish production throughput or global provider coverage.

## Modules

| Module | Responsibility |
|---|---|
| `core/routing/models.py` | UTC/query validation, allowed rankings, hard budgets, diagnostics and stable error codes |
| `core/routing/policy.py` | Shared connection assessment, directed transfer permissions, airport avoidance and cycle checks |
| `core/routing/index.py` | Departure arrays per airport, binary-search windows, sorted merges and a safe static hop lower bound |
| `core/routing/projection.py` | Compact routing edges and a digest of routing inputs, independent of display metadata |
| `core/routing/snapshot.py` | Supplied fact set, duplicate-identity validation, content digest and replay serialization |
| `core/routing/session.py` | Request-local index, counters, bounded trace and candidate budget; no shared mutable cache |
| `core/routing/search.py` | Full simple-path traversal and bounded heap for exact ranked top-k |
| `core/planning.py` | Prefix validation, earliest eligible next departure and memoized best suffix |
| `core/routing/debug.py` | Pure replay document/calculation; no DB or filesystem access |
| `server/routing_projection.py` | SQL time/basis filtering, conservative forward/reverse airport components and compact edge projection |
| `server/reader.py` | Read-only, query-scoped loading from one SQLite transaction snapshot |
| `server/api.py`, `admin/client.py` | HTTP/terminal adapters; admin can save scoped facts for replay |

Public calculation functions receive validated typed requests/facts and perform no persistence, network I/O or mutation of caller inputs. Treat supplied models as read-only during a calculation. The core imports no server, admin or ingestion package; boundary tests enforce this.

## Traversal and ranking

The reader projects admitted `searchable_legs` columns from the existing schema. Recursive SQL traversals use distinct airport sets rather than enumerating paths in SQL. Exact transfer eligibility, waits, via order and cycles remain engine decisions. Dataset/source metadata is hydrated only for the response. No ingestion, admission or schema changes are involved.

The in-memory departure index uses binary searches on minimum/maximum/not-before windows. Reverse time-compatible traversals give each flight a minimum suffix length and an earliest possible destination arrival. These independent bounds may come from different paths; ignoring visited airports and required vias makes them optimistic. Database search uses a best-first frontier with these admissible bounds for each supported ranking. It stops only when every unexpanded bound is no better than the worst retained result, or when the work budget is reached. Thus finding 20 routes alone is insufficient to claim a complete ranking. Full-record snapshots retain their deterministic exhaustive traversal for replay.

There is no unsafe “earliest arrival per airport wins” pruning: different visited airports and maximum waits can make a later arrival necessary. Connection metadata and flight choices remain exact, dated identities.

Full-search ranking is explicit and deterministic:

| Ranking | Ascending comparison tuple |
|---|---|
| `fewest_legs` (default) | leg count, exact elapsed seconds, initial departure, final arrival, provider/ID sequence |
| `shortest_elapsed` | exact elapsed seconds, leg count, final arrival, initial departure, provider/ID sequence |
| `earliest_arrival` | final arrival, leg count, exact elapsed seconds, initial departure, provider/ID sequence |

The displayed duration is whole minutes; ranking uses full timestamp precision. A worst-first heap retains the best `limit` paths in `O(log limit)` per retained replacement. Database search may stop when lower bounds prove that no remaining path can improve the ranking; result count alone is not a stopping condition. If candidate/data budgets interrupt it, the engine returns only proven feasible results and explicitly withholds an exhaustive ranking claim.

Automatic continuation chooses the earliest departing next flight that has a complete suffix. Each suffix ranks by final arrival, suffix leg count, then exact ID sequence. Explicit `chosen_next` overrides that automatic choice. Memoization keys include preceding dated identity, remaining legs, every visited airport and ordered via progress; caches are request-local. Incompletely searched suffixes are not cached as optimal. Option paging changes response size, not the automatic selection decision.

Sorting/indexing is `O(N log N)` over loaded records; graph construction/hop bounds are linear in loaded edges and permitted transfer memberships. Simple-path enumeration remains combinatorial on dense networks; the leg count and candidate budget constrain it. This is a deliberate bounded prototype algorithm, not a claim that enumerating global networks is cheap.

## Diagnostics and replay

Responses include:

- `engine_version`, a SHA-256 `snapshot_id` over the actual flight/airport/transfer facts and data-cutoff flag, and a `request_id` over that digest plus the normalized request/version. Conflicting records for the same identity are rejected; identical repeated flight records are deduplicated. Input order does not affect route order, IDs or traces.
- Ranking, scoped `exhaustive`, `termination_reasons` (`data_limit`, `candidate_limit`) and a separate result-page limit indicator.
- `scope: "sql_projection"` for database calculations; `rows_loaded` counts compact routing edges and `records_hydrated` counts distinct full flight details fetched for the response (including validation anchors for editing). Explicit snapshot calculations retain `scope: "loaded_snapshot"`. Also included are origin counts, checked candidates, time-window skips, expanded states, memo hits and route count. Window skips count rejected entries per lookup, not distinct physical flights. Full-search route count counts completed examined paths; continuation route count counts completed assessed next-flight suffixes, not all possible suffix paths.
- Optional capped events (`trace: true`): current dated path, candidate identity, decision reason and measured wait/minimum. Default cap is 200, hard cap 500. Events omitted after the cap are counted. Index-pruned time ranges are summarized rather than producing one event per skipped record.
- HTTP 422 selection errors with stable codes and flight/gap context where known, e.g. `chosen_next_connection_too_short`, wait 30, minimum 45. Missing/cancelled choices are rejected rather than substituted.

Itinerary IDs hash the exact provider/ID sequence. SQL-projection digests cover the routing fields, selected time basis and connection metadata. Display-only changes do not alter that digest. Full-record replay digests also cover provenance fields. This ID is not a persisted historical DB snapshot address. To replay, save the scoped facts with the request/result. Replay checks the engine/format version, performs no DB access and compares the complete regenerated result against the saved result. It preserves partial-search budgets and diagnostics too.

## API and compatibility

`POST /v1/itineraries/search` accepts `RouteSearchRequest` and returns the allowlisted public `{query, items, diagnostics}` contract. Items contain opaque flight IDs, itinerary timing and assessed connections. Public diagnostics contain only completion/limit status and route counts; the replay hashes, events and provider identities described above remain internal/admin-only. `POST /v1/plans/rebuild` accepts `flight_id` references and returns the same public flight model with option-page metadata. Both remain read-only calculations. See [public API boundaries](../database-schema.md#public-api-boundary).

UTC and local-date search responses include `search_time_seconds`, measured with
a monotonic clock from revision/catalog lookup through result hydration. It
excludes HTTP transport and public serialization. Empty, partial and paginated
results each carry their own duration. The browser keeps this time beneath the
results; loading more journeys replaces it with the latest page's search time.
The legacy list endpoint exposes the duration in `X-Search-Time-Seconds`.
Snapshot replay remains deterministic and does not measure execution time.

Single-destination database windows seek the existing
`flight_legs_airport_pair` index before checking times and admission, avoiding
year-long scans of unrelated hub departures during final-leg checks. Databases
without that additive index retain the original searchable-view query.

The search, local-time search and rebuild endpoints accept `via_airport` as either a single IATA code or an ordered array such as `["LHR", "AMS"]`. Scalars remain scalars in the echoed query; arrays retain their normalized order. `null` and `[]` impose no via requirement. Every listed airport must be visited in order across the retained prefix and suffix. Arrival and permitted transfer-departure airports count chronologically; an ordinary departure from the previous arrival does not count twice. Repeated codes, endpoints, avoided vias and lists exceeding the flight-leg capacity are rejected. Four flight legs support up to three ordinary vias, or up to six distinct vias with permitted airport changes. There is no SQLite schema change.

The browser resolves departure dates in the origin's timezone and uses all available destination arrivals by default. Its optional arrival-window field sends `onward_days`, measured after the resolved departure-window end. Destination timezone metadata is optional for these cases. Explicit `arrival_by_local` remains supported by the local API and requires destination timezone metadata. Departure ranges have no fixed day ceiling and remain valid across DST changes.

Legacy `GET /v1/itineraries` retains its list shape and default fewest-legs ranking. It now delegates to the shared engine. It accepts optional `horizon_end`; the default is the latest available destination arrival, at least `end`, independent of the connection allowance. Maximum layover defaults to unrestricted. It supports `offset` and exposes the next page through the HTTP `Link` header without changing its list-shaped body. This replaces the old erroneous minute-to-hour fetch-horizon coupling without assuming flight durations equal layover bounds. If a capped search cannot establish the list ranking, it returns **409 `incomplete_search`** with diagnostics and directs clients to the richer POST endpoint. This is an intentional compatibility behavior change. The unified CLI `search --horizon` supplies an explicit arrival bound and always returns the richer search result with diagnostics.

Naive/mixed-offset query times now return 422 at the HTTP boundary for flights/routes/itineraries, instead of leaking a Python comparison/reader error. Flight imports reject malformed airport codes and empty provider identities. Existing valid legacy flight rows remain queryable without airport metadata.

The updater initializes the additive catalog/index schema. For an existing installation, run `.venv/bin/python -m admin maintenance init` once before using the new reader. All local DBs and replay/debug JSON remain under ignored `var/`.

## Try locally

```sh
.venv/bin/python scripts/seed_connection_demo.py
FLIGHT_DATABASE_PATH=var/connections-demo.db .venv/bin/python -m admin search --request var/route-search-request.json --snapshot-out var/routing-replay.json
.venv/bin/python -m admin debug replay var/routing-replay.json
```

The seed creates the separate twelve-flight demo DB and both request examples, leaving normal flight facts intact. Change ranking, arrival horizon, avoidance, `trace` or `limits` in `var/route-search-request.json`. For a budget experiment use `"limits": {"max_candidates": 1}` and inspect `diagnostics`.

For continuation replay:

```sh
FLIGHT_DATABASE_PATH=var/connections-demo.db .venv/bin/python -m admin debug continue-route var/connection-request.json --snapshot-out var/connection-replay.json
.venv/bin/python -m admin debug replay var/connection-replay.json
```

Add the exact `lgw-ams-overnight` demo choice to the rebuild request to inspect the next-day final flight. Replay prints `matches_saved_result`; modifying the saved facts/result is detectable. Files are internal debugging data, not another storage service.

Swagger can use a separate demo instance:

```sh
FLIGHT_DATABASE_PATH=var/connections-demo.db .venv/bin/uvicorn server.api:app --port 8002
```

## Development

The Python suite compares both the pure engine and SQLite loading with an independent exhaustive oracle on generated networks. It also covers connection boundaries, cycles, via and next-airport constraints, budget exhaustion, stale selections and concurrent updates.

```sh
python -m pytest -q
python scripts/benchmark_routing.py --output var/routing-benchmark.json
```

The benchmark measures synthetic CPU/SQLite workloads; its output stays under ignored `var/`.

Add policy rules and rankings with independent-oracle cases. Keep snapshot loading conservative unless narrower windows are proven safe. Result count must not stop traversal, and an earlier arrival alone does not justify dropping another path. Update the replay engine version when calculation semantics change.
