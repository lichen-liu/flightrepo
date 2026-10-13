# Routing engine

## Behavior and limits

The engine finds **schedule-feasible, simple airport paths** through exact dated flight records. It uses scheduled timestamps, excludes cancelled records, keeps provider identities, and checks configured transfer rules. It does not infer ticket protection, immigration, baggage transfer, inventory, published MCT or successful historical passenger connections from those records. Actual timestamps remain facts but are not the time basis for this search.

- Initial departure is in `[departure_start, departure_end)`. All arrivals must be at or before the inclusive `horizon_end`. Inputs require UTC offsets and normalize to UTC; local wall-clock/DST interpretation is a separate client/boundary concern.
- Connection minima and maxima are inclusive. Same-airport allowance defaults to 45 minutes; changing airports needs opt-in, same-city metadata and an explicitly configured directed rule. The larger of the user minimum and rule minimum applies. These are supplied allowances, not guarantees.
- A path stops at the exact destination airport. It cannot revisit an airport, including returning through a ground transfer to an earlier departure airport. City membership does not merge physical airport vertices. Roundtrips/repeated-airport tours and ground-only arrival at the destination are outside this request grammar.
- Full search supports one to four flight legs, avoided airports, and an explicit ranking. A departure window is at most 31 days, with an arrival horizon at most 31 days after its end. A continuation horizon remains within 31 days of the first preserved departure.
- Rebuild retains and validates every prefix identity and cancellation status. Retained same-airport gaps must be nonnegative; changing the current minimum does not rewrite an earlier gap. Configured physical airport-change minima still apply. Airport-change permission is request-wide. The active maximum/not-before applies only to the edited gap; subsequent gaps use its minimum. Per-gap policies are a future extension.
- Rebuild keeps an explicit next flight even if no complete suffix is loaded. Budget exhaustion preserves a feasible suffix already found, labels search partial and avoids an optimality claim.

`complete` on a plan means it reaches the requested destination. `diagnostics.exhaustive` means the search/continuation calculation finished over the request's **loaded, scoped snapshot** without a data/candidate cutoff. It is not evidence of exhaustive provider coverage or commercial feasibility. An empty result with partial diagnostics is not proof that no route exists.

Default budgets are 10,000 candidate rows and 100,000 candidate visits. Requests may lower them; hard maxima are 20,000 rows and 200,000 visits. Exact prefix/explicit-choice anchors are separately retained, adding at most five records. The search retains at most 100 ranked results. The default continuation option page has 100 entries (maximum 500), always including the selected continuation; `options_total` counts eligible next flights in loaded data and is unknown when the candidate budget interrupts enumeration. `options_truncated` identifies an incomplete option list.

The row budget bounds loaded flight facts, not SQLite VM steps, metadata rows, elapsed time or concurrent memory. These resource limits do not establish production throughput.

## Modules

| Module | Responsibility |
|---|---|
| `core/routing/models.py` | UTC/query validation, allowed rankings, hard budgets, diagnostics and stable error codes |
| `core/routing/policy.py` | Shared connection assessment, directed transfer permissions, airport avoidance and cycle checks |
| `core/routing/index.py` | Departure arrays per airport, binary-search windows, sorted merges and a safe static hop lower bound |
| `core/routing/snapshot.py` | Supplied fact set, duplicate-identity validation, content digest and replay serialization |
| `core/routing/session.py` | Request-local index, counters, bounded trace and candidate budget; no shared mutable cache |
| `core/routing/search.py` | Full simple-path traversal and bounded heap for exact ranked top-k |
| `core/planning.py` | Prefix validation, earliest eligible next departure and memoized best suffix |
| `core/routing/debug.py` | Pure replay document/calculation; no DB or filesystem access |
| `server/reader.py` | Read-only, query-scoped loading from one SQLite transaction snapshot |
| `server/api.py`, `admin/client.py` | HTTP/terminal adapters; admin can save scoped facts for replay |

Public calculation functions receive validated typed requests/facts and perform no persistence, network I/O or mutation of caller inputs. Treat supplied models as read-only during a calculation. The core imports no server, admin or ingestion package; boundary tests enforce this.

## Traversal and ranking

The reader first reads airport metadata and flight anchors in a read transaction. It loads flight departures only from airports reachable within the flight-leg bound. Roots use the exact initial departure window. Onward reads conservatively cover the remaining time horizon: a city airport considered as a short first connection may be reached later by another path. Reusing that airport's too-narrow initial window would drop valid later departures; a regression test covers this case. Irrelevant disconnected airport schedules do not consume the row budget.

The partial SQLite index is `(origin_iata, departure_scheduled, provider, provider_id) WHERE status != 'cancelled'`. SQL orders deterministically before applying limits. The pure index then uses binary searches on actual minimum/maximum/not-before windows. Its range iterator starts at the calculated position without scanning preceding records or copying a full schedule. Allowed transfer origins and reverse memberships are pre-indexed.

The engine traverses every eligible simple path inside its leg/work bounds. A reverse graph supplies a minimum required number of remaining flight legs, ignoring time and visited airports. It is a safe lower bound, used only to reject branches that cannot reach the destination within the remaining count. It is not a guessed geographic/time score. There is no unsafe “earliest arrival per airport wins” pruning: different visited airports and maximum waits can make an apparently later arrival necessary.

Full-search ranking is explicit and deterministic:

| Ranking | Ascending comparison tuple |
|---|---|
| `fewest_legs` (default) | leg count, exact elapsed seconds, initial departure, final arrival, provider/ID sequence |
| `shortest_elapsed` | exact elapsed seconds, leg count, final arrival, initial departure, provider/ID sequence |
| `earliest_arrival` | final arrival, leg count, exact elapsed seconds, initial departure, provider/ID sequence |

The displayed duration is whole minutes; ranking uses full timestamp precision. A worst-first heap retains the best `limit` paths in `O(log limit)` per retained replacement. Finding enough results does not stop traversal. If candidate/data budgets interrupt it, the engine returns only proven feasible results and explicitly withholds an exhaustive ranking claim.

Automatic continuation chooses the earliest departing next flight that has a complete suffix. Each suffix ranks by final arrival, suffix leg count, then exact ID sequence. Explicit `chosen_next` overrides that automatic choice. Memoization keys include preceding dated identity, remaining legs and every visited airport; caches are request-local. Incompletely searched suffixes are not cached as optimal. Option paging changes response size, not the automatic selection decision.

Sorting/indexing is `O(N log N)` over loaded records; graph construction/hop bounds are linear in loaded edges and permitted transfer memberships. Simple-path enumeration remains combinatorial on dense networks; the leg count and candidate budget constrain it. This is a deliberate bounded prototype algorithm, not a claim that enumerating global networks is cheap.

## Diagnostics and replay

Responses include:

- `engine_version`, a SHA-256 `snapshot_id` over the actual flight/airport/transfer facts and data-cutoff flag, and a `request_id` over that digest plus the normalized request/version. Conflicting records for the same identity are rejected; identical repeated flight records are deduplicated. Input order does not affect route order, IDs or traces.
- Ranking, scoped `exhaustive`, `termination_reasons` (`data_limit`, `candidate_limit`) and a separate result-page limit indicator.
- Loaded row/origin counts, checked candidates, time-window skips, expanded states, memo hits and route count. Window skips count rejected entries per lookup, not distinct physical flights. Full-search route count counts completed examined paths; continuation route count counts completed assessed next-flight suffixes, not all possible suffix paths.
- Optional capped events (`trace: true`): current dated path, candidate identity, decision reason and measured wait/minimum. Default cap is 200, hard cap 500. Events omitted after the cap are counted. Index-pruned time ranges are summarized rather than producing one event per skipped record.
- HTTP 422 selection errors with stable codes and flight/gap context where known, e.g. `chosen_next_connection_too_short`, wait 30, minimum 45. Missing/cancelled choices are rejected rather than substituted.

Itinerary IDs hash the exact provider/ID sequence. A schedule/provenance revision changes the snapshot digest even when the path identity remains the same. This ID is not a persisted historical DB snapshot address. To replay, save the scoped facts with the request/result. Replay checks the engine/format version, performs no DB access and compares the complete regenerated result against the saved result. It preserves partial-search budgets and diagnostics too.

## API and compatibility

`POST /v1/itineraries/search` accepts `RouteSearchRequest` and returns `{query, items, diagnostics}`. Every item has a stable itinerary ID, complete legs, assessed connections and scheduled elapsed time. `POST /v1/plans/rebuild` adds diagnostics and option-page metadata to the existing plan response; it remains a read-only calculation.

Legacy `GET /v1/itineraries` retains its list shape and default fewest-legs ranking. It now delegates to the shared engine. It accepts optional `horizon_end`; the documented default is **end + 7 days**, an inclusive arrival bound, independent of the connection allowance. This replaces the old erroneous minute-to-hour fetch-horizon coupling without assuming flight durations equal layover bounds. If a capped search cannot establish the list ranking, it returns **409 `incomplete_search`** with diagnostics and directs clients to the richer POST endpoint. This is an intentional compatibility behavior change. CLI `itineraries --horizon` supplies the same explicit arrival bound.

Naive/mixed-offset query times now return 422 at the HTTP boundary for flights/routes/itineraries, instead of leaking a Python comparison/reader error. Flight imports reject malformed airport codes and empty provider identities. Existing valid legacy flight rows remain queryable without airport metadata.

The updater initializes the additive catalog/index schema. For an existing installation, run `.venv/bin/python -m admin stats` once before using the new reader. All local DBs and replay/debug JSON remain under ignored `var/`.

## Try locally

```sh
.venv/bin/python scripts/seed_connection_demo.py
FLIGHT_DATABASE_PATH=var/connections-demo.db .venv/bin/python -m admin search-itineraries var/route-search-request.json --snapshot-out var/routing-replay.json
.venv/bin/python -m admin replay-routing var/routing-replay.json
```

The seed creates the separate twelve-flight demo DB and both request examples, leaving normal flight facts intact. Change ranking, arrival horizon, avoidance, `trace` or `limits` in `var/route-search-request.json`. For a budget experiment use `"limits": {"max_candidates": 1}` and inspect `diagnostics`.

For continuation replay:

```sh
FLIGHT_DATABASE_PATH=var/connections-demo.db .venv/bin/python -m admin rebuild var/connection-request.json --snapshot-out var/connection-replay.json
.venv/bin/python -m admin replay-routing var/connection-replay.json
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
