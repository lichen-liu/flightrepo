# ADSB.lol ingestion and storage findings

Measured October 14, 2026. The recommended design is a compact aircraft-day
catalogue with byte offsets into the provider's archives, plus a bounded cache
that retrieves individual traces using HTTP Range.

## Why the archives are large

The [2025 archive](https://github.com/adsblol/globe_history_2025) contains repeated
position observations for many kinds of aircraft, plus heatmap replay data.
These are not one-row-per-flight schedule records. Individual trace JSON files
are gzip compressed inside a split, otherwise uncompressed tar archive.

The local inventory selects one preferred replica for every day, including
December 31 from the next year's repository:

| Preferred archive volume | GiB |
| --- | ---: |
| Average daily | 2.837 |
| Median daily | 2.977 |
| Smallest day, January 1 | 1.567 |
| Largest day, July 18 | 3.584 |
| Entire 2025, 365 days | 1,035.564 |

All published replicas in the 2025 repository total approximately 2,116 GiB.
Choosing the preferred replica avoids storing those duplicate observations.
Even preferred raw archives exhaust a 20 GiB budget in approximately seven days.

## Measured filtering result

We downloaded and processed the complete preferred December 30 archive, then
deleted its raw parts after the catalogue transaction committed.

| December 30, 2025 | Measurement |
| --- | ---: |
| Complete raw archive | 2.977 GiB |
| Aircraft traces examined | 61,215 |
| Commercial fixed-wing candidates retained | 13,241 |
| Position observations in retained candidates | 36,796,571 |
| Compressed traces for those candidates | 0.971 GiB |
| Summary JSON, before trace-location fields | 8.96 MiB |
| SQLite catalogue, before trace-location fields | 11.21 MiB |

At this day's rate, one year is approximately 4.83 million aircraft-day summaries
and 4 GiB before trace-location metadata; budget roughly 5–6 GiB for the indexed
catalogue. This is a one-day extrapolation, not a measured annual size. Retaining
all filtered traces would still need approximately 354 GiB, so filtering alone
does not solve the storage problem.

At roughly 0.9–1.1 KiB per indexed aircraft-day, one million summaries use about
0.9–1.1 GiB, and five million about 4.5–5.5 GiB. These estimates include the
SQLite representation and indexes but exclude the trace cache. The 6 GiB
catalogue ceiling is independent of the 8 GiB cache ceiling; room for one raw
day and transaction files remains within the combined 20 GiB cap.

An aircraft-day can contain several commercial flights. These counts must not
be presented as counts of individual flight legs. A true flight-leg database
needs a separate segmentation and airport-matching process with uncertainty
and coverage information. Observations have no future schedules.

The explicit filter in `fixtures/adsblol-commercial-filter.json` requires a
listed fixed-wing type and a known airline callsign prefix, or an explicitly
listed owner/operator. It rejects military flags, non-ICAO addresses and
incompatible emitter categories. Unknown types and missing commercial evidence
are excluded. The starter airline list is not exhaustive: airline charters,
business aviation, smaller operators and some regional types will be missed.
Classification is labelled `commercial_fixed_wing_candidate`; neither an A320
type nor a registry owner proves a scheduled commercial operation.

## On-demand retrieval

The [public API](https://www.adsb.lol/docs/open-data/api/) has live lookups by
aircraft type, ICAO address, registration, callsign and location. Its current
[OpenAPI schema](https://api.adsb.lol/api/openapi.json) has no historical date
search. The API source documents dynamic [rate limits](https://github.com/adsblol/api#rate-limits).
The routes endpoint provides plausible routes, not verified historical schedules.

The map retrieves individual recent traces through the path defined in
[tar1090's source](https://github.com/wiedehopf/tar1090/blob/master/html/script.js).
A request for aircraft `a5ea5c` on October 13, 2026 succeeded; December 30, 2025
returned 403. This does not establish a universal retention window, and that
undocumented path is not used as the 2025 backend.

GitHub release assets honored HTTP Range in the pilot. After indexing the tar
member's offset, compressed length and SHA-256, we retrieved the December 30
trace for `ad4584` without fetching the whole archive:

| Request | Size transferred | Elapsed |
| --- | ---: | ---: |
| First request, 6,911 positions | 176,048 bytes | 0.424 seconds |
| Second request, local cache | 0 network bytes | 0.011 seconds |

These are local measurements, not an upstream latency guarantee. Split-boundary
requests are supported. Unexpected HTTP 200, incorrect range, truncated response
or hash mismatch fails the request instead of falling back to a multi-GiB fetch.

The archive index still requires a one-time download and scan of approximately
1.01 TiB. A day-level inventory alone cannot tell us which aircraft were present
or where their trace bytes live. Processing one day at a time keeps disk use small,
but does not eliminate this initial network cost. Approximate transfer-only times
are 25 hours at 100 Mbps, 5 hours at 500 Mbps or 2.5 hours at 1 Gbps; decoding and
upstream throttling add time. No full-year scan is started automatically.

## Storage and metadata

The catalogue writer caps SQLite at 6 GiB. The trace cache caps compressed traces
at 8 GiB and evicts the least recently used entries; it also checks a combined
20 GiB ceiling over the configured ADSB.lol data root. Raw archive downloads use
the remaining allowance and preserve an extra GiB for a catalogue transaction.
Writers and cache workers coordinate through a process lock. Raw files should be
discarded after every successful day; keeping them eventually stops the importer
at the budget. Interrupted downloads resume from `.partial` files.

Available fields include ICAO address, registration, type code, description,
owner/operator text, manufacture year, emitter category, callsigns, first/last
observation, position count, squawks, emergency codes, data sources, maximum
barometric altitude and ground speed. Missing fields remain unknown. Full traces
can contain further measurements, such as vertical rate, selected altitude,
airspeed, roll and weather, where present; see the [readsb format](https://github.com/wiedehopf/readsb/blob/dev/README-json.md).

ADSB.lol publishes these observations under [ODbL 1.0](https://www.adsb.lol/docs/open-data/historical/).
API trace responses include attribution and licence identification. Publication
of a derived database needs to preserve the applicable licence obligations.
Data-link submission endpoints are a separate project and are not download APIs.

## Commands and serving

Run from an installed checkout (`pip install -e .`), or set `PYTHONPATH=src`:

```sh
python scripts/catalog_adsblol.py catalogs/adsblol-2025.json \
  --filter fixtures/adsblol-commercial-filter.json \
  --data-root var/adsblol --date 2025-12-30 --download --discard-raw
```

To scan all 365 days, replace `--date 2025-12-30` with `--all-dates`.
This performs the roughly 1 TiB initial transfer. Rerunning skips successfully
completed days with the same filter/representation fingerprint. Changing the
filter rebuilds days. A malformed or oversized trace aborts that day's import,
leaving any existing catalogue for the day intact.

To serve this observation catalogue:

```sh
FLIGHT_DATABASE_PATH=var/adsblol/observations.db \
FLIGHT_ADSBLOL_DATA_ROOT=var/adsblol uvicorn server.api:app
```

`GET /v1/observations?day=2025-12-30&type_code=B738&limit=100`
returns candidate summaries. `GET /v1/observations/2025-12-30/ad4584/trace`
fetches/caches an indexed trace. The research pilot attached trace offsets to 43
sample summaries; a normal complete catalogue import indexes every retained trace.

Observation summaries do not populate the scheduled `flights` table. The route
calendar therefore remains empty until routable records are loaded. For real
scheduled records, optional `aircraft` metadata survives import, storage, edits,
route snapshots, exports and serving; route detail cards show type and registration.
Existing SQLite flight files are upgraded by the internal writer's `initialize`
operation, never by the read-only server. CSV imports may supply the metadata as
an optional JSON `aircraft` column; JSON imports use the nested object directly.

The calendar calls `/v1/dates?origin=YYZ&clock=local` (or `clock=UTC`) and enables
only dates with non-cancelled departures for that origin. Gaps, empty databases,
timezone changes and date-loading failures cannot enable unrelated days. Available
departure data does not guarantee a feasible route to the chosen destination.

## Collected so far

The year inventory covers all 365 days. Actual observations currently cover
December 30 only: 13,241 aircraft-day summaries. The raw daily archive was deleted;
research samples and catalogue use less than 0.1 GiB. The complete year's aircraft
catalogue has not been collected, and no inferred flight routes have been created.
