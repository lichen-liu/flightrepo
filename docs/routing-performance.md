# Routing performance

Routing stays in the Python server. SQLite supplies compact calculation facts;
full flight details are fetched after ranking. Search coverage, connection rules
and response pagination are unchanged.

## What was expensive

The YYZ → BKK search departing August 18, 2023 used the available destination
archive through March 2024. The previous projection loaded 5,855,988 edges,
constructed several identity maps, ran two reverse traversals and built a full
in-memory departure index before examining 1,585 routing candidates. The bulk
of those edges were unrelated to an improving journey from that departure day.

Removing the old loading cap exposed this cost. Reintroducing a cap would hide
better routes again; the fix is to avoid loading facts that the calculation does
not need.

## Query and algorithm changes

Normal search uses a request-local SQL departure index. It reads one airport's
connection window at a time, using the existing origin/time indexes and the
shared `searchable_legs` view. All reads, including final detail hydration, share
one SQLite transaction. There is no row or candidate cutoff by default.

A covering airport-pair index supplies a small, relaxed airport graph without
reading flight metadata. It deliberately includes cancelled and unreviewed facts
and ignores dates: these extra arcs make lower bounds optimistic. Admission,
time intervals, transfers, avoided airports, ordered vias and airport cycles
remain exact routing decisions.

Best-first search separates a direct completion from taking another connection.
The latter requires at least two more flights. Deferring that expansion avoids
allocating distant partial paths before shorter complete routes establish an
incumbent. Once a page plus its next-page sentinel is known, remaining work is
pruned only when its lower bound cannot improve that ranking. Time and reachable
airport predicates are also passed into subsequent SQL windows.

Earliest-arrival and shortest-elapsed rankings first obtain feasible incumbents.
For earliest arrival, the latest arrival among enough feasible paths bounds the
page: a later arrival cannot enter it. For elapsed time, the departure-window end
plus the worst incumbent duration gives a conservative arrival bound. A small
amount of rounding preserves ties in the engine's floating-second score.
SQL then projects this proven time interval, and temporal reverse bounds guide
the final exact ranking. These are bounds derived from valid routes, not a fixed
number of onward days. If fewer than a page plus sentinel exist, the seed already
contains every feasible journey and can be reranked directly.

Continuation planning finds the best tail rather than enumerating every suffix.
Its frontier reads departures chronologically and retains nondominated labels.
An earlier arrival dominates a later one only when it uses no more legs or
visited airports, has the same via progress, and preserves identity tie-breaking.
This relies on the existing rule that later connections have no maximum wait;
the edited first connection still uses its requested maximum and not-before.

Only visible continuation options and the selected/first complete option need
tail solutions. Off-page options are still examined and counted, so total counts
and later pages stay exact.

Tail calculation starts at the first feasible onward departure. It initially
tries a short arrival interval and expands it until a continuation is found or
the full requested horizon has been examined. A found earliest tail is globally
optimal because anything arriving after that trial cannot improve it. One
compact temporal index is reused across options and replaced when its scope
changes. Long waits before the first usable departure do not fill that index
with unrelated intervening flights.

## Local measurements

Measured October 18, 2026, on the development Mac with Python 3.14.7 and SQLite
3.53.4. The database contains approximately 8.2 million flight facts. Updated
queries used an isolated SQLite backup with the additive airport-pair index;
serving and ingestion processes were not changed. These are single-process local
measurements with warm filesystem caches, not production latency guarantees.

| Operation | Before | After |
| --- | --- | --- |
| YYZ → BKK, August 18, 45–360 minute connections, fewest legs | 106.2 s; 7.76 GiB process peak RSS | About 1.0 s; 97 MiB |
| Same search, earliest arrival / shortest elapsed | — | About 1.2 s; 102–106 MiB in the ranking matrix |
| Same search, unrestricted maximum layover, all rankings | — | About 1.6–2.1 s; up to 126 MiB in the ranking matrix |
| Edit the first connection of the returned YYZ → TPE route | Previous suffix enumeration did not complete during the diagnostic probes | 2.1 s; 158 MiB; all 14 options and their best tails |

The shared search returns the identical ordered itinerary payloads and next-page
offset as the original implementation. The continuation comparison also preserves
the option payloads, including a valid nine-day wait. Automated checks compare
against an independent exhaustive oracle and cover pagination, late better
flights, transfers, via order, identity ties and long waits.

The new index occupied about 126 MiB and took about five seconds to build on the
copy. It adds a write/index-maintenance cost to durable files; temporary ingestion
staging still omits serving indexes. Apply it through explicit writer maintenance
as described in [backend operations](backend-operations.md#index-and-publication-maintenance).
Existing files without it remain readable, with slower topology loading.

A separate CPU profile of the updated shared search spent about 0.58 s in SQLite
`execute` calls and about 0.22 s obtaining the relaxed graph, out of about 1.28 s
under profiling. No evidence here justifies changing the Python runtime. The
large gain came from query scope, allocation and traversal order. Difficult,
wide searches and deep pagination can still require substantial work; neither
exact ranking nor this optimization promises constant resource use. Deployment
isolation and cancellation remain tracked in [issue #27](https://github.com/lichen-liu/flightrepo/issues/27).

## Reproduce

Save a UTC `RouteSearchRequest` as `query.json`, then use a serving database or an
isolated SQLite backup:

```sh
python scripts/profile_route_search.py --db /path/to/serving.db \
  --query-json query.json --output report.json --result-json result.json
```

Use `--operation rebuild` with a `RebuildRequest` to measure connection editing.
`--compare-json previous-result.json` checks ordered itineraries/options and
pagination against a saved result. Comparisons require the same underlying facts
and request. Saved result files contain internal flight details and belong in
local QA storage, not public reports.

`--cpu-profile search.prof` records a separate cProfile run. Profiling changes
elapsed time, so compare unprofiled runs for timing. Peak RSS is the whole fresh
process, including imports and SQLite; use a new process for each memory sample.
`rows_loaded` counts decoded compact rows, including repeated windows and
projection passes. It is a work counter, not a distinct-flight count or a limit.
SQL-filtered rows are not scanned again solely to populate skipped-row counters.
