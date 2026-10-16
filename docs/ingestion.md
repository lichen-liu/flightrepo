# Ingestion pipeline

Process one archive day at a time, save compact observed itinerary candidates
into a private ingestion SQLite file, then delete verified raw inputs. Explicit
publication copies eligible flight facts and airport metadata into serving. There is no itinerary catalogue, serving downloader or flight chunk cache.
ADSB.lol supplies historical observations, not future airline schedules.

## Sources and licences

For researched alternatives and measured free bulk access, see the
[flight data source reference](flight-data-sources.md). It distinguishes installed
pipelines from proposed reference/schedule adapters.

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
there is no checked-in annual inventory. `adsblol prepare` in the producer CLI
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
         → disposable staging → atomic ingestion-database commit → delete raw day
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
  have separate evidence labels and can appear in search without manual approval
  when airport estimation is their only review reason. Route options and leg
  details explicitly identify estimated airports and whether they were manually verified.
  Ground candidates with sample gaps over 30 minutes still require review:
  hidden intermediate stops can otherwise resemble one flight. Shorter reception
  gaps and changing callsigns no longer require review on their own. Keep the
  dominant callsign and log variations; source leg markers still split movements.
  Candidates whose airport distance requires more than 1,500 km/h even across
  their widest retained time window also require review (`implausible_airport_timing`).
  This is a generous best-effort plausibility bound, independent of aircraft type;
  it retains the record and does not certify slower candidates as correct.
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
  Ingestion readers see completed days throughout parsing. Serving changes only on publication. Failed days apply zero legs
  and keep raw inputs for retry; successful days remove their verified inputs.

The common [typed schema](database-schema.md) preserves flight evidence across
publication and powers serving calendar, routing, lookup, export and admin. Source keys anchor an aircraft's final airborne arrival
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
| Search review | Sample gaps over 30 minutes stay held unless explicitly confirmed by VRS under the best-effort policy; timing/other reasons still block. Airport-estimate-only records are labeled unless VRS-confirmed |
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
`observed-itinerary-v5` are recorded in batch/configuration provenance; changed
rules require a fresh output database so old receipts cannot silently skip work.
`review_gap_seconds` adjusts the 1,800-second review threshold independently of
the six-hour parsing limit. The source identity namespace remains stable across
algorithm versions, so changing quality rules does not invent new flight IDs.
Malformed/unordered archive records, checksum failures and quota violations fail
an entire day without applying partial flight facts. Endpoint estimates still
need empirical validation; higher retained counts do not prove flight coverage.
Admin `flights --needs-review` inspects private held candidates. Producer APIs
may record explicit validation decisions; admin cannot approve or edit private
candidates. Serving admin can still approve/edit published records. Automatic
estimate admission never sets `itinerary_reviewed`. External route-reference
documents do not approve or publish anything by themselves.

## Interactive jobs and resume

```sh
python -m ingestion --db var/ingestion/ingestion.db init
python -m ingestion --db var/ingestion/ingestion.db
```

```text
ingest> sources
ingest> adsblol prepare --start 2023-07-01 --end 2023-12-31 --finish-year
ingest> adsblol run --start 2023-07-01 --end 2023-07-03
ingest> adsblol run --start 2023-08-10 --end 2023-08-12
ingest> adsblol status
ingest> publish --serving-db var/hosted/flights.db --start 2023-07-01 --end 2023-07-03
```

Installed `flyji-ingest` is the same producer CLI. `FLIGHT_INGESTION_DATABASE_PATH`
sets its default; `FLIGHT_DATABASE_PATH` selects serving/admin and is independent.
Preparation saves metadata/input paths, not raw flight archives. Existing CSV
snapshots and explicit policies remain immutable. `adsblol configure` accepts
existing inputs. `--db` is the sole writable destination; a different
`--output-db` is rejected. `--data-root` selects disposable working space.

Every UTC day commits independently. Separate, overlapping and out-of-order
ranges remain supported. Guided jobs suggest three days; explicit ADSB ranges
can span a year. CSV/API jobs retain the configured 31-day default limit.
Ctrl-C preserves successful days. `--offline` restricts processing to verified
local raw inputs. `--departure-year` filters UTC departures. For December 31,
`--finish-year` adds January 1 arrival evidence, excluding next-year departures;
prepare the extra day first. `--boundary-manifest` supplies existing adjacent inputs.

```sh
python -m ingestion --db var/ingestion/ingestion.db csv run \
  --csv var/licensed.csv --airports YYZ YUL --start 2023-07-01 --end 2023-07-03
python -m admin --db var/ingestion/ingestion.db ingest adsblol status
python -m admin --db var/ingestion/ingestion.db inspect query \
  "SELECT kind,count(*) FROM ingestion_documents GROUP BY kind"
python -m admin --db var/ingestion/ingestion.db maintenance clear-data --yes
```

Admin cannot launch producer jobs or edit/import/approve private facts, change
private quota or modify SQL. `clear-data` clears candidates, airports/transfers,
evidence, receipts and boundary/config state, retaining source profiles and quota.
`maintenance reset --yes` also clears source profiles/notices and restores the
20 GiB default quota. Both retain file/schema/store identity, increment generation,
refuse an active producer/extractor and leave serving untouched. Re-running jobs
starts fresh because completed-day receipts were cleared. Neither unlinks a live
SQLite file. Cleared pages remain available for SQLite reuse.

### Tool/API evidence

Python producers use `IngestionStore` and `IngestionService`. The latter refuses
a serving writer. `document FILE.json` (or `IngestionStore.document`) stores one
compact normalized route, schedule, airport, aircraft or validation document:

```json
{"source":"licensed-provider","kind":"route","key":"AAA-BBB:2024-01",
 "source_url":"https://provider.example/authorized-feed","source_group":"provider-family",
 "license_expression":"account-specific","valid_from":"2024-01-01T00:00:00Z",
 "valid_to":"2024-02-01T00:00:00Z","payload":{"origin":"AAA","destination":"BBB"}}
```

A stable `(source,kind,key)` deduplicates evidence; content hashes and revisions
track changed payloads. Fetched time and validity remain distinct; timestamps
require offsets. Each normalized payload is limited to 64 KiB. Do not store raw
trace archives here. Flight facts still use validated `FlightRecord` columns;
JSON reference documents never masquerade as flown legs. References require an
authorized provider/tool adapter: no FlightRoutes/FlightsFrom scraper, public
reference API or automatic validation policy is implemented. The Python API is
local/internal; no ingestion HTTP endpoint is exposed by the public backend.

### Publication

For legacy adoption, bulk audit/cohort review and preserving canonical admin state,
see the [adoption and audit workflow](ingestion-review.md). Producer `adopt` and
`audit` are separate from admin; audit writes require `--apply` and a private store.


Publish an explicit inclusive UTC range, one atomic transaction per day. It
copies non-held flight facts (including partial/cancelled records for lookup),
the airport/transfer catalog and relevant source attribution. Search keeps its
usual complete-time/cancellation filters. Airport estimates retain visible
labels and unverified status. Gaps/timing holds and unrelated reference documents
stay private. Receipts, raw inputs, boundary state and profiles never copy.

Publication is repeatable: source keys deduplicate, upstream corrections update,
and previously published records that become held/missing retract. Serving admin
edits and deletion tombstones win; explicit prior human approvals remain protected.
A serving file has one publishing store identity to prevent conflicting owners.
Clearing ingestion alone does not change serving; a later explicit publication
reconciles only its requested dates. Never swap or rename an ingestion database
into the serving path: the backend rejects its stored role.

## RAM-backed raw downloads

For this host, temporary daily archives use a **4 GiB RAM disk**. Durable SQLite
files, receipts, boundary state and logs remain on disk. Only one raw day is held;
verified inputs are removed after commit. The largest selected 2023 daily copy is
about 1.81 GiB, leaving room for the downloader's mandatory 1 GiB free-space reserve.
Larger future daily selections fail the free-space check rather than overflow.
RAM filesystems may be swapped by the OS; this reduces archive writes but cannot
promise zero SSD writes. Do not disable system swap to run ingestion.

Mount a bounded RAM filesystem before starting: macOS `hdiutil attach -nomount
ram://8388608` creates a 4 GiB device. Format **only that newly returned empty
RAM device** with `diskutil eraseVolume HFS+ FlightIngestRaw <returned-device>`.
On Linux, use an explicitly size-limited `tmpfs` mount. Then bind a working root:

```sh
python -m ingestion.adsblol.scratch \
  --data-root var/ingestion/adsblol-work \
  --mount /Volumes/FlightIngestRaw \
  --output-db var/ingestion/ingestion.db \
  --wait
```

Use that same `--data-root` for the normal producer job. This operation only
configures temporary storage; it neither initializes nor changes database rows.
It verifies the mount is a macOS `ram://` device or Linux `tmpfs`, obtains the
extractor's daily/output locks, and links `raw` into a dedicated RAM directory.
`--wait` lets a running day finish, commit and clean its input before switching;
unfinished archives/partial downloads are never moved or discarded. A small
`raw.disk-before-ram` directory retains old selection/lock metadata only.

The durable `.raw-storage.json` and a volatile session marker identify the mount.
New producers recheck mount/device/link/session during working-space IO and stop
if it disappears; they never silently download to the old disk directory. The
working-space budget includes RAM raw/partial files independently of DB quota.
The already-running frozen legacy worker can use this same link without changing
its code; it retains its existing filesystem free-space guard. Keep its volume
mounted until the entire job stops. Before resuming any frozen worker, explicitly
check the recorded mount/session: it does not have the new per-IO mount checks.

RAM inputs are disposable and vanish on reboot. To recover, create/remount RAM,
bind a **fresh working root**, and resume the same date range/output database with
that root. Completed days and cross-day boundaries are in SQLite and are reused;
only an uncommitted day is downloaded again. Do not reset the database or remove
its receipts. Never unmount while a worker is running. After all workers stop and
raw archives are gone, detach the recorded RAM device to release its memory.

## Quota and observability

`database_policy.quota_bytes` stores the live database limit (default 20 GiB).
Producer `init --quota-bytes N` updates it on the selected ingestion file, including
from another producer process. Admin can inspect it with `storage quota`. It counts SQLite and
WAL/SHM only; raw/partial archives and daily staging are excluded. `--budget-gib`
independently limits working space (default/max 20 GiB). Downloads preserve 1 GiB
of free filesystem space. Provision database space **plus** one raw day and staging.

Ingestion rechecks quota during IO and before commit, including pending page
growth. An oversized day rolls back. Lowering quota preserves completed facts,
keeps reads available and pauses new ingestion when over limit. There is no LRU
retirement: accepted legs are the sole retained copy. WAL/checkpointing or active
readers can cause temporary physical overage, rechecked on later ingestion IO.

JSON stderr events report `day_started`, provisional `day_progress`, `day_finished`
and `day_failed`. Receipts and producer `adsblol status` report aircraft/type exclusions,
invalid/ignored samples, reception-gap markers, gaps grouped by duration,
per-record review reasons, endpoint/callsign/duration
rejections, midnight deferrals and boundary joins. Failed batches retain diagnostics
without advancing coverage. `--refresh-diagnostics` explicitly reprocesses selected
days; retained inputs or redownloads are required. These counters describe aircraft,
sample rows and movement fragments, **not missing-flight percentages**. Compare
accepted legs against a trusted flight list before claiming completeness.

### January 2024 pilot

Run the relaxed rules in a separate database before replacing the July–December
2023 extraction. Prepare December 31 through February 1 for overnight context;
report January departures separately from these boundary days. Use only ADSB.lol
observations and the airport reference already used by extraction. There are no
tail lookup APIs, commercial operator lists or external flight-validation joins.

Report daily candidate/searchable/review counts, available aircraft types,
exclusions and rejection reasons, database bytes, and successful raw-input cleanup.
`metadata.accepted_under_relaxed_review` counts evaluated candidates that no longer
need review because of the relaxed gap/callsign rules. These candidate events can
include later boundary refinements; use database counts for unique itineraries.
Treat a pilot as operationally successful only after all days commit, database
integrity/foreign-key and duplicate checks pass, itinerary time bounds are valid,
and search/calendar can read eligible records. Inspect representative airport
pairs and unusually long legs using retained itinerary fields. This is a
best-effort reconstruction, not a completeness or accuracy guarantee. Airport
estimates alone are searchable with visible labels; gap and timing cases remain
private and available to admin for read-only inspection.

## Measured storage

The prepared July–December 2023 manifest plus one arrival-boundary day transfers
about **283.93 GiB** across 185 daily inputs; its largest day is **1.81 GiB**.
Only the current day needs raw working space. A relaxed July 10, 2023 pilot
reconstructed **21,048 candidates** from a **1.13 GiB** archive containing 42,846
aircraft-days: 1,322 had ground endpoints and 19,726 used airborne proximity.
Under the earlier blanket estimate exclusion, **287** candidates were searchable
and **20,761** awaited review. The ingestion database occupied about **31.5 MiB**, including airport metadata,
receipts and overnight summaries. These historical admission counts predate the
current labeled-estimate policy and do not measure complete flight coverage.

A limited independent comparison against the official
[BTS July 2023 reporting-carrier file](https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2023_7.zip)
found 4,992 matching airport pairs and 375 disagreements among comparable numeric
callsigns for nine US carrier prefixes. Most disagreements were proximity
estimates; some ground candidates crossed unobserved stops. This check does not
verify tail identity, timestamps, types or worldwide recall. It motivated the
earlier blanket review gate, rather than a claim of lossless extraction. Under that earlier gate,
all 375 disagreements were retained for review; 70 comparable searchable records
agreed and one had no reference match. This small subset does not establish
global accuracy. The current labeled-estimate admission intentionally accepts
some airport-assignment risk; those earlier agreement results do not establish
accuracy for the expanded searchable set.

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

## Selected verification and timing scope

ADSB.lol remains the main source. Use the producer-only [VRS bulk reference and
cohort report](vrs-verification.md) after private-store adoption; its undated matches
never clear holds or change flight facts. Flight.info/Flightradar24 are requested
historical timing references, but automatic extraction is not enabled because
historical coverage and persistent-use access are unresolved. Keep scheduled,
gate and airborne timing separate. Future scheduled-flight ingestion is deferred.
See [the current source decision](flight-data-sources.md#current-selection-historical-operated-flights-only).

The explicit [VRS admission command](vrs-verification.md#explicit-best-effort-route-admission)
can now confirm directed airport pairs, replace the estimated-airport badge and
waive observation-gap holds. Source reasons and inferred timing are retained;
import/report alone never applies this policy. This supersedes research-only VRS notes.
