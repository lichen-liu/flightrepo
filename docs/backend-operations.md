# Backend operations

The service keeps its existing SQLite design: private ingestion, explicit
publication and a read-only serving process. Flight facts use typed columns,
integer UTC timestamps and shared admission rules. References remain dimensions;
raw archives and temporary staging do not become serving tables.

The [limits audit](backend-limits-audit.md) distinguishes benchmark fixture size
from actual candidate, date, response and execution restrictions. The 100,000-row
storage fixture does not configure the backend or cap its SQL graph.

## Search scope and response pages

Departure ranges, onward horizons, via/avoid counts and connection allowances
have no arbitrary upper ceiling. Minimum allowances are nonnegative; a supplied
maximum must be at least the minimum. Airports must be valid codes and journeys
cannot repeat airports or reuse an endpoint as an intermediate stop.

Omit `horizon_end` on UTC searches to include available destination arrivals.
The server resolves it to the latest arrival in the selected time basis, at least
the departure-window end. Local searches do the same when both `arrival_by_local`
and `onward_days` are omitted. A positive or zero `onward_days` explicitly searches
that many elapsed days after the resolved departure-window end. Do not supply it
with `arrival_by_local`. Calendar availability accepts the same `onward_days`
filter. The browser defaults to all available data and no maximum layover.

POST search uses `offset` and `limit` (at most 100 per response), returning
`next_offset` or null. Continuation uses `option_offset` and `option_limit` (at
most 500 per response), returning `next_option_offset`. Keep the same query on
later pages and send back `data_revision`. A changed database produces HTTP 409
`data_changed`; restart the search rather than appending a page from different
facts. Explicitly budget-limited partial calculations do not promise a next page.
Continuation selection is independent of pagination. A selected option outside
the page is included in addition to that page, so it cannot displace an entry.

GET `/v1/airports`, `/v1/flights`, `/v1/routes` and legacy `/v1/itineraries` retain
list-shaped bodies. Follow their `Link: <...>; rel="next"` headers, which preserve
filters and carry the next `offset` and `data_revision`. `X-Next-Offset` and
`X-Data-Revision` expose the same values. Airports, flights and route summaries
return at most 1,000 per page; legacy itineraries return at most 100. These are
response sizes, not total-result ceilings. Narrow airport lookup searches the
full catalog; via autocomplete also queries the server rather than relying on
the initial bootstrap subset.

Offset paging reruns the query. Deep ranked pages retain the best `offset +
limit + 1` paths to establish ordering and whether another page exists. They do
not cache an entire search or promise constant work per page. Resource isolation,
cancellation and host controls are tracked separately in
[issue #27](https://github.com/lichen-liu/flightrepo/issues/27).

## Read budgets

Each server process admits two concurrent `/v1/` requests by default, plus one
separate catalog request. Dataset statistics cannot occupy all interactive slots.
Excess work receives HTTP 503, `Retry-After: 1` and code `server_busy` immediately.
There is no application queue. Slots are released after success, exceptions or
cancellation. Health checks and static files are outside this API budget; the
hosted Uvicorn connection limit still applies to the whole process.

`FLIGHT_MAX_CONCURRENT_READS` changes the interactive limit (1–32).
`FLIGHT_READ_QUERY_TIMEOUT_SECONDS` optionally enables a serving read deadline in
seconds. It is unset by default, so accepted queries have no SQL time cutoff. Any
positive duration is accepted; remove the environment variable to disable it. Set
these in the serving process environment and restart it. Configuration is per
process, not shared among replicas.

SQLite checks the deadline every 1,000 virtual-machine instructions. An interrupted
read returns HTTP 503 with `query_timeout`, rather than an empty or partial route
list. The deadline applies to SQL work within a read connection; it is not a hard
wall-clock limit on Python routing or network delivery. Search and continuation
have no candidate-count cutoff by default. Callers may explicitly set
`limits.max_candidates`; reaching that budget reports incomplete ranking. Admin
and ingestion do not inherit the serving deadline.

Start with the defaults and measure representative data and date windows before
raising concurrency. More simultaneous graph searches increase memory and CPU
pressure. Match the process budget to the host; adding replicas does not increase
the host's total resources.

`/health` checks process reachability. `/health/ready` verifies readable storage,
the serving role, schema contract and read view. An initialized empty serving file
is ready. Readiness does not scan or count the archive. Exact counts and the
default time basis remain in `/v1/catalog`. Bootstrap defaults are cached per
reader process and invalidated on DB/WAL changes or file replacement. Returned
models are independent, so callers cannot mutate the cached copy.

Shared UTC links submit the route query immediately, alongside calendar
availability. Bootstrap waits for those two interactive reads; archive statistics
start after the search returns. Calendar counts still control manual date
selection, but do not gate an already specified shared query. Background
statistics report their own status in the availability details rather than
keeping the page-wide activity bar open after route results arrive.

Public search and continuation responses do not compute replay graph fingerprints,
which their response contracts exclude. Private calculations and explicit traces
still retain stable replay identities.

Installation/startup and real local/public verification use schema/source probes
and bootstrap route evidence instead of rebuilding archive statistics. Full
catalog statistics remain exact and invalidate after publication. Bulk time-basis
aggregation reads facts sequentially; airport dimensions and the relaxed routing
graph seek between distinct pairs using the existing airport-pair index. No new
schema/index upgrade is required for these query changes. See the
[archive query measurements](archive-query-performance.md) for plans, reproducible
fixture benchmarks and limits of the live-archive measurements.

## Index and publication maintenance

The table contract remains version 1. A covering airport-pair index supports
relaxed routing bounds without scanning flight metadata. Three destination/arrival indexes
complement the existing departure indexes, with one partial index per time basis.
Cancelled and incomplete records do not occupy these indexes. See the
[canonical definitions](../src/core/query_indexes.sql) and
[SQLite's partial-index rules](https://www.sqlite.org/partialindex.html).

Before a release using these indexes, stop the relevant writer and run explicit
initialization with the new installed code against each durable file:

```sh
flyji --db /absolute/path/serving.db maintenance init
flyji-ingest --db /absolute/path/ingestion.db init
```

Use the correct role-specific command. Index creation reads the existing rows and
needs free disk space; schedule it as writer maintenance. It does not rewrite flight
facts or change admission. The read-only server never creates indexes on startup,
and older files remain readable. Check the resulting plans with
`EXPLAIN QUERY PLAN` and verify a representative search before releasing traffic.

Temporary daily staging retains the same constraints and identity indexes while
omitting serving query indexes. The durable ingestion commit still updates them.
Publication remains atomic per day, preserves serving edits/tombstones and keeps
receipts for reconciliation. Unchanged airport fields, source notices, reference
snapshots, carrier dimensions and publication memberships avoid redundant writes.
Changed or missing carrier dimensions are still reconciled even if their source
receipt is unchanged.

## Repeatable checks

Run the isolated demo, then exercise concurrent searches and readiness probes:

```sh
python scripts/run_demo.py
python scripts/qa_api_load.py --requests 96 --workers 8
```

The load report distinguishes completed searches from deliberate overload
rejections and reports readiness p95 latency. Other HTTP errors, incomplete
ranking or inconsistent successful responses fail the probe. For a representative
local serving instance, use `--url`, `--query-json` and optionally `--utc` with a
request for that endpoint. The probe refuses non-loopback hosts.

Profile a representative database search or continuation with
`scripts/profile_route_search.py`. It reports elapsed time, process peak memory,
SQL statement count and routing work, and can compare ordered results with a
saved baseline. See [routing performance](routing-performance.md) for the query
strategy, correctness arguments, measurements and commands.

Compare write cost and inbound query plans on disposable synthetic data:

```sh
python scripts/benchmark_storage.py --rows 100000
```

It reports insertion time with/without serving indexes, index bytes and paired
inbound-query timings. It does not copy or modify real databases. These probes
establish local behavior, not a production throughput guarantee. Run the HTTP
probe on the intended host and representative archive before changing budgets.
