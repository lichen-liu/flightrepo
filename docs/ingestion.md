# Ingestion pipeline

Collect compact historical itinerary candidates in **private ingestion SQLite**,
audit them, then explicitly publish flight facts into **serving SQLite**. Process
one raw day at a time; retain aircraft types, airport/operator metadata and time
bounds, not aircraft traces. There is no itinerary download catalogue or serving
fetch-on-demand cache. Future schedules and Flight.info/Flightradar24 timing
imports are not enabled.

See [source research](flight-data-sources.md) for evaluated alternatives and measured
results, [schema/API contracts](database-schema.md) for data semantics, and
[admin](admin.md) for local inspection and serving edits.

## Sources and licences

| Source | Purpose | Licence |
| --- | --- | --- |
| [ADSB.lol historical releases](https://www.adsb.lol/docs/open-data/historical/) | Daily observations → itinerary bounds, callsign and observed ICAO aircraft type | [ODbL 1.0](https://github.com/adsblol/globe_history_2023/blob/main/LICENSE-ODbL.txt) |
| [OurAirports CSV](https://davidmegginson.github.io/ourairports-data/airports.csv) | Airport identity, names, geography, coordinates and extraction elevation | [Public domain](https://ourairports.com/data/) |
| [VRS routes](https://vrs-standing-data.adsb.lol/routes.csv) and [airport mappings](https://vrs-standing-data.adsb.lol/airports.csv) | Undated directed route plausibility | [CC0-1.0](https://github.com/vradarserver/standing-data/blob/main/LICENSE) |
| [VRS airlines CSV](https://raw.githubusercontent.com/vradarserver/standing-data/main/airlines/schema-01/airlines.csv) | ICAO operator names and IATA references | CC0-1.0 |
| [Timezone Boundary Builder](https://github.com/evansiroky/timezone-boundary-builder), bundled in timezonefinder 8.2.4 | Offline coordinate-to-IANA-zone reference | ODbL-1.0; lookup code MIT |

Extracted ADSB records are treated as a derivative database. Preserve attribution,
ODbL notices/share-alike and applicable database-access requirements. Public source
credits appear on **Data sources & licences**, `/data-sources`; per-flight source
and audit evidence stay on the backend. CSV/AeroDataBox adapters retain their
feed/account-specific rights; account access is not a permanent-redistribution grant.

## Interactive jobs and resume

```sh
python -m ingestion --db var/ingestion/ingestion.db init
python -m ingestion --db var/ingestion/ingestion.db
```

In the producer shell, after mounting RAM as described below:

```text
ingest> sources
ingest> adsblol prepare --start 2023-07-01 --end 2023-12-31 --finish-year
ingest> adsblol workspace --ram-mount /Volumes/FlightIngestRaw
ingest> adsblol run --start 2023-07-01 --end 2023-07-03
ingest> adsblol run --start 2023-08-10 --end 2023-08-12
ingest> adsblol status
ingest> publish --serving-db var/hosted/flights.db --start 2023-07-01 --end 2023-07-03
```

Installed `flyji-ingest` is the same producer. `FLIGHT_INGESTION_DATABASE_PATH`
selects private ingestion; `FLIGHT_DATABASE_PATH` independently selects serving/admin.
`--db` is the only pipeline output; a conflicting `--output-db` is rejected.
`--data-root` holds working inputs/metadata; `prepare` discovers releases and pins
manifest/airport inputs without downloading flight archives. `configure` accepts
existing inputs. Generated manifests and data belong under ignored `var/`.

Separate, overlapping and out-of-order ranges are supported. Successful UTC days
commit independently and skip on retry; stable source keys deduplicate flights.
Ctrl-C preserves completed work. Guided batches suggest three days; explicit ADSB
ranges may span a year. `--offline` uses verified local raw inputs only.
`--departure-year` restricts departures. `--finish-year` adds January 1 arrival
context while excluding next-year departures; prepare that extra input first.
`--boundary-manifest` supplies existing adjacent inputs.

Other supported producers are `csv configure/run/status` for licensed CSV and
`aerodatabox configure/run/status` for account-authorized airport collections.
Their normal query limit is 31 days; AeroDataBox needs `FLIGHT_PROVIDER_API_KEY`.
They do not turn ADSB observation windows into airline schedules.

## Daily verification and live updates

For a serving site, use the explicit daily workflow rather than collecting a
whole range privately and publishing at the end:

```sh
flyji-ingest --db var/ingestion/ingestion.db daily \
  --serving-db var/hosted/flights.db \
  --start 2024-01-01 --end 2024-12-31 --finish-year
```

It uses the prepared ADSB profile and verified RAM workspace. Each input commits
privately, passes the retained-fact/structural audit, applies supported flag
corrections and the pinned VRS audit, then publishes eligible facts atomically.
Only then does it advance to the next raw day. Complete local legs become visible
immediately; processing day D also rechecks and republishes D-1 to include overnight
joins. The extra January 1 input completes December 31 without publishing next-year
departures. VRS matches are undated corroboration, not proof of historical timing.

Existing committed inputs are reused without downloading them again, making the
same command useful to catch up a private backlog. Per-input validation documents
retain audit counts and publication receipts. Unchanged completed stages skip;
interrupted audit/publication retries are idempotent. A failed check or quota stops
the workflow before the next input while keeping committed facts and earlier live
days. A producer lock spans all stages; another producer or admin reset cannot
interleave. Manual serving edits/tombstones and each database's quota still apply.
Raw/staging remain on RAM, private evidence stays private, and serving readers stay
online. Existing browser pages may need a refresh to fetch the updated calendar.

### Serving as the authoritative flight store

The daily workflow can keep verified flight records in serving and retain only
pending replacements and pipeline history in private ingestion:

```sh
flyji-ingest --db var/ingestion/ingestion.db daily \
  --serving-db var/hosted/flights.db --serving-authoritative \
  --start 2024-01-01 --end 2024-12-31 --finish-year
```

This is an explicit, persistent binding to that serving database's identity and
publisher. Subsequent daily commands recognize it without repeating the flag.
It changes the ingestion identity to version 2: older producer binaries reject
the database rather than interpreting released private copies as deletions.
It never changes the serving role or exposes pending candidates to the website.

After each day's serving transaction commits, the producer checks its publication
receipt and compares each candidate with the current canonical record. It releases
matching copies, or copies superseded by serving manual controls, in a separate
private transaction. An interrupted handoff leaves either an extra private copy or
the committed canonical flight. Retries handle both states. Changed/unpublished
candidates remain private; input receipts, audit journals, first-batch provenance,
reference evidence and overnight boundaries remain persistent.

Reverification stages the relevant canonical days back into ingestion, runs the
existing audit/VRS checks and republishes before releasing copies again. Extraction
also reads canonical records before comparing a backfill, so weaker reconstruction
cannot replace stronger evidence. Serving edits and tombstones continue to win.
Missing private rows mean the canonical record remains; an explicit private
tombstone or held replacement still retracts an owned publication. Standalone
publication uses the same handoff and bound destination checks.

The private `flight_legs` count now measures pending/staged records, **not total
ingested flights**. Count input completion from `extraction_days`, publication from
serving receipts, and retained flight totals from serving. Private audit/review
commands inspect the candidate workspace; a read-only full-fact audit should use
the serving database. A daily rerun stages and reverifies canonical windows when
their input, references or fingerprint changed. Bound pipeline history cannot be
cleared/reset independently of the authoritative store.

Canonical daily windows use an index on the current flight's publication-time
expression. A startup migration creates it under the producer/publication locks
and the existing serving quota, then reclaims the index build's WAL. Selection
uses current flight dates rather than the cached membership departure date, so a
manual date correction remains visible in its new window. Fingerprints read the
same ordered identities, records and airport/edit metadata in bounded batches;
the digest format, private-over-serving precedence and validation checks stay the
same. No input day begins before the preceding day's verified publication.

For an existing run, use a controlled cutover:

1. Finish/stop the producer and confirm its job/extraction locks are released.
   Keep the verified RAM mount and session. Use a frozen release containing both
   this feature and any archive-layout fixes required by the manifest.
2. Take and verify SQLite backups of both durable databases, including their
   committed WAL content, with writers stopped. Keep the existing runtime and
   launch configuration. Do not copy an open `.db` without its WAL.
3. Resume the **original full range** with `--serving-authoritative`. Already
   committed inputs are reused; previous published days are rechecked and their
   duplicate private facts are released progressively. Binding alone deletes no
   flights. Starting only at the next input leaves older private copies in place.
4. Verify daily receipts, serving totals/controls, input coverage and both quotas.
   Monitor serving as the authoritative dataset and private state as pending work.

Both databases remain on disk with separate persisted quotas; this feature does
not raise either limit. Boundary history can still occupy substantial space.
Released SQLite pages are reused by future work and do not immediately shrink the
physical file. No automatic VACUUM, reset, file replacement or RAM detach occurs.
Serving is now essential for recovery and needs a durable backup alongside pipeline
history. A rollback after release requires the supported runtime or a verified
pre-cutover database pair; an older producer cannot reconstruct serving from the
remaining private candidates alone. Missing/replaced serving files fail closed.

## Storage placement and RAM-backed workspace

**Raw archives and disposable per-day SQLite staging use RAM. Private ingestion
and serving databases stay on durable disk.**

| Artifact | Placement |
| --- | --- |
| Current archive, partial downloads, `.day-stage-*/day.db` and journal | Verified RAM mount; disposable |
| Extraction SQL temporary sorts | Process memory (`temp_store=MEMORY`) |
| VRS/operator CSV downloads | Bounded process-memory buffers; no temporary CSV file |
| Private/serving DB, WAL/SHM, committed boundaries and evidence | Durable disk outside RAM |
| Manifest, pinned airport CSV, receipts, logs and reports | Durable working directory |
| Adoption/serving replacement copies | Destination disk for safe SQLite handoff |
| Bulk audit correction-plan spool | OS temporary storage; not raw/day staging |

On macOS, `hdiutil attach -nomount ram://8388608` creates a 4 GiB device. Format
**only that newly returned empty RAM device** using
`diskutil eraseVolume HFS+ FlightIngestRaw <returned-device>`. On Linux, mount a
size-limited `tmpfs`. Then bind it:

```sh
flyji-ingest --db var/ingestion/ingestion.db adsblol workspace \
  --data-root var/ingestion/adsblol-work \
  --ram-mount /Volumes/FlightIngestRaw --wait
```

Use that working root for preparation and runs. Binding verifies macOS `ram://`
or Linux `tmpfs`, takes daily/output locks and persists the profile. `--wait`
lets an active day commit/clean up before binding; unfinished inputs are not moved.
A durable DB under the RAM mount is rejected. Status reports mode and paths.

RAM is required unless `--disk-workspace` is explicitly selected on workspace,
configure or run. A RAM-bound root never falls back to disk: mount, device, link
and session checks stop IO after mount loss. The durable `.raw-storage.json`
configuration and volatile marker identify the session. A bound root cannot be
changed to disk; choose another root for an explicit disk workflow.

After reboot, remount RAM, bind a **fresh working root**, and resume the same
range/DB. Committed receipts/boundaries survive; only unfinished inputs need
redownloading. Do not reset successful work. Never detach RAM while a worker runs;
release only its recorded device after workers stop and raw files are gone. The
low-level `python -m ingestion.adsblol.scratch` helper remains for frozen legacy
workers, whose mount must be checked manually before resuming.

A 4 GiB workspace accommodated the measured 1.81 GiB largest selected 2023 archive
plus staging and the downloader's 1 GiB free-space reserve. Larger inputs can
require more RAM. OS swapping means RAM scratch reduces archive writes without
guaranteeing zero SSD writes.

## Raw shape and extraction

The manifest records UTC day, preferred release URL, filename, byte length and
optional SHA-256. Adjacent-year repositories can host year-end inputs; download
one preferred release rather than replicas. Validate every day's archive layout
before starting the range: a standalone `.tar`, contiguous `.tar.aa/.tar.ab/...`
parts, or both with the same basename. A mixed release contains two independent
tar streams; concatenating them would stop at the first tar EOF marker.

Mixed releases index trace headers and combine matching aircraft members one
aircraft at a time, retaining observations unique to either snapshot. Absolute
microsecond timestamps establish order. The snapshot spanning the longest time
interval takes priority, then the one with more observations, then the standalone
archive. Equal-time rows retain this order; the existing reducer keeps the first
usable whole observation, without combining conflicting coordinates or rounding
nearby observations together. Military flags and excluded emitter categories from
either snapshot still apply. Conflicting normalized aircraft types become unknown
and increment `metadata.mixed_snapshot_type_conflict`; merged aircraft increment
`archive_members.merged_aircraft_snapshots`. All asset identities and checksums
remain in the receipt. Duplicate members within a stream still fail the day.

Tar members
`traces/xx/trace_full_<icao>.json` contain gzip-compressed JSON:

```json
{"icao":"a5ea5c","t":"A320","dbFlags":0,"timestamp":1688169600,
 "trace":[[0,40,-70,"ground",0,0,0,0,{"flight":"UAL123"}]]}
```

The [readsb trace format](https://github.com/wiedehopf/readsb/blob/dev/README-json.md#trace-jsons)
uses Unix-second base time, row offset/latitude/longitude/altitude or ground marker,
and row `[8].flight` callsign. `t` is the type; `dbFlags & 1` marks military metadata.
Flag `1` describes the preceding reception gap, not an invalid newly received position.

```text
verify one raw day → stream members → reconstruct legs → disposable RAM staging
→ atomic private DB commit (legs + boundaries + receipt + diagnostics) → delete raw
```

Downloads resume and verify sizes/checksums. Tar paths are never extracted to disk.
Failure applies zero legs and retains inputs for retry; successful days remove
their raw copies. Readers can access completed days throughout processing.

| Default check | Rule |
| --- | --- |
| Identity/military | Six hexadecimal ICAO characters; exclude military metadata |
| Type/callsign | Optional; no whitelists; retain dominant airborne callsign and log variations |
| Airport eligibility | IATA-coded; not closed, heliport or seaplane base; scheduled service not required |
| Endpoint matching | Nearest within 25 km; reject alternatives within 1 km of its distance |
| Ground endpoints | Preferred; not mandatory at both ends |
| Airborne fallback | Within 3,500 ft above airport elevation; origin needs 100-ft climb or 300 ft/min upward rate; destination nonascending; 30-minute event window |
| Heading | When available beyond 3 km, within 45 degrees of approach/departure; later climb/exit cancels arrival hint |
| Continuity | At most six hours between usable samples, including overnight joins |
| Duration/terminal bounds | At least 60 seconds airborne; endpoint brackets at most 30 minutes |
| Invalid samples | Skip without advancing evidence or bridging gaps; log reason |
| Emitter categories | Exclude `A7`, `B1`, `B2`, `B3`, `B4`, `B6`, `B7`, `C1`, `C2`, `C3` |
| Review concerns | Airport estimate; gap over 30 minutes; retained callsign variation; timing requiring over 1,500 km/h even across the widest window |

Endpoint lookup reuses at most 1,024 coarse-cell candidate lists per fixed
airport catalog. Every observation still uses its original coordinates and the
current radius, heading and ambiguity rules. A conservative latitude-separation
bound skips only airports necessarily outside the radius; a roughly one-centimeter
numerical guard leaves boundary cases to the original exact distance calculation.
Candidate lists are discarded with the lookup and rebuilt for a different catalog.
This changes neither extraction policy nor flight identities or resume receipts.

Review concerns retain candidates. All four known categories are searchable with
accuracy warnings; unknown reasons and unusable/cancelled/deleted facts remain
excluded. This does not certify commercial service: relaxed rules also retain
private fixed-wing movements. Aircraft-days are not individual flights.

`--policy FILE` accepts [ExtractionPolicy](../src/ingestion/adsblol/policy.py) fields,
e.g. `{"max_gap_seconds":21600,"airport_radius_km":25}`; it cannot add whitelists.
`allow_airborne_endpoints:false` requires ground endpoints. Algorithm
`observed-itinerary-v5` and policy are recorded; changed extraction rules require
a fresh output so old receipts cannot silently skip reprocessing. Raw positions,
speed and tracks are discarded; improved inference later requires redownloading.

## Reference catalogs and VRS audit

Install the producer extra with `python -m pip install -e '.[producer]'` (the dev
extra also includes it). Serving uses the stored values and pinned tzdata 2026.2;
it does not load timezonefinder. Resolver version, comprehensive geometry payload
hashes and attribution are pinned in `src/ingestion/timezone-data.json`.
All containing polygons are checked; overlapping civil zones, ocean-only matches
and missing coordinates remain unresolved. Geometry describes current boundaries;
IANA rules provide historical offsets, not historical airport boundary evidence.

```sh
flyji-ingest --db var/ingestion/ingestion.db references refresh
flyji-ingest --db var/ingestion/ingestion.db vrs import
flyji-ingest --db var/ingestion/ingestion.db vrs report \
  --start 2023-07-01 --end 2023-12-31 --report var/ingestion/vrs-review.json
flyji-ingest --db var/ingestion/ingestion.db vrs audit --apply \
  --start 2023-07-01 --end 2023-12-31
```

A fresh node downloads published CSVs; no hand-prepared repo artifact is required.
Each is capped at 64 MiB in memory. Pinned alternatives are
`references refresh --airports-csv PATH --carriers-csv PATH` and
`vrs import --routes-csv PATH --airports-csv PATH`. Receipts retain URLs, hashes,
retrieval times, licences, accepted/ignored counts and Last-Modified when supplied.
Whole-snapshot replacements are atomic; malformed input or failed refresh leaves
the old snapshot usable. Reference rows count against the private DB quota.

VRS normalizes exact callsigns by trimming/uppercasing and uses explicit airport
mappings. A–B–C supports directed A–B and B–C, never nonstop A–C. Outcomes are exact,
adjacent, different, incomplete or missing. Matches affect confidence, not original
endpoints/times/types, historical validity or human approval. Changed identities
invalidate old matches. Missing/disagreeing current routes cannot disprove an old
flight. `import`/`report` do not change facts; `audit --apply` is explicit (`admit`
is an alias). No fuzzy matching, scraper or per-flight external lookup is used.

Publication audits each day with `--vrs auto`: reuse a pinned snapshot or fetch
once if absent. `cached` forbids fetching, `refresh` explicitly replaces it, and
`skip` preserves existing results. Fetch failure stops auto/refresh publication.
Serving requests never download or audit. Public records show only general
accuracy warnings; reference evidence stays inside backend/admin.

ADSB preparation imports its pinned airport snapshot; publication obtains carrier
metadata once if absent. For a reference-only backfill without raw reprocessing:

```sh
flyji-ingest --db var/ingestion/ingestion.db references publish \
  --serving-db var/hosted/flights.db
```

Airport imports fill missing fields while preserving manual metadata and enrich
effective retained coordinates with timezone reference data. Carrier snapshots
withdraw stale source-owned mappings. Neither changes flight revisions, manual
decisions, timing or aircraft types.

Backfill existing airports without flight downloads/re-extraction:

```sh
flyji-ingest --db var/ingestion/ingestion.db references timezones --dry-run
flyji-ingest --db var/ingestion/ingestion.db references timezones
flyji-ingest --db var/ingestion/ingestion.db references publish --serving-db var/hosted/flights.db
```

Dry-run is read-only and requires an existing private database. Reports contain
coverage, changed/protected/unresolved counts, candidate mappings, a result hash
and source identities. Actual writes reject an active external producer; nested
daily stages reuse their existing job lock. Reference refresh and daily cached-day
resume automatically ensure timezone metadata. Cached flight receipts remain valid.

Generated zones update on data/coordinate changes; existing legacy/manual values
are protected. Explicit admin timezone imports pin even an unchanged value;
omitting timezone preserves it. For a private override, use producer
`references timezones --release-override YYZ`. For serving, use admin
`airports release-timezone YYZ`, then explicitly republish reference metadata.
Release clears the effective value until a new mapping is applied. A serving
coordinate override prevents publication of a mapping for different coordinates.

Pause writers and take a verified metadata backup before upgrading/backfilling;
install compatible producer/admin code together. Preview first and inspect
unresolved/changed cohorts, then apply and publish. Repeating unchanged enrichment
does not rewrite state. Retain reports and the metadata backup for rollback;
restore only values still owned by the rolled-back operation to protect later edits.
Source credits are published on `/data-sources`, with the derived reference-owned
mapping available under ODbL at `/v1/airport-timezones`. Private ownership and
per-flight audit evidence are not part of that export.

## Audit, review and publication

```sh
flyji-ingest --db var/ingestion/ingestion.db audit --report var/ingestion/audit-before.json
flyji-ingest --db var/ingestion/ingestion.db audit --apply --report var/ingestion/audit-applied.json
flyji-ingest --db var/ingestion/ingestion.db audit --report var/ingestion/audit-after.json
flyji-ingest --db var/ingestion/ingestion.db review queue --start 2023-07-01 --end 2023-12-31
flyji-ingest --db var/ingestion/ingestion.db review show \
  --start 2023-07-01 --end 2023-07-31 --origin YYZ --destination SEA \
  --reason observation_gap --samples 5 --report var/ingestion/review-case.json
```

Audit reports integrity/FKs, duplicate identities/signatures, coverage, type/metadata
availability and ranked reason/airport-pair cohorts. Corrections only align supported
derived timing/estimate flags; they never guess airports/times/types or merge flights.
Human edits/approvals are protected. Before/after revisions and evidence are journaled
atomically; quota rolls back failures. Investigate structural errors or skipped
concurrent changes and rerun the audit after applying corrections.

Review conflicts and large repeated cohorts using diverse samples. `review decide
--case FILE --action acknowledge --note TEXT` acknowledges only that unchanged
cohort; changed/new evidence reopens it. It does not approve flights or erase warnings.
For a single record, `review show --provider PROVIDER --record-id ID` creates a case;
`approve`, `reject` or `correct --replacement FILE` requires a note and current
revision. Corrections require a validated complete `FlightRecord` with the same
source identity; rejection preserves private evidence/tombstone. Bulk human approval
is not supported. Selective raw reprocessing needs adjacent-day context, not a reset.

Publish an inclusive UTC **departure** range explicitly, one atomic day at a time:

```sh
flyji-ingest --db var/ingestion/ingestion.db publish \
  --serving-db var/hosted/flights-published.db \
  --preserve-serving var/hosted/flights.db --start 2023-07-01 --end 2023-12-31
```

For normal incremental publication, use the existing serving target and omit
`--preserve-serving`. Publication copies eligible facts, airport/transfers/operators
and necessary provenance into backend serving storage. Partial/cancelled facts can
remain available for lookup while routing filters apply. Source keys deduplicate;
missing/newly excluded owned records retract. Serving edits, approvals and tombstones
win. Profiles, receipts, boundaries, private reference tables and review journals
never copy. One serving target has one owning publisher. Clearing ingestion alone
does not change serving; later publication reconciles only requested dates.

For a clean replacement, preserve canonical quota/manual routes/edits/approvals,
tombstones, airports/transfers and configuration. Canonical administrative drift
requires a new replacement. Stop canonical writers and recheck
`ingestion.serving_controls.verify_serving_controls(canonical,replacement)` before
cutover. Follow [deployment](../deployment/README.md) and SQLite stop/checkpoint/
replacement procedures, retain rollback, and release compatible backend/frontend
contracts together. Verify readiness, actual search, calendar and aircraft types.
**Never copy or rename a private ingestion DB into serving.**

`adopt` is only for a completed legacy unmarked v5 archive, not normal new jobs.
It requires a new destination, free producer/extractor locks, completed receipts
including context days, and verifies original-table fingerprints, integrity/FKs
and quota through a consistent backup. Example options: `--from-db LEGACY.db
--source-root ROOT --policy-db SERVING.db --profiles-db SERVING.db --start DATE
--end DATE`. Keep the source/rollback; never reset an adopted destination on retry.

## Quota, diagnostics and admin access

`database_policy.quota_bytes` defaults to 20 GiB and covers each DB's own SQLite,
WAL and SHM, excluding raw/day staging. Producer `init --quota-bytes N` changes
private policy live; serving admin `storage quota` changes serving policy.
`--budget-gib` separately bounds working space (default/max 20 GiB), with a 1 GiB
filesystem reserve. Quota is rechecked during IO and before commit, including
pending growth. Lowering below usage preserves facts/reads and pauses ingestion;
there is no LRU deletion of the sole retained copy. Active readers/WAL can cause
transient physical overage rechecked on later IO.

JSON stderr events are `day_started`, `day_progress`, `day_finished`, `day_failed`.
Durable receipts and `adsblol status` count exclusions, invalid samples, gap durations,
endpoint/callsign/duration rejections, midnight deferrals, boundary joins, updates
and duplicates. Failed batches keep diagnostics without advancing coverage.
`--refresh-diagnostics` reprocesses selected inputs and needs raw/redownloads.
Counters describe aircraft/samples/fragments, not missing-flight percentages.

Private admin supports read/query/export and `maintenance clear-data --yes` or
`maintenance reset --yes`, never producer launches, candidate edits/approvals or
quota updates. Clear retains profiles/quota; reset also clears profiles/notices
and restores 20 GiB. Both refuse active jobs, retain DB/schema identity, increment
generation, clear receipts/evidence and leave serving untouched without unlinking
a live SQLite family. Reads can use `inspect query` or `ingest adsblol status`.

Python producers use `IngestionStore`/`IngestionService`. `document FILE.json`
stores normalized evidence keyed by `(source,kind,key)`, with URL/licence, validity,
content hash and revision. Payloads are limited to 64 KiB; no raw trace blobs.
Documents never become flight facts without a validated adapter/publication policy.
No public ingestion HTTP endpoint is exposed.


ADSB aircraft traces have no fixed compressed or decompressed byte-size cap.
The archive reader opens one aircraft member at a time directly through gzip,
then parses its JSON; it never extracts tar member paths to disk. A complete
trace must fit in available process memory. Corrupt gzip, malformed JSON and
invalid member shapes still fail the day before its atomic commit. Database
quotas and the raw-workspace budget remain separate limits.


Serving publication reclaims its WAL before starting a publication and after its
canonical read attachments close. This prevents a read-only connection closing
last from retaining an ever-growing journal. Reclamation uses a SQLite TRUNCATE
checkpoint with a one-second busy timeout; an active reader or writer pauses the
producer with an actionable error. A committed serving receipt remains durable
if reclamation is interrupted, and private copies are released only after the
post-publication checkpoint succeeds. Retrying reuses the normal receipt and
identity checks. The persisted DB+WAL+SHM quota is unchanged; do not remove WAL
files manually. A leftover journal is reclaimed before the next quota gate.
