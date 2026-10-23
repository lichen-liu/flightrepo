# Archive query performance

Installation appeared stuck after printing an exact count of 18,142,805 stored
flights. The installed `serve.py --check` was doing SQLite work, rather than pip
installation. Investigation and changes were made in an isolated checkout based
on `6897542`; the installed runtime, jobs, ingestion worker and serving facts were
not changed. The shutdown-race fix in PR #38 is independent.

## Operator behavior

The preservation/seed phase still reports an exact count of live stored records,
including cancelled, incomplete and search-blocked facts. It counts each source
once through the identity index and sums those counts. Startup validates the
same serving role, schema and source restrictions through existence probes; it
does not need record totals or time-basis statistics.

Real offline preflight checks packaged assets and readiness, selects an admitted
route using `/v1/bootstrap?include_airports=false`, then calls the real search
and rebuild endpoints. It keeps the original time basis, date window, horizon
and route calculation. Empty datasets and datasets without admitted complete
time evidence keep their explicit skip behavior. No record/graph caps were added.
Synthetic constrained-route verification is retained.

Each installer seed/check subprocess now has a 180-second wall-clock deadline.
The child prints its current phase and elapsed time, including during startup.
Timeouts retain the incomplete marker and previous receipt. Real local/public
checks use the bootstrap summary and real route search; their output reports
routing status instead of forcing a full archive count. Exact catalog statistics
remain available on demand. HTTP probe deadlines and user search scope are
unchanged.

No schema, new index, writer migration, persisted summary table or trigger is
required. The queries reuse `flight_legs_airport_pair` and the identity/time
indexes already present on the inspected archive. Databases without the pair
index retain the exact legacy dimension query.

## Database work and plans

`FlightReader.stats()` previously counted `flights`, grouped it by provider, and
requested three separate time-basis aggregates. Installation/startup repeated
this work, and real preflight then requested a full catalog. The compatibility
view expands source joins, tombstone checks and projected evidence. Although
unused formatted timestamps are pruned, stepping time indexes to obtain wide
facts in shuffled departure order still creates random table reads.

The old time-basis plan includes a `CO-ROUTINE searchable_legs`, `UNION ALL`
branches and `SEARCH f USING INDEX flight_legs_*_departure`, followed by
`SCAN searchable_legs`. Plans show all union branches; SQLite can skip branches
at execution when their constant basis does not match. This plan alone does not
prove that every branch reads facts for every request.

The new bulk summary computes all three exact counts and departure extrema in
one storage-order pass. Admission and tombstone checks remain in its `WHERE`;
basis completeness remains in each aggregate's `FILTER`. Its plan is:

```text
SCAN f
CORRELATED SCALAR SUBQUERY
SEARCH t USING PRIMARY KEY (source_id=? AND source_record_key=?)
CORRELATED SCALAR SUBQUERY
SCAN reason VIRTUAL TABLE INDEX 1:
```

The full catalog still performs its separate exact quality/total aggregate.
Thus it performs two sequential fact passes rather than the separate
departure-order basis reads. Source validation uses `EXISTS` per source against
the identity index and preserves tombstones. `stats()` reuses provider counts
for the total inside one read snapshot.

Bootstrap's old active-airport `UNION` scanned the archive twice. Routing also
ran `SELECT DISTINCT origin_iata,destination_iata FROM flight_legs` for every
search and rebuild. Its plan scanned every entry of the covering pair index.
The new recursive query seeks the next distinct origin, then the next distinct
destination within that origin:

```text
SEARCH flight_legs USING COVERING INDEX flight_legs_airport_pair (origin_iata>?)
SEARCH flight_legs USING COVERING INDEX flight_legs_airport_pair (origin_iata=?)
SEARCH flight_legs USING COVERING INDEX flight_legs_airport_pair
    (origin_iata=? AND destination_iata>?)
```

Separate seeks matter: a tested row-value comparison `(origin,destination) >
(?,?)` only constrained the first column in this SQLite plan and still stepped
duplicates. The chosen query enumerates every pair without a recursion/record
cap. Routing retains the same relaxed graph, including blocked, cancelled,
out-of-window and tombstoned arcs; actual candidates still use shared admission.

Airport priority is computed from the distinct pairs and, when tombstones exist,
an exact live-fact probe per pair. Fully tombstoned pairs may still require
walking their records to prove absence. With no tombstones, no per-pair fact
probe is needed. Bootstrap now matches the catalog's live-fact priority instead
of prioritizing tombstoned-only airports. Reference airports remain available
under the existing 1,000-airport bootstrap response limit.

Catalog/bootstrap caches retain resolved database/WAL/journal file revisions,
locks and independent returned models. Full/summary bootstrap caches are separate.
A publication during the read prevents caching that obsolete snapshot; the next
request reads again. No stale TTL or ingestion-only invalidation was introduced.

## Fixture measurements

Measured on 2026-10-23 with Python 3.14.7 / SQLite 3.53.4, before code pinned to
`6897542`. Final runs were sequential after validation. Both versions read the
same generated database and indexes. The 250,000-row fixture was 65,560,576 bytes;
the 1,000,000-row fixture was 262,795,264 bytes. They contain shuffled observed
departure times, 676 distinct pairs, 1,352 reference airports, cancellations and
blocked reasons. These sizes configure the benchmark only.

Each range below contains the first and second call on fresh readers. This
measures a cold application cache and a subsequent fresh reader against a warmer
OS cache. The OS cache was not purged; these are not disk-cold guarantees.
Validation rows time reader work (`stats()` before, `validate_dataset()` after);
the preflight row measures the complete `check_installation()` function.

| Operation | 250k before | 250k after | 1m before | 1m after |
|---|---:|---:|---:|---:|
| Exact preservation validation | 0.408–0.410 s | 0.0247–0.0257 s | 1.84–1.92 s | 0.0909–0.0915 s |
| Startup schema/source validation | 0.389–0.397 s | 0.00093–0.00122 s | 1.88–1.89 s | 0.00090–0.00115 s |
| `stats()` | 0.410–0.417 s | 0.116–0.118 s | 1.70–1.76 s | 0.475–0.484 s |
| Full catalog | 0.824–0.832 s | 0.226–0.234 s | 3.47–3.67 s | 0.914–0.917 s |
| Full bootstrap | 0.0495–0.0505 s | 0.00766–0.00821 s | 0.191–0.195 s | 0.00762–0.00803 s |
| Offline preflight, including search/rebuild | 1.32–1.47 s | 0.0558–0.0568 s | 5.94–6.01 s | 0.0588–0.0638 s |

Unchanged same-reader catalog/bootstrap calls remain about 1.7–1.8 ms for both
versions. `stats()` deliberately has no cache. The baseline preflight uses its
original validation/catalog logic and original distinct-pair scan. The production
ASGI application and real routing endpoints are exercised in-process from source;
the wheel is verified separately. No server or tunnel is started by the fixture
benchmark.

Reproduce from a development environment with the baseline commit available:

```sh
PYTHONPATH=src python scripts/benchmark_archive_queries.py --rows 250000
PYTHONPATH=src python scripts/benchmark_archive_queries.py --rows 1000000
```

The script creates and removes its own temporary database. It has no option to
open the hosted archive. The [raw report](research/archive-query-benchmark-2026-10-23.json)
contains individual timings, progress and query plans.

## Bounded measurements on the actual archive

Process inspection first found an active installed `serve.py --check`, so initial
work used fixtures and read-only schema/plans. After that process exited, direct
reads used `mode=ro`, `query_only` and a two-second SQLite work deadline. The file
was approximately 14 GiB with a 1.4 GiB WAL; 18,142,805 records was the earlier
operator-reported preservation count, not a newly completed count here.

| Operation | First read | Second read |
|---|---:|---:|
| Before `stats()` | interrupted at 2.010 s | not repeated |
| Before full catalog | interrupted at 2.001 s | not repeated |
| After exact preservation count | interrupted at 2.001 s | not repeated |
| After schema/source validation | 1.55 ms | 1.35 ms |
| After bootstrap summary | 1.69 ms | 1.49 ms |
| After full catalog | interrupted at 2.001 s | not repeated |
| Before distinct-pair scan | interrupted at 2.005 s | not repeated |
| After exact pair enumeration, 307,660 pairs | 1.673 s | 1.195 s |

The summary selected admitted observed-time evidence. These are lower bounds
for interrupted operations, not completed full-archive timings. No live
end-to-end routing/preflight benchmark was run, no OS caches were flushed, and
no live app was stopped, restarted, installed or deployed.

Full counts/quality statistics remain O(records); frequent publication can still
invalidate their cache before the next request. Dense shuffled archives benefit
from sequential aggregation; sparse time-basis distributions can have different
tradeoffs. Distinct-pair seeking scales with graph cardinality: the fixture's
676-pair graph is substantially smaller than the actual 307,660-pair graph.
All-blocked evidence and fully tombstoned sources/pairs may still require long
exact absence checks. The subprocess deadlines bound preflight failure without
turning it into a partial success.

## Regression and release checks

- 1,380 Python tests passed, including full indexed routing, admission,
  serving authority, deployment and monitoring checks.
- 13 frontend tests passed; the built wheel passed isolated installed-package
  and ingestion diagnostics probes on generated databases.
- Ruff lint/format and diff checks passed.
- New tests compare counts, extrema and quality totals with the public view,
  including null/unknown reasons, manual review, overlapping bases, cancellation,
  tombstones and microsecond/pre-epoch evidence.
- SQLite VM-work checks reject duplicate-fact scans for dimension/probe paths;
  tests also cover missing-index fallback, source rejection, timeouts, WAL
  invalidation and publication during a cached read.

The PR is intended for manual review and a later operator-controlled rollout;
it does not include or merge the shutdown-race change.
