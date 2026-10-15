# Shared flight database

**Implemented contract: `flight-legs`, schema version 1.** The canonical
[SQLite DDL](../src/core/schema.sql) and [storage adapter](../src/core/storage.py)
are shared by ingestion, API and admin. Startup does not seed real databases.
SQLite 3.38 or newer is required (STRICT tables and built-in JSON).

## Start with the consumer

Ingestion, backend, calendar, routing and admin must share one flight-leg
contract. A committed leg must be browseable and, when its evidence is eligible, searchable
without a separate export or pretending that an observation is a schedule. The previous split between
`flights` and `itinerary_legs` failed this requirement, even in the same file.

Store one leg per source identity. Build connecting journeys at query time.
Keep aircraft type, airport pair, identifiers, time evidence, status and source
provenance. Discard traces, positions and speeds. Missing information is NULL;
never synthesize a flight number, schedule, airport timezone or city membership.

## Tables and retained state

| Table | Purpose |
| --- | --- |
| `flight_legs` | Shared, typed leg facts; source identity; aircraft type; separate time evidence; edit revision |
| `flight_codeshares` | Zero or more source-reported marketing designators for a leg |
| `airports` | IATA code and nullable descriptive/location/timezone metadata |
| `airport_transfers` | Explicit, directed transfers between different airports |
| `data_sources` | Provider codes, attribution and licence references |
| `ingestion_batches` | Input window, pipeline/config identity, input URLs/hashes, commit counts and diagnostics per attempt |
| `flight_tombstones` | Durable deletion intent across repeated imports/downloads |
| `schema_metadata` | Versioned contract for startup checks |
| `database_policy` | Persisted live database disk quota |
| `ingestion_profiles` | Configured source/input paths for repeatable admin jobs |
| `searchable_legs` (view) | Common departure/arrival intervals, filtered by explicit time basis |

The DDL covers the shared facts, quota and admin profiles. The extractor also
retains config/day receipts/boundary state for resumability. Compatibility `flights` and `itinerary_legs`
views project the same typed facts; they are not separate flight stores. Boundary summaries contain compact airport/time evidence,
not tracks; successful receipts remain available after raw deletion. A later
cleanup can retire redundant metadata only after compatibility is demonstrated.

## Identity, updates and truthfulness

`UNIQUE(source_id, source_record_key)` makes overlapping imports idempotent.
Preserve existing provider IDs and ADSB.lol's deterministic 32-character leg key.
An integer internal ID keeps codeshare references small; the external API
continues to use provider/key, so exports and saved references remain stable.

An identical reimport records a duplicate, without increasing the revision.
A trustworthy upstream correction updates the same identity and increments its
revision. An unexpected conflicting ADSB extraction identity fails its day's
transaction and reports the conflict. `locally_modified_us` protects manual
overrides from subsequent ingestion. Edits compare reviewed facts under an
immediate transaction, and the stored revision tracks changes.
Delete writes a tombstone in the same transaction as removing the leg. Only an
explicit restore clears it. The shared flight view hides tombstoned identities.

Deduplication is guaranteed **within a source identity**. Callsign plus airport
pair plus day is insufficient to merge different providers or repeated daily
flights. Cross-provider matching requires a separately validated policy; until
then report counts as source leg records, not unique physical flights.

`flight_number`/`airline_iata` hold source-reported commercial identifiers.
ADSB callsigns go in `callsign`; they do not populate either commercial field.
`aircraft_type_code` is the observed/reported ICAO type designator, not a
registration or seat configuration. An ADSB candidate has
`evidence_kind = inferred_ground_brackets` or `inferred_airport_proximity`, and
`status = unknown`. Proximity candidates use estimated event windows and inferred
airport endpoints; neither evidence class certifies commercial service or an
official landed status. Inference policy belongs in batch provenance, not a
fabricated fact. `review_reasons` retains per-record uncertainty (airborne airport
estimate, observation gap, implausible airport timing, legacy callsign variation).
Airport-estimate-only records are admitted with visible estimate labels. Other
reasons block `searchable_legs`, API routing, calendar availability and pure
snapshot routing unless explicitly approved. `needs_review` means held by those
blocking reasons, not every record containing an estimate. The model and SQL
admission predicates follow the same rule. Explicit admin approval sets
`itinerary_reviewed`, preserves the reasons/evidence, and protects the record
against passive reingestion. Catalog counts distinguish all stored records,
searchable legs and pending reviews.

## Time model and routing

All instants use integer Unix microseconds in UTC, preserving current precision.
SQLite checks chronological order; the application validates timezone names,
airport/code normalization and the meaning of each provider's timestamps.

| Evidence | Stored values | Meaning |
| --- | --- | --- |
| Scheduled | Nullable departure/arrival pair | A provider's published schedule; can include future dates |
| Actual | Nullable departure/arrival pair | Provider-reported events; event semantics must be documented by the adapter |
| Observed | Four bounds: departure lower/upper, arrival lower/upper | Ground/air transitions or labelled proximity estimates; neither schedules nor gate times |

Actual events also record `gate_out`/`takeoff` and `gate_in`/`landing`, or explicit
`unknown` kinds. Routing must expose these semantics; a runway arrival cannot be
silently treated as arrival at a passenger gate.

Partial schedule/actual data is retained for lookup but becomes searchable only
when both endpoints exist. Observed bounds are either complete or absent.
A leg can have multiple kinds of evidence. Queries select **one** basis
(`scheduled`, `actual`, or `observed`); they never count all view rows as flights
or silently fill missing actual times with schedule times. The API returns the
selected intervals plus available evidence, callsign/flight number, aircraft
type, provenance and quality. The frontend labels inferred times and coverage.
The current ADSB dataset therefore powers historical **observed** mode only.

For a minimum connection `m`, require
`next.departure_lower - previous.arrival_upper >= m`. If maximum connection `M`
is requested, also require
`next.departure_upper - previous.arrival_lower <= M`. Apply airport-transfer
time explicitly. Require the first departure interval to be wholly inside the
requested window and the final arrival upper bound inside the horizon. Return
duration bounds and use a documented conservative ranking (latest arrival for
earliest-arrival ranking; maximum duration for shortest-elapsed ranking).
Rebuild, replay and map responses use the same versioned interval contract.
These are temporal possibilities; historical observations do not establish
ticket availability or passenger connection feasibility.

## Calendar, coverage and concurrent access

The calendar uses eligible `searchable_legs` for its selected basis and origin,
including local overrides and deletion markers. A completed archive day with no
accepted legs is not a selectable departure date. Airport-local dates require a
known IANA timezone; otherwise explicitly offer UTC dates. For a departure
interval crossing local midnight, report the ambiguous days in coverage without
enabling an exact-day route option that has no guaranteed departures. A broader
window spanning both days can still find the leg. Browsing may use interval
overlap, labelled explicitly; the route calendar uses the strict search predicate.
Compute day boundaries using IANA rules, including daylight-saving changes.

Overview separates stored leg count, searchable count by basis, completed input
days, excluded/rejected units, unresolved boundaries, missing metadata and last
successful ingestion. Refresh time is distinct from flight date. Coverage is
partial/unknown unless measured against a trusted reference; receipts alone do
not establish completeness. A no-data response differs from a failed ingestion attempt.

Use SQLite WAL and short read snapshots. Parsing/downloading takes place outside
the write transaction; one day's leg upserts, airport stubs, boundary changes,
receipt and committed batch state become visible atomically. Register the attempt
first; persist failure diagnostics after rollback, with zero applied rows.
Readers keep accessing previously committed days during downloads and commits.
API responses use one consistent snapshot, not multiple unrelated connections.

## Space and quota

Typed columns replace per-flight JSON. Source names/licences are stored once;
empty time fields cost only SQLite record-header space. Keep JSON for small
variable diagnostics/configuration, not searchable leg payloads. Two partial
indexes per time basis cover origin/time routing and global date windows; an
observed-only dataset populates only two of those six indexes. The primary source
identity index is needed for deduplication and lookup. They serve local queries;
no downloadable itinerary catalogue is required.

A compacted typed-fact size probe measured about 244 bytes/leg, including source
identity and two observed-time indexes, versus 497 bytes/leg with per-leg JSON.
This excludes receipts, boundary state, airports, diagnostics and WAL, so it is
not a whole-database capacity claim. Scheduled/actual-rich records can be larger.

The 20 GiB quota is stored in SQLite (`database_policy.quota_bytes`) and covers
only the database and WAL/SHM. Raw daily inputs and staging have an independent
`--budget-gib` working-space limit, even inside the same directory. Admin's
`storage quota` changes policy live; ingestion checks it before downloading,
during parsing and before committing pending page growth. Reads remain available
if policy is lowered below usage, and new ingestion pauses. Extracted legs,
manual edits, tombstones, receipts and boundary state are protected: no LRU or
background deletion can erase the sole copy. SQLite WAL/readers can cause a
transient physical overage; later IO rechecks it. Noncontiguous ingestion remains
supported without retaining annual raw archives or per-flight position files.

## Initialization and serving

Initialize a fresh target explicitly with `python -m admin --db PATH db --create`.
The admin ADSB pipeline defaults to the shell's selected database; `--output-db`
remains an explicit override. Scratch directories hold temporary inputs/staging,
not a second serving database. Noncontiguous jobs and overlapping days retain
stable keys and boundary state. Legacy schemas require a new initialized file;
there is no automatic destructive conversion or reseeding.

For a clean reingest, stop the ingestion worker and serving processes before
replacing the target with a freshly initialized database. Do not copy old flight
facts, tombstones, batches, day receipts, configuration or boundary summaries:
receipts would otherwise skip days and boundary state could join old evidence.
Initialize the shared runtime and extraction schema, persist the chosen quota,
verify integrity/foreign keys and zero data counts before restarting. The archive
manifest, airport CSV and filter are
inputs, not database state, and may be reused. Point every consumer at the same
target. Clear aborted raw/staging files, then restart the requested range from
its first day; each day's fresh commit becomes visible immediately.

Hosted settings accept `data_mode: real`. This validates the shared schema and
rejects demo flight records, allows an initialized empty dataset, and never seeds
it. The default `synthetic` mode keeps the isolated demo's existing checks. Install
backend, frontend and admin together, and point serving and ingestion at the same
absolute database path. Readiness and admin `overview --json` expose the schema contract and
version; admin overview also reports searchable counts by basis and batch states.

API routing accepts `time_basis: scheduled | actual | observed`. An omitted basis
selects available scheduled evidence first, then observed, then actual. Calendars
use the same preference. Lookup and JSON export retain every available kind of
evidence, including partial facts that cannot yet participate in routing. The
frontend offers the three modes, labels callsigns and inferred intervals, selects
UTC explicitly when no timezone metadata exists, and shows source/licence notices.

Raw parsing writes disposable daily staging outside the main database's write
transaction. Admin quota changes can commit while this parser runs. Only the
candidate/boundary merge, receipt and successful batch state acquire the main
write lock. Readers see prior complete days until that commit. Failed attempts
retain diagnostics and raw retry inputs, remove staging, and apply no partial
legs. Lowering quota during parsing is rechecked before committing the day.

`tests/test_shared_flight_database.py` exercises the complete daily handoff,
API/calendar/routing/rebuild/replay/export/admin, edit/delete protection, failed-day
receipts, partial records, interval connection
limits and a separate admin writer changing quota during parsing. Scheduled
routing, interactive admin, package and browser regression suites cover the
existing workflows.
