# Backend limits audit

Audited on October 17, 2026, initially against PR #22 revision `3b7162e` and
updated after its merge and the search-scope follow-up. This covers the public
search path, calendar and listing APIs, private snapshot/replay, ingestion and hosted serving. It describes code
behavior, not a deployment.

Search and continuation now have no default candidate-count cutoff. An explicit
`limits.max_candidates` remains available to callers and has no fixed maximum;
`null` disables it. The serving SQL deadline is also opt-in, disabled by default.
Concurrency admission still rejects excess requests explicitly without truncating
the graph or ranking of accepted requests.

## What the 100,000-flight benchmark means

`scripts/benchmark_storage.py --rows 100000` generates 100,000 synthetic records
in disposable databases. It compares index/write costs. It neither configures nor
limits the server. The script itself accepts 1–1,000,000 fixture rows; that ceiling
only prevents an accidental oversized local benchmark.

The separate **100,000 candidate-check search budget** was a real restriction
in the initial audit. It has been removed from the defaults; the benchmark size
has never configured either SQL loading or routing.

## Search restrictions reviewed and removed

| Restriction | Actual behavior | Assessment |
| --- | --- | --- |
| Candidate checks: unlimited by default; optional positive caller budget | `SearchLimits.max_candidates=null` has no count cutoff. An explicit budget interrupts search and continuation with `candidate_limit` and incomplete diagnostics. Visits count repeated examination along different paths, not distinct SQL rows. | The former 100,000 default and 200,000 maximum have been removed. Explicit budgets can still omit better or matching journeys and must not be presented as complete ranking. |
| Serving read deadline: disabled by default; optional positive operator setting | A configured monotonic deadline applies per read connection, checked every 1,000 SQLite VM instructions. An interrupt returns 503 `query_timeout`, not an empty result. Unset `FLIGHT_READ_QUERY_TIMEOUT_SECONDS` to disable it. | Explicit execution policy, not a SQL optimization. The former 15-second default and 120-second maximum have been removed. When enabled, the timer also elapses during Python work while the connection is open; it is not accumulated SQL execution time. |
| Departure span | No fixed day ceiling; ordered, representable UTC/local dates remain required. | The former 31-day ceiling and DST inconsistency are removed. |
| Arrival horizon | No fixed day ceiling in search or continuation. An omitted horizon uses the latest available destination arrival, at least the departure-window end. | The former 31/62-day ceilings are removed. |
| Calendar/browser horizon | Uses all available arrival data by default. Optional `onward_days` is shared by availability and search; precise local/UTC arrival deadlines remain supported. | The fixed seven-day eligibility window is removed. |
| Via and avoided airports | No count ceiling. Codes, no-repeat vias, endpoint exclusions and conflicts remain validated. | The six-via/fifty-avoid ceilings and browser character cap are removed. |
| Connection allowances | Nonnegative minimum and nullable maximum, without arbitrary numeric ceilings. Maximum must be at least minimum. Public adapters agree; default maximum is unrestricted. | The 1,440/44,640-minute ceilings and tighter legacy validators are removed. |
| Continuation gap semantics | The edited gap uses its requested maximum/not-before. Later gaps use the minimum without a maximum. | Continuation and full-search policies differ; an edited journey may exceed the maximum used in its original search. This is documented behavior, not a row-loading cutoff. |

Sources: [request validation](../src/core/routing/models.py),
[continuation inputs](../src/core/connections.py),
[candidate accounting](../src/core/routing/session.py),
[search traversal](../src/core/routing/search.py),
[continuation traversal](../src/core/planning.py),
[reader/deadline/calendar](../src/server/reader.py),
[local-time conversion](../src/server/web_models.py),
[HTTP adapters](../src/server/api.py), [browser request](../src/server/web/app.mjs).

## Response pages and initial suggestions

Response sizes bound each page without limiting the total results a caller can
retrieve. See [pagination and revision handling](backend-operations.md#search-scope-and-response-pages).

| Surface | Default / maximum | Consequence |
| --- | --- | --- |
| Ranked itineraries | 20 / 100 per page | `offset`, `next_offset` and `data_revision` make later ranked entries reachable. Ranking remains exact within the requested scope unless an explicit caller budget reports incomplete calculation. |
| Continuation options | 100 / 500 per page | `option_offset` and `next_option_offset` expose later entries. Selection remains independent; an out-of-page selected option is added without displacing a page entry. |
| Flights, direct route summaries, airport listing | 500 / 1,000 per page | Next links carry offset, filters and database revision. List bodies retain their existing shape. A changed dataset returns 409 rather than mixing pages. |
| Catalog/bootstrap airport suggestions | 1,000, fetched with a 1,001st sentinel | This initial cache is explicitly truncated. Airport and via lookups query the full server catalog; clients can also follow airport listing pages. |
| Via autocomplete | First eight matches | A display size only. Typing a code queries the full catalog rather than restricting discovery to bootstrap data. |
| Airport coverage | Any nonempty airport list | Grouped indexed reads use JSON parameters without one SQL column per requested airport. The former two-airport request ceiling is removed. |

Response page sizes remain bounded; every relevant entry is reachable through
pagination or narrowed server-side airport lookup. Truncated bootstrap caches
are not complete catalogs. Deep ranked pages rerun calculation and retain more
paths; pagination does not promise constant computation or memory.

## Paths that do not impose a live SQL row cap

Normal database search and continuation use `server.routing_projection.project_routes`.
Its SQL has no row `LIMIT`. SQL retains all admitted edges matching the requested
time window and conservative reachability filters. Exact timing, via order and
cycle decisions remain in the engine. Full details are fetched only for returned
flights and validation anchors.

The private `routing_snapshot` / `rebuild_snapshot` paths are different:

- Full-record debug loading defaults to 10,000 records and allows at most 20,000,
  plus validation anchors. It can return a truncated snapshot.
- Its preliminary reverse graph uses a cap of
  `max(100000, max_records * 20, max_candidates or 0)`; at defaults this is 200,000
  edges. If exceeded, it discards that incomplete pruning result and falls back
  to the bounded loader; it does not prune live searches with an incomplete graph.
- Replay document validation independently caps its flight list at 20,005. That
  fixed five-record allowance does not express an unrestricted prefix/anchor
  contract and deserves cleanup if these debugging limits are revised.
- CLI search uses live SQL projection normally. Explicit `--snapshot-out` and
  private debug continuation use the bounded full-record path.

The exposed `max_records` field is therefore misleading on a public search: it is
accepted but ignored for SQL-projected database routing. Separate replay settings
from public query inputs rather than suggesting they govern the same operation.

Other small numbers are batches or existential tests, not truncation: public
identity lookup processes **every** identity in batches of 200; metadata hydration
reads all selected identities; `LIMIT 1` chooses a default/suggested first flight
or tests existence; readiness `LIMIT 0` prepares the view without scanning it.

Trace capture defaults to 200 events and caps at 500. Omitted events are counted;
the trace cap does not stop route exploration. Public requests disable trace.

## Resource controls and domain rules

PR #22 admits two interactive reads and one separate catalog request per process;
excess requests receive 503 with `Retry-After`. Interactive concurrency is
configurable from 1 to 32. This controls simultaneous work, not which flights an
accepted query can examine. Hosted Uvicorn separately limits concurrency and
backlog to 32, including non-API requests. These limits do not establish a global
budget across replicas.

Hosted request bodies are limited to 128,000 bytes and 15 seconds to upload;
incomplete HTTP headers to 16,384 bytes. Oversized bodies receive 413. Keep-alive
is five seconds; graceful shutdown is 15 seconds; SQLite busy waits use ten seconds.
These are transport/lock controls, not a result-count cap. The body limit can
indirectly reject an exceptionally long retained prefix.

The engine has **no fixed three-stop or other default leg-count ceiling**.
`max_legs=null` uses the finite no-repeated-airport graph bound. No airport cycles,
endpoint reuse as intermediate stops, cancellation/tombstone exclusion, admitted
time evidence, selected time basis, avoided airports and opted-in transfer rules
are eligibility semantics. An unrestricted search is still restricted to the
request and that product definition; it does not mean returning cancelled flights
or accepting an impossible connection. Those rules must not be removed merely
because they reduce the set of eligible journeys.

The candidate budget does not protect every resource stage: SQL projection and
reverse reachability happen before candidate accounting. The SQL progress handler
cannot stop Python routing/reachability while it is executing Python. The routing
loops have no request-disconnect cancellation check. Removing all work controls
without addressing those facts could leave expensive work running after its
caller has gone away.

## Ingestion and administrative bounds

There is no 100,000-flight daily ingestion or serving-table row ceiling.

| Bound | Effect |
| --- | --- |
| DB-family quota: default 20 GiB, can be unlimited with `quota_bytes=null` | Pauses ingestion/publication writes on quota failure and preserves existing facts. It is not a read/query row limit. |
| Disposable extraction/download workspace: hard maximum 20 GiB | A valid selected archive day can fail to fit despite more physical disk being available. This is operational policy, not an intrinsic data limit. |
| Archive members: 32 MiB compressed and decompressed | Oversized input fails the day rather than silently committing a prefix of its flights. |
| Reference CSVs: 64 MiB; preparation response: 32 MiB; evidence document: 64 KiB | Size/ingestion validation controls, not SQL result caps. Rejections can prevent new coverage from being ingested. |
| Release discovery: 40 pages of 100 releases | Fails explicitly if requested inputs are not found by then; it does not silently claim discovery complete. |
| Provider-service date request: configured 31-day maximum and at most 50 airports; archive pipeline optional `max_days` | The provider import rejects oversized requests. Archive extraction defaults to no `max_days` ceiling and processes committed days independently. These do not limit total retained dates/rows. |
| AeroDataBox requests: sequential 12-hour windows | The adapter traverses the requested range, not only its first window. External provider/account coverage remains separate; the local code cannot guarantee upstream completeness. |
| Review queue, samples and audit cohorts | Display/report limits. Audit scans still process the selected facts; integrity/FK reports keep only the first 20 errors but treat any reported error as failure. |

Extraction gap, proximity, heading, height and timing policies also affect which
observations become facts or warnings. They are inference semantics, not query
work budgets; this audit did not change them or assert complete provider coverage.

## Verification and decisions

Read-only checks on the existing YYZ–BKK query loaded **173,798 edges**, examined
**1,558 candidates**, hydrated **30 full records**, and returned 20 journeys with
complete ranking. Thus neither the fixture size nor the default full-record debug
budget capped its eligible SQL graph.

A disposable 100,001-edge counterexample put the best direct flight last in the
departure-ordered scan. At the former default 100,000 candidate budget, the engine
returned a worse flight with `exhaustive=false`. The regression test now verifies
that default search finds the correct flight after 100,001 checks, while an
explicit 100,000 budget still reports incomplete ranking. This proves the original effect,
not just a hypothetical concern. It is not a claim that this synthetic route
volume reflects a physical service.

A separate disposable two-flight example arrived January 10 after a January 1
departure. Previously an explicit longer-horizon request found the route while
the fixed calendar arrival window offered no date. Calendar and default search
now agree on all available arrivals; an explicit shorter window can still exclude
the journey as a user-selected filter.

Regression coverage includes the former cutoff, unrestricted stops, optional SQL
deadlines, explicit budget diagnostics and capacity cleanup. Validation now accepts
`max_candidates=null` and positive values above the former 200,000 ceiling.

The completed changes remove default candidate/SQL deadlines, date ceilings,
via/avoid counts and numeric connection ceilings, and expose reachable result
pages. Domain correctness rules and per-response sizes remain intentional.
Private snapshot/replay artifact bounds and ingestion/transport resource controls
remain separate from public SQL routing.

Backend resource isolation, cancellation and host controls are deferred in
[issue #27](https://github.com/lichen-liu/flightrepo/issues/27). This work does not
claim that unrestricted request scope establishes production resource safety.

Exact top-K does not require returning every path: admissible pruning may stop
once no unseen route can improve the answer. A fixed candidate counter offers no
such proof. Even a finite no-cycle graph can have exponentially many paths (two
choices at each of N layers produce 2^N paths), so removing cutoffs does not by
itself promise a bounded foreground response time. Efficient search and genuine
cancellation need to accompany that policy.
