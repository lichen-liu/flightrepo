# Backend operations

The service keeps its existing SQLite design: private ingestion, explicit
publication and a read-only serving process. Flight facts use typed columns,
integer UTC timestamps and shared admission rules. References remain dimensions;
raw archives and temporary staging do not become serving tables.

The [limits audit](backend-limits-audit.md) distinguishes benchmark fixture size
from actual candidate, date, response and execution restrictions. The 100,000-row
storage fixture does not configure the backend or cap its SQL graph.

## Read budgets

Each server process admits two concurrent `/v1/` requests by default, plus one
separate catalog request. Dataset statistics cannot occupy all interactive slots.
Excess work receives HTTP 503, `Retry-After: 1` and code `server_busy` immediately.
There is no application queue. Slots are released after success, exceptions or
cancellation. Health checks and static files are outside this API budget; the
hosted Uvicorn connection limit still applies to the whole process.

`FLIGHT_MAX_CONCURRENT_READS` changes the interactive limit (1–32).
`FLIGHT_READ_QUERY_TIMEOUT_SECONDS` changes the serving read deadline (default
15 seconds, maximum 120). Set these in the serving process environment and restart
it. Configuration is per process, not shared among replicas.

SQLite checks the deadline every 1,000 virtual-machine instructions. An interrupted
read returns HTTP 503 with `query_timeout`, rather than an empty or partial route
list. The deadline applies to SQL work within a read connection; it is not a hard
wall-clock limit on Python routing or network delivery. Engine candidate/state
budgets still apply. Admin and ingestion do not inherit the serving deadline.

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

Public search and continuation responses do not compute replay graph fingerprints,
which their response contracts exclude. Private calculations and explicit traces
still retain stable replay identities.

## Index and publication maintenance

The table contract remains version 1. Three additive destination/arrival indexes
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

Compare write cost and inbound query plans on disposable synthetic data:

```sh
python scripts/benchmark_storage.py --rows 100000
```

It reports insertion time with/without serving indexes, index bytes and paired
inbound-query timings. It does not copy or modify real databases. These probes
establish local behavior, not a production throughput guarantee. Run the HTTP
probe on the intended host and representative archive before changing budgets.
