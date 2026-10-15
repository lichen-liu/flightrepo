# Ingestion pipeline

Process one archive day at a time, save compact observed itinerary candidates
into the same SQLite file used by the API and admin, then delete verified raw
inputs. There is no itinerary catalogue, serving downloader or flight chunk cache.
ADSB.lol supplies historical observations, not future airline schedules.

## Sources and licences

| Source | Retained information | Data licence |
| --- | --- | --- |
| [ADSB.lol historical releases](https://www.adsb.lol/docs/open-data/historical/), including [2023](https://github.com/adsblol/globe_history_2023/releases) | Airport pair, callsign, ICAO aircraft type and inferred time bounds | [ODbL 1.0](https://github.com/adsblol/globe_history_2023/blob/main/LICENSE-ODbL.txt) |
| [OurAirports](https://ourairports.com/data/) airport CSV | Codes and coordinates for endpoint matching | Public domain |

Treat extracted ADSB records as a derivative database. Retain ADSB.lol attribution
and licence links; distribution must follow ODbL notice/share-alike and database
access conditions. Source notices are stored once in `data_sources` and shown by
the frontend. Code has its own licence. CSV/AeroDataBox imports retain their feed
or account's licence; these paths can supply schedules without turning observed
ADSB times into schedules.

## Raw inputs

An **archive manifest** lists preferred daily raw archive parts: UTC date, URL,
filename, byte length and optional SHA-256. It is an ingestion input, not a
searchable itinerary catalogue. Keep generated manifests under ignored `var/`;
there is no checked-in annual inventory. `ingest adsblol prepare`
builds it from preferred-release URLs and GitHub release metadata. Adjacent
year repositories can host year-end days. Download one preferred copy, not replicas.

Tar members `traces/xx/trace_full_<icao>.json` contain gzip-compressed JSON:

```json
{
  "icao": "a5ea5c", "t": "A320", "dbFlags": 0, "timestamp": 1688169600,
  "trace": [[0, 40, -70, "ground", 0, 0, 0, 0, {"flight": "UAL123"}]]
}
```

In the [readsb format](https://github.com/wiedehopf/readsb/blob/dev/README-json.md#trace-jsons),
`timestamp` is Unix seconds and each row starts with its time offset, latitude,
longitude and altitude/ground marker. Row flag `1` marks a newly accepted position after a reception gap;
row `[8].flight` supplies the callsign. Top-level `t` is the aircraft type;
`dbFlags & 1` marks military metadata. Callsigns are not verified marketing numbers.

The [2023 readsb implementation](https://github.com/wiedehopf/readsb/blob/54c0272f584f7841b9befb8c10690d81aa01158c/track.c#L1168)
sets the flag before replacing the reliable coordinates with the newly decoded
position. It describes the preceding reception interval, not unusable coordinates.

## Daily extraction

```text
manifest → download/verify one day → stream aircraft members → filter/reconstruct
         → disposable staging → atomic shared-database commit → delete raw day
```

- Resume partial downloads and verify lengths/checksums. Stream tar members;
  do not extract arbitrary archive paths or retain position files.
- Exclude military metadata and explicitly non-fixed-wing/ground emitter
  categories. There are no aircraft-type or callsign whitelists. Missing type and
  callsign remain unknown; the feed does not reliably certify commercial service.
- Use all eligible IATA airports, including those without scheduled service.
  Match the nearest airport within 25 km; reject near ties within 1 km.
- Prefer observed ground endpoints. When ground data is absent, allow low-altitude
  airport-proximity estimates (up to 3,500 ft above airport elevation), with a
  confirmed climb for an origin, a descending destination observation, and a
  30-minute estimated event window. When track is available, airborne matching
  rejects airports more than 45 degrees off the approach/departure heading
  (outside 3 km, where airport-center/runway offsets matter).
  A later climb/exit from the airport area cancels an arrival hint. Such records
  have separate evidence labels and require admin review before search.
  Ground candidates with sample gaps over 120 seconds or changing callsigns also
  require review: hidden intermediate stops can otherwise resemble one flight.
  These checks retain candidates rather than rejecting them; the six-hour parsing
  limit is independent of the serving review gate.
- Allow internal observation gaps up to six hours; terminal brackets up to 30
  minutes; require 60 seconds between first and last airborne observations.
  Source new-leg markers separate movements. Reception-gap flags do not invalidate
  newly received positions. Skip missing/invalid samples without erasing valid
  evidence or allowing skipped rows to bridge gaps.
- Retain the most frequent airborne callsign, if present, without requiring a
  known operator or rejecting variations. Log missing metadata and variations.
- Save source identity, airport pair, available callsign/type, four time windows
  and evidence provenance. Compact overnight summaries connect adjacent-day
  backfills; raw positions/speeds/tracks are discarded.
- Commit legs, boundaries, successful receipt and batch diagnostics together.
  Readers see completed days throughout parsing. Failed days apply zero legs
  and keep raw inputs for retry; successful days remove their verified inputs.

The [typed schema](database-schema.md) powers calendar, routing, lookup, export
and admin directly. Source keys anchor an aircraft's final airborne arrival
observation, so backfilling departure evidence refines the same record rather than
creating another flight. More complete context replaces an earlier estimate;
weaker reconstructions cannot overwrite it. Updated/duplicate counts are separate.
Manual edits, explicit review approvals
and deletion tombstones survive passive reingestion. Inferred event windows are
not airline schedules or certified actual times. Missing commercial flight
numbers and timezones stay unknown. Removing whitelists also admits private
fixed-wing movements; commercial-only coverage cannot be established from these
fields alone.

### Default inference limits

| Check | Requirement |
| --- | --- |
| Identity/military | Six lowercase hexadecimal ICAO characters; exclude `dbFlags & 1` |
| Type/callsign | Optional metadata, no whitelists; retain a dominant callsign if it varies |
| Airports | IATA-coded, neither closed, heliport nor seaplane base; scheduled service not required |
| Matching | Nearest airport within 25 km; reject alternatives within 1 km of its distance |
| Airborne endpoint fallback | Within 3,500 ft of airport elevation; origin needs a 100-ft observed climb or 300 ft/min upward rate; destination descending/nonascending; available heading within 45 degrees beyond 3 km; estimated window 30 minutes |
| Ground endpoints | Preferred, but no longer required at both ends |
| Continuity | Maximum six-hour gap between usable samples, including overnight joins |
| Terminal bounds/duration | Maximum 30-minute endpoint bracket; minimum 60-second airborne span |
| Bad samples | Skip without advancing evidence; report the reason |
| Reception-gap flag | Keep the newly received position; log flag `1` rather than discarding it |
| Emitter category | Explicit `A7`, `B1`, `B2`, `B3`, `B4`, `B6`, `B7`, `C1`, `C2`, `C3` exclude the aircraft-day, including metadata on reception-gap-marked positions |
| Day/year | Current UTC-day samples; optional departure-year restriction requires both departure bounds within that year |

Optional `--policy FILE` contains fields from
[ExtractionPolicy](../src/ingestion/adsblol/policy.py), for example
`{"max_gap_seconds":21600,"airport_radius_km":25}`. It cannot contain aircraft
or callsign lists. `{"allow_airborne_endpoints":false}` requires ground evidence
at both ends without adding any aircraft/operator whitelist. Defaults need no
configuration file. Policy and algorithm
`observed-itinerary-v3` are recorded in batch/configuration provenance; changed
rules require a fresh output database so old receipts cannot silently skip work.
Malformed/unordered archive records, checksum failures and quota violations fail
an entire day without applying partial flight facts. Endpoint estimates still
need empirical validation; higher retained counts do not prove flight coverage.
`flights --needs-review` lists uncertain candidates; `flight approve PROVIDER ID`
makes a checked/corrected record searchable without erasing its inference label.

## Interactive jobs and resume

```sh
python -m admin --db var/hosted/flights.db
```

```text
flyji> ingest sources
flyji> ingest adsblol prepare --start 2023-07-01 --end 2023-12-31 --finish-year
flyji> ingest adsblol run --start 2023-07-01 --end 2023-07-03
flyji> ingest adsblol run --start 2023-08-10 --end 2023-08-12
flyji> ingest adsblol status
flyji> storage quota 20GiB
flyji> storage status
```

Preparation downloads metadata and the airport CSV, saving a profile; it does not
fetch raw flight archives. Existing snapshots and explicit quality-policy files remain immutable. Alternatively,
`ingest adsblol configure` accepts existing inputs. Quality defaults are part of
admin, so installed commands work outside the checkout without filter artifacts. Output defaults to the
selected database. Separate, overlapping and out-of-order ranges remain supported.
Every day commits independently; guided jobs suggest three days, while explicit
ADSB ranges can span a year. CSV/API jobs retain the 31-day default limit.

`--offline` restricts processing to verified raw inputs already present.
`--departure-year` filters departures by UTC year. For December 31,
`--finish-year` adds January 1 arrival evidence and excludes next-year departures;
prepare that extra day first. `--boundary-manifest` can supply existing adjacent
inputs. The same admin commands work interactively and in a terminal; there is
no standalone ingestion script.

```sh
python -m admin --db var/hosted/flights.db ingest adsblol run \
  --start 2023-07-01 --end 2023-12-31 --finish-year
python -m admin --db var/hosted/flights.db ingest csv run \
  --csv var/licensed.csv --airports YYZ YUL --start 2023-07-01 --end 2023-07-03
```

Stop ingestion before `maintenance clear-data --yes`; reset clears records,
receipts and boundary state while preserving admin configuration/quota. This
clears rows, not schema: when changing this unreleased schema, stop all readers
and writers and initialize a fresh SQLite file, rather than retaining old DDL. Old
receipts must never be copied into a fresh database, since they would skip days.

## Quota and observability

`database_policy.quota_bytes` stores the live database limit (default 20 GiB).
`storage quota` changes it from another admin process. It counts SQLite and
WAL/SHM only; raw/partial archives and daily staging are excluded. `--budget-gib`
independently limits working space (default/max 20 GiB). Downloads preserve 1 GiB
of free filesystem space. Provision database space **plus** one raw day and staging.

Ingestion rechecks quota during IO and before commit, including pending page
growth. An oversized day rolls back. Lowering quota preserves completed facts,
keeps reads available and pauses new ingestion when over limit. There is no LRU
retirement: accepted legs are the sole retained copy. WAL/checkpointing or active
readers can cause temporary physical overage, rechecked on later ingestion IO.

JSON stderr events report `day_started`, provisional `day_progress`, `day_finished`
and `day_failed`. Receipts and `ingest adsblol status` report aircraft/type exclusions,
invalid/ignored samples, reception-gap markers, gaps grouped by duration,
per-record review reasons, endpoint/callsign/duration
rejections, midnight deferrals and boundary joins. Failed batches retain diagnostics
without advancing coverage. `--refresh-diagnostics` explicitly reprocesses selected
days; retained inputs or redownloads are required. These counters describe aircraft,
sample rows and movement fragments, **not missing-flight percentages**. Compare
accepted legs against a trusted flight list before claiming completeness.

## Measured storage

The prepared July–December 2023 manifest plus one arrival-boundary day transfers
about **283.93 GiB** across 185 daily inputs; its largest day is **1.81 GiB**.
Only the current day needs raw working space. A relaxed July 10, 2023 pilot
reconstructed **21,048 candidates** from a **1.13 GiB** archive containing 42,846
aircraft-days: 1,322 had ground endpoints and 19,726 used airborne proximity.
After per-record review reasons were added, **287** candidates were searchable
and **20,761** awaited review. The shared database occupied about **31.5 MiB**, including airport metadata,
receipts and overnight summaries. Candidates with uncertainty remain unserved
until review; this is not a measurement of complete flight coverage.

A limited independent comparison against the official
[BTS July 2023 reporting-carrier file](https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2023_7.zip)
found 4,992 matching airport pairs and 375 disagreements among comparable numeric
callsigns for nine US carrier prefixes. Most disagreements were proximity
estimates; some ground candidates crossed unobserved stops. This check does not
verify tail identity, timestamps, types or worldwide recall. It motivated the
review gate, rather than a claim of lossless extraction. With that gate applied,
all 375 disagreements were retained for review; 70 comparable searchable records
agreed and one had no reference match. This small subset does not establish
global accuracy.

Under the older strict extractor, a historical December 30, 2025 pilot
processed 2.98 GiB/61,215 aircraft-days into 2,138 accepted legs across 39 types.
Its earlier JSON representation used about 504 bytes/leg with the identity index;
a compacted typed-fact probe used about **244 bytes/leg** with the identity and two
observed-time indexes. Aircraft-days and flight-leg counts are not comparable.

At 244 bytes/leg, 20 GiB would hold roughly 88 million fact rows before metadata:
about 4.8 years at 50,000 legs/day or 2.4 years at 100,000. This is a fact-only
estimate. In a pre-reset live sample, 3,783 accepted legs over 69 days occupied
0.93 MiB of facts/indexes, while overnight summaries/index occupied 58.3 MiB.
Boundary state, diagnostics, airport metadata and WAL materially affect capacity.
Validate coverage first; compact boundary state without breaking disjoint backfills
before extrapolating whole-database storage or ingesting more years.
