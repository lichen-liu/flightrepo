# ADSB.lol: flight-leg ingestion and aircraft type

Updated October 14, 2026. The serving requirement is a flight's aircraft type
code. Aircraft traces and aircraft-day summaries are not part of serving.
The backend now supports [normalized itinerary downloads with a live disk quota](itinerary-cache.md).
That storage mechanism requires flight-leg records, including the type code;
ADSB.lol observations still need a validated conversion before they can feed it.

The preferred 2025 storage plan is now a **durable local database of compact
flight legs**, built one day at a time. Raw archives are temporary processing
inputs and are removed after validated output is committed. A remote itinerary
source is not required when the extracted flight facts fit the local budget.

## What the pilot proved

The earlier byte-range experiment proved retrieval of one known aircraft-day
trace from a GitHub archive. It did not prove historical flight discovery,
correct leg reconstruction, or itinerary search from a catalogue.

The day-level inventory in `catalogs/adsblol-2025.json` lists archive files and
sizes. The initial aircraft-day summaries list callsigns and observation ranges.
Neither contains the origin, destination and per-flight times needed for search.
The 13,241 retained aircraft-days must not be described as 13,241 flights.

A subsequent conservative research probe used the real December 30 sample for
aircraft `ad4584`, whose source type code is `B737`. It retained four complete
ground-to-ground candidates after checking nearby airports:

| Observed callsign | Inferred airports | Type |
| --- | --- | --- |
| SWA2595 | SNA → OAK | B737 |
| SWA3281 | LAS → SAN | B737 |
| SWA1817 | PHX → SAN | B737 |
| SWA1821 | SAN → SJC | B737 |

For SWA1817, the observed departure transition is between
20:44:26.290 and 20:44:27.100 UTC, and the arrival transition between
21:30:41.930 and 21:30:44.310 UTC on December 30. These are inferred movement
time bounds, not scheduled times or gate times. Callsigns are operating radio
identifiers; converting them to marketing flight numbers needs a separate mapping.

The probe requires ground observations at both ends, short transition brackets,
continuous coverage, a listed fixed-wing type, an airline callsign and an
unambiguous airport within 5 km. It rejects partial flights, stale positions,
coverage gaps and ambiguous endpoints. The airport match uses the current
[OurAirports public dataset](https://ourairports.com/data/); historical airport
changes and these inferred identities have not been independently reconciled.
The result proves candidate extraction on one aircraft, not production coverage.

## What can be fetched on demand

The current [public API schema](https://api.adsb.lol/api/openapi.json) provides
live aircraft lookups by ICAO address, callsign, registration, type and location.
It does not provide a dated historical flight-leg list or itinerary search.

The callsign route service loads standing route data and tests whether a
position lies plausibly along that route. Its [implementation](https://github.com/adsblol/api/blob/main/src/adsb_api/utils/api_routes.py)
and [data loader](https://github.com/adsblol/api/blob/main/src/adsb_api/utils/provider.py)
have no historical date argument. A current callsign-route result cannot verify
where that callsign flew on a particular day in 2025.

Direct endpoint probes on October 14 confirmed the distinction:

- `GET /v2/callsign/ACA107` returned a live aircraft with `t: B789`,
  registration `C-FVLU` and ICAO address `c038a7`.
- [Standing route JSON for ACA107](https://vrs-standing-data.adsb.lol/routes/AC/ACA107.json)
  returned `YYZ-YVR`, flight number `107` and airline code `ACA`, plus airport
  metadata. It contained no date, departure/arrival times or aircraft assignment.
- `GET /api/0/route/ACA107/{lat}/{lng}` returned HTTP 500; the documented
  `POST /api/0/routeset` returned HTTP 201 with an empty body in this probe.
  Neither probe established a reliable live route response. The route source
  implementation independently shows callsign/position inputs, with no date filter.
- [Standing routes CSV](https://vrs-standing-data.adsb.lol/routes.csv.gz) was
  4,500,164 compressed bytes (18,957,159 uncompressed), containing 620,700
  data rows. Its five columns are `Callsign, Code, Number, AirlineCode,
  AirportCodes`. It is a compact route-hint catalogue, not dated flight instances.

The API rejected a generic HTTP client's User-Agent with HTTP 403, asking for
contact information. The live callsign request succeeded using this repository's
public GitHub URL in a descriptive User-Agent. That does not establish permission
or availability guarantees for production load.

Combining the live `B789` observation and standing `YYZ-YVR` route produces a
current route/type hint. It does not prove that this route applied to that aircraft
on a requested historical date, and cannot establish a connection itinerary.
Origin/date search requires actual dated leg records with departure and arrival
times; airport metadata and callsign routes alone cannot supply those records.

Aircraft type is the field `t` in readsb data; `type` describes the message
source, such as ADS-B or MLAT. The observed callsign is in `flight`. These
fields are documented in the [readsb format](https://github.com/wiedehopf/readsb/blob/dev/README-json.md).
Live lookup can supply type when a flight is observed now. It cannot be relied
on to recover a finished 2025 flight and its historical aircraft assignment.

For this requirement, fetching type on demand offers little benefit: while
processing a flight's observations, its aircraft type is already available.
Store the few-character code on that flight leg and return it with search results.

## How the searchable dataset must be built

1. Read one preferred daily archive at a time, skipping heatmap members.
2. Apply the explicit commercial fixed-wing selection rules. The starter list
   is conservative and incomplete; unknown types or missing commercial evidence
   must not be silently described as comprehensive airline coverage.
3. Reconstruct individual movements from ground/airborne transitions, track the
   operating callsign within each movement, and preserve state across UTC days.
4. Match terminal observations to airport metadata. Keep the timing bounds,
   inference method and confidence; reject incomplete or ambiguous legs.
5. Store one compact row per accepted leg: stable identity, date, callsign,
   airports, departure/arrival evidence and `aircraft_type_code`. The type comes
   from the same aircraft's observations for this movement, not an unrelated
   aircraft with a reused callsign.
6. Discard the position data after processing. Index origin and departure time
   for search; also index route/date as required by actual queries.

A user search reads these local rows for the origin and departure window. The
routing engine follows onward rows and checks airport continuity, connection
times and the arrival horizon. Each resulting itinerary contains its legs and
their stored aircraft type codes. No external lookup is needed for a result.

Calling this compact flight database a catalogue is reasonable, but its contents
must be the searchable flight facts. A catalogue containing only aircraft IDs,
dates and external file URLs cannot power the search.

The current engine expects scheduled departure and arrival timestamps. ADSB.lol
does not supply them. Before accepting these inferred legs into the serving
engine, we need an explicit historical/observed-time contract and conservative
connection semantics—for example, arrival's upper bound plus minimum connection
time must precede departure's lower bound. Filling the scheduled fields with
inferred takeoff and landing times would misrepresent the evidence.

The normalized itinerary-to-search path is implemented and tested. The full
ADSB-to-normalized-itinerary pipeline is not implemented or validated in this PR.
Cross-midnight continuity, deduplication, callsign assignment, endpoint validation,
coverage measurement and observed-time search remain required work. These
observations supply historical movements, not future schedules or availability.

## Storage and download cost

### Daily processing within the 20 GiB budget

Download only one preferred day's archive parts into the working directory.
Read their tar stream without extracting individual members to disk; inflate
and process one aircraft member at a time. Keep aircraft type, callsign, matched
airports and observed departure/arrival bounds, together with provenance and
classification evidence. Discard position samples once their evidence has been
reduced. Store partial cross-midnight flight state separately until its arrival
can be resolved from the next day; a midnight boundary must not drop a flight.

Commit validated, deduplicated flight legs and a day completion receipt
atomically, then remove that day's verified archive parts and advance to the
next day. Failures leave the day resumable rather than marked complete. Serving
must continue to distinguish observed movement times from scheduled/gate times.
This end-to-end daily leg processor is planned; the existing whole-day probe
produces aircraft-day summaries and does not yet implement this pipeline.

Reserve roughly **4 GiB for one raw day and 1 GiB for write/processing headroom**,
leaving a starting target of **15 GiB for durable flight facts**. These are
planning allowances, not measured peak WAL/transaction usage. The worker must
account for all retained facts, raw/partial downloads and temporary files within
the same 20 GiB total, and pause before exceeding it. Durable flight facts cannot
be LRU-retired if they are their only copy; the backend cache's LRU policy applies
only to recoverable payloads. Raw files are disposable after the daily commit.

Store each flight leg once. Origin/departure and route/departure indexes serve
search; itineraries are assembled from those legs at query time instead of
storing every possible connection combination. Coverage/calendar metadata is a
small derived index over committed legs. Extra gzip copies of the same local
database are unnecessary when the indexed facts fit the budget.

### Measured cost per flight leg

The reproducible command below measures 100,000 synthetic legs against the
current schema, including all five flight indexes (the identity index and four
query indexes). It deletes its temporary benchmark databases afterward:

```sh
PYTHONPATH=src python scripts/benchmark_flight_storage.py --records 100000 \
  --output var/adsblol/research/compact-flight-storage.json
```

| Profile | Measured bytes/leg including indexes | 1 million legs, extrapolated | 20 million legs, extrapolated |
| --- | ---: | ---: | ---: |
| Scheduled | 469 | 0.44 GiB | 8.74 GiB |
| Completed, including actual times | 536 | 0.50 GiB | 9.98 GiB |
| Completed plus proposed observed evidence | 593 | 0.55 GiB | 11.05 GiB |

The last profile retains every current flight field and adds a hypothetical
companion evidence table with ICAO address, callsign, four timing bounds stored
as integer microseconds and a shared provenance method. That schema is a storage
experiment, not an implemented observed-time serving contract. The benchmark's
records are synthetic; marketing identities and scheduled times are not inferred
from ADSB.lol. Actual string lengths, indexes, codeshares, source receipts and
deduplication rules affect the final size.

The four real December 30 candidate records average **380 bytes each as compact
JSON**, excluding database indexes; all four together gzip to 386 bytes. Four
candidates are too small and repetitive to establish a dataset compression ratio.
A three-leg itinerary references three stored legs (about 1.7 KiB at the proposed
593-byte profile); it creates no additional persistent itinerary rows. API JSON
and airport metadata have their own response overhead.

At the proposed profile, a 15 GiB facts allowance projects to about 27 million
legs. This is a capacity estimate, not a count of usable 2025 flights. A complete
day of validated leg extraction is still required to estimate year coverage,
working-space peaks and final database size.

### Raw archive volume and earlier benchmark

The [2025 source archive](https://github.com/adsblol/globe_history_2025) contains
repeated positions across all aircraft categories and heatmap replay data.
One preferred copy per UTC day totals **1,035.564 GiB** for 365 days; December 31
is hosted in the next year's repository. Average daily volume is **2.837 GiB**,
median **2.977 GiB**, and the range is **1.567–3.584 GiB**.

The complete December 30 benchmark examined **61,215 aircraft traces** and retained
**13,241 commercial fixed-wing candidate aircraft-days**. Those aircraft-days
contained 36,796,571 positions. Their full compressed traces alone would take
0.971 GiB for the day. All raw daily parts were deleted after that research scan.

A new synthetic storage benchmark uses the current flight schema, all indexes,
and an `aircraft_type_code` on every row:

| Flight rows with type code | Scheduled profile | Completed profile |
| --- | ---: | ---: |
| 100,000, measured | 47.82 MiB | 53.82 MiB |
| 1 million, extrapolated | 0.47 GiB | 0.53 GiB |
| 10 million, extrapolated | 4.67 GiB | 5.26 GiB |
| 20 million, extrapolated | 9.34 GiB | 10.51 GiB |

These synthetic measurements support keeping compact flight records inside
20 GiB while leaving room for one raw day and temporary files. They do not
establish the number of usable 2025 legs or the size of an observed-time schema
with confidence/timing bounds. Measure that schema on a validated full-day leg
extraction before committing to a whole-year import.

A one-time archive scan still transfers about **1.01 TiB**, even when positions
are discarded immediately. At 100 Mbps that is about 25 hours of transfer alone;
decoding and upstream throttling add time. The full-year scan has not started.

## What this PR supports

Scheduled flight records now have an optional flat `aircraft_type_code` field.
It survives SQLite storage, edits, JSON/CSV imports, snapshots, exports and API
responses. The route detail card displays it. Missing codes remain unknown.
The ingestion/admin writer migrates existing flight files. Serving queries durable
flight facts without editing them; configured downloads manage cache metadata and files.

The route calendar enables only dates containing non-cancelled departures from
the selected origin, in the chosen UTC or airport-local clock. Archive inventory
dates and aircraft-day research summaries do not enable that calendar.

Research probes are separate from serving:

```sh
# Research one complete archive day, with a 20 GiB allowance.
PYTHONPATH=src python scripts/probe_adsblol.py catalogs/adsblol-2025.json \
  --filter fixtures/adsblol-commercial-filter.json --date 2025-12-30 \
  --data-root var/adsblol --download --discard-raw

# Inspect inferred leg candidates from an existing local research sample.
PYTHONPATH=src python scripts/probe_adsblol_legs.py sample.json.gz \
  --airports airports.csv --filter fixtures/adsblol-commercial-filter.json
```

Neither command inserts routable flight records. The production trace endpoints,
cache, archive offsets and aircraft observation tables have been removed. The
previous byte-range timing remains a research result only.

Data source attribution: ADSB.lol, [ODbL 1.0](https://www.adsb.lol/docs/open-data/historical/).
The archive inventory covers all 365 days. Actual research observation summaries
cover December 30 only. No ADSB-derived flights are in the serving database yet.
