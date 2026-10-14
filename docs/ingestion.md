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
longitude and altitude/ground marker. Row flags identify stale positions;
row `[8].flight` supplies the callsign. Top-level `t` is the aircraft type;
`dbFlags & 1` marks military metadata. Callsigns are not verified marketing numbers.

## Daily extraction

```text
manifest → download/verify one day → stream aircraft members → filter/reconstruct
         → disposable staging → atomic shared-database commit → delete raw day
```

- Resume partial downloads and verify lengths/checksums. Stream tar members;
  do not extract arbitrary archive paths or retain position files.
- Apply the [fixed-wing/type/callsign filter](../src/ingestion/adsblol/commercial-filter.json),
  excluding military metadata. The commercial callsign list includes cargo.
- Require ground observations at both ends, an unambiguous airport within 5 km,
  continuous transition brackets/gaps of at most 120 seconds and at least 300
  seconds between first and last airborne observations. Reject stale/missing
  observations, changing callsigns, unknown types and ambiguous endpoints.
- Save source identity, airport pair, callsign, aircraft type, four time bounds
  and provenance. Compact overnight summaries connect adjacent-day backfills;
  raw positions/speeds/tracks are discarded.
- Commit legs, boundaries, successful receipt and batch diagnostics together.
  Readers see completed days throughout parsing. Failed days apply zero legs
  and keep raw inputs for retry; successful days remove their verified inputs.

The [typed schema](database-schema.md) powers calendar, routing, lookup, export
and admin directly. Stable source keys deduplicate overlapping jobs. Manual edits
and deletion tombstones survive passive reingestion. Observed times remain labelled
inferred bounds; missing schedules, commercial flight numbers and timezones stay
unknown. Coverage is conservative and incomplete.

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
fetch raw flight archives. Existing snapshots/filters remain immutable. Alternatively,
`ingest adsblol configure` accepts existing inputs. The filter is bundled with
admin, so installed commands work outside the checkout. Output defaults to the
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
receipts and boundary state while preserving admin configuration/quota. Old
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
invalid/ignored samples, gap/stale interruptions, endpoint/callsign/duration
rejections, midnight deferrals and boundary joins. Failed batches retain diagnostics
without advancing coverage. `--refresh-diagnostics` explicitly reprocesses selected
days; retained inputs or redownloads are required. These counters describe aircraft,
sample rows and movement fragments, **not missing-flight percentages**. Compare
accepted legs against a trusted flight list before claiming completeness.

## Measured storage

The prepared July–December 2023 manifest plus one arrival-boundary day transfers
about **283.93 GiB** across 185 daily inputs; its largest day is **1.81 GiB**.
Only the current day needs raw working space. A historical December 30, 2025 pilot
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
