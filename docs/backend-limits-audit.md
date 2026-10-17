# Backend limits audit

Audited on October 17, 2026 against PR #22 revision `3b7162e`. This is an inventory
and evaluation, not a change to the limits. It covers the public search path,
calendar and listing APIs, private snapshot/replay, ingestion and hosted serving.
The new deadline/admission controls describe the unmerged PR, not a deployment
performed by this audit.

## What the 100,000-flight benchmark means

`scripts/benchmark_storage.py --rows 100000` generates 100,000 synthetic records
in disposable databases. It compares index/write costs. It neither configures nor
limits the server. The script itself accepts 1–1,000,000 fixture rows; that ceiling
only prevents an accidental oversized local benchmark.

There is, separately, a real **100,000 candidate-check search budget**. Calling the
benchmark a fixture must not obscure that different restriction.

## Restrictions that can omit or prevent valid results

| Restriction | Actual behavior | Assessment |
| --- | --- | --- |
| Candidate checks: default 100,000, hard maximum 200,000 | `SearchLimits.max_candidates` interrupts search and continuation. Visits count repeated examination along different paths, not distinct SQL rows. `null` is rejected. Returned journeys remain feasible, but better or any matching journeys can remain unexamined; diagnostics report `candidate_limit`. | A real completeness restriction. The fixed count and hard maximum are not justified by an invariant of flight routing. It is not an accuracy-neutral optimization. |
| Serving read deadline: default 15 seconds, configurable only up to 120 | PR #22 adds a monotonic deadline per read connection, checked every 1,000 SQLite VM instructions. An interrupt returns 503 `query_timeout`, not an empty result. Configuration currently has no disable value. | A real availability restriction added by this PR. It is a resource policy, not a SQL optimization. The timer also elapses during Python work while the connection is open; it is not 15 seconds of accumulated SQL execution. |
| Departure span: at most 31 elapsed days | `RouteSearchRequest` rejects longer windows. Local requests allow 31 calendar dates, but the UTC validator can reject that range across a fall-back DST change. | A product/validation ceiling, not a correctness requirement. Calendar dates and elapsed durations are inconsistent at DST boundaries. |
| Arrival horizon: at most 31 days after departure-window end | Longer explicit horizons receive validation errors. Continuation separately enforces at most 62 days from the first retained departure. | Another fixed product ceiling. Any change must keep full-search, continuation, calendar and local/UTC validation aligned. |
| Calendar/browser horizon: seven days after departure-window end | Local search defaults to this horizon; UTC browser requests explicitly construct it. Calendar availability hardcodes the same horizon and has no configurable horizon argument. | This can hide dates whose only valid journey arrives later. It is explicit in the current UI scope but not an unrestricted search of the archive. |
| Via airports: maximum six; avoided airports: maximum 50 | Requests exceeding these counts are rejected, even with unrestricted stops. The browser additionally caps the avoid field at 199 characters. | Historical input ceilings, not graph correctness requirements. Valid no-repeat via constraints are a separate matter. |
| Connection allowances | POST search defaults to 45 minutes minimum and 360 maximum; maximum can be `null`. Numeric minimum is capped at 1,440 and maximum at 44,640 minutes. The legacy GET endpoint is tighter: minimum 20–360, maximum 60–1,440, and no nullable maximum. | Caller-selected layovers define eligibility, but arbitrary numeric ceilings and differences between adapters need justification. Removing a work budget does not undo these filters. |
| Continuation gap semantics | The edited gap uses its requested maximum/not-before. Later gaps use the minimum without a maximum. | Continuation and full-search policies differ; an edited journey may exceed the maximum used in its original search. This is documented behavior, not a row-loading cutoff. |

Sources: [request validation](../src/core/routing/models.py),
[continuation inputs](../src/core/connections.py),
[candidate accounting](../src/core/routing/session.py),
[search traversal](../src/core/routing/search.py),
[continuation traversal](../src/core/planning.py),
[reader/deadline/calendar](../src/server/reader.py),
[local-time conversion](../src/server/web_models.py),
[HTTP adapters](../src/server/api.py), [browser request](../src/server/web/app.mjs).

## Response sizes are also real restrictions

These do not limit the eligible SQL graph, but they restrict what a caller can
retrieve. Most are bounded lists, **not implemented cursor pagination**.

| Surface | Default / maximum | Consequence |
| --- | --- | --- |
| Ranked itineraries | 20 / 100 | The engine can prove the exact best K without enumerating every worse route. `exhaustive` means that ranking completed within the request scope; it does not mean every possible route was returned. There is no cursor/offset for route 101. |
| Continuation options | 100 / 500 | Enumeration continues after the list fills; automatic selection is independent of the display cap unless the candidate budget interrupts it. The explicit/automatic selected option is retained. Total/truncated fields exist, but there is no ordinary next-page cursor. |
| Flights, direct route summaries, airport listing | 500 / 1,000 per call | List-shaped APIs have no cursor/offset or `has_more` response. Broad queries can silently omit entries beyond the list limit. Route-summary grouping still covers its query before limiting returned pairs. |
| Catalog/bootstrap airport suggestions | 1,000, fetched with a 1,001st sentinel | `airports_truncated` exists, but the frontend does not use it to fetch more suggestions. A legitimate airport can be absent from suggestions. Exact backend lookup/routing still works beyond this subset. |
| Via autocomplete | First eight matches from the loaded airport subset | A UI suggestion cap, not an engine filter, but the incomplete source catalog compounds discovery problems. |
| Airport coverage | One or two airports per request | Request shape only; repeated calls can inspect others. It is not a graph-size ceiling. |

Retain reasonable response page sizes, but make all relevant entries reachable
through genuine pagination or server-side airport lookup. A truncated array must
not be presented as the complete dataset. Raising 1,000 to a bigger constant is
not a complete remedy.

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
  `max(100000, max_records * 20, max_candidates)`; at defaults this is 200,000
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
departure-ordered scan. At the default 100,000 candidate budget, the engine
returned a worse flight with `exhaustive=false`. At 200,000 it found the correct
flight after 100,001 checks. This proves a real default-budget completeness effect,
not just a hypothetical concern. It is not a claim that this synthetic route
volume reflects a physical service.

A separate disposable two-flight example arrived January 10 after a January 1
departure. With no maximum layover, an explicit longer-horizon request found the
route; the fixed calendar arrival window offered no date. Scope alone can explain
missing results even when no compute budget is reached.

Eight focused regression cases passed for candidate cutoffs, unrestricted stops,
SQL deadlines and capacity cleanup. Validation rejects both
`max_candidates=null` and `max_candidates=200001` today.

For the stated preference of no arbitrary query cutoffs, the priorities are:

1. Remove mandatory candidate-count ceilings from authoritative search semantics;
   retain optional caller/operator budgets only with explicit incomplete/cancelled
   outcomes. Increasing the fixed number does not resolve the policy problem.
2. Reconsider the newly added mandatory serving deadline. Make execution policy
   configurable/disableable and cover the actual calculation lifecycle, not just
   SQLite callbacks. Completion, cancellation and failure must remain distinct.
3. Make departure/arrival scope caller-controlled and consistent across calendar,
   search and continuation. Remove arbitrary six-via/fifty-avoid ceilings and
   reconcile the differing connection validators.
4. Implement reachable response pages and airport lookup; keep page sizes separate
   from the amount of eligible data examined.
5. Retain configurable admission, transport and ingestion resource controls that
   fail explicitly instead of returning a fabricated complete empty result.

Exact top-K does not require returning every path: admissible pruning may stop
once no unseen route can improve the answer. A fixed candidate counter offers no
such proof. Even a finite no-cycle graph can have exponentially many paths (two
choices at each of N layers produce 2^N paths), so removing cutoffs does not by
itself promise a bounded foreground response time. Efficient search and genuine
cancellation need to accompany that policy.
