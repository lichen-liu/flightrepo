# Ingestion and serving databases

**Implemented contract: `flight-legs`, schema version 1.** The canonical
[SQLite DDL](../src/core/schema.sql) and [storage adapter](../src/core/storage.py)
define the flight/airport contract in both stores. Pipeline state belongs only
to the private [ingestion schema](../src/ingestion/schema.sql). Startup does not seed real databases.
SQLite 3.38 or newer is required (STRICT tables and built-in JSON).

## Start with the consumer

The ingestion database collects candidates and normalized reference evidence.
The serving database holds explicitly published flight facts and airport metadata.
They share a typed contract, so publication preserves aircraft type and time
semantics without making observations look like schedules. Their stored roles
prevent an ingestion file from being used by the backend or a manual writer.
Admin can inspect either file, but only clear/reset ingestion, never manually edit it.

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
| `ingestion_batches` | Private input attempts/diagnostics; common FK structure exists in serving but publication leaves it empty |
| `flight_tombstones` | Durable deletion intent across repeated imports/downloads |
| `schema_metadata` | Versioned contract for startup checks |
| `database_policy` | Persisted live database disk quota |
| `ingestion_profiles` (private) | Configured source/input paths for repeatable producer jobs |
| `ingestion_store_metadata` (private) | Role/version, immutable store identity and reset generation |
| `ingestion_vrs_snapshot`, `ingestion_vrs_airports`, `ingestion_vrs_routes` (private) | Atomic CC0 reference receipt, explicit airport mappings and indexed callsign chains; no admission writes |
| `ingestion_documents` (private) | Source/kind/key, validity, normalized JSON, content hash and revision |
| `ingestion_adoptions` (private) | Verified legacy-copy fingerprints and inherited quota provenance |
| `ingestion_audit_runs`, `ingestion_audit_changes` (private) | Scoped machine audit/correction receipts and per-record before/after evidence |
| `publication_origin`, `publication_membership`, `publication_runs` (serving) | Publisher identity, owned source keys and per-day publication receipts |
| `searchable_legs` (view) | Common departure/arrival intervals, filtered by explicit time basis |

Extraction receipts, algorithm/config and overnight boundary summaries stay in
ingestion. The `flights` view projects typed facts in both stores;
`itinerary_legs` is an extractor-only compatibility view. Serving has no private
profiles, reference documents, extraction receipts or boundary summaries.
Boundary summaries contain compact airport/time evidence, not tracks. They remain
after raw deletion to support disjoint backfills.

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
The approved labeled admission policy makes all four known uncertainty categories
searchable. `blocked_for_search`/SQL admission remain separate from confidence-based
`needs_review` and `quality.review_recommended`. Unknown future reasons fail closed;
cancelled/deleted records and incomplete/invalid time evidence still cannot power
routing/calendar. API, SQL and pure snapshot routing share the policy. See
[unified confidence labels](itinerary-quality.md). Explicit human approval sets
`itinerary_reviewed`, preserves reasons/conflict warnings and protects the record
against passive reingestion. Private admin can inspect all candidates without
changing them; confidence labels do not expose private reference tables or journals.

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

Private ingestion overview separates stored leg count, searchable count by basis, completed input
days, excluded/rejected units, unresolved boundaries, missing metadata and last
successful ingestion. Refresh time is distinct from flight date. Coverage is
partial/unknown unless measured against a trusted reference; receipts alone do
not establish completeness. A no-data response differs from a failed ingestion attempt.

Use SQLite WAL and short read snapshots. Parsing/downloading takes place outside
the write transaction; one day's leg upserts, airport stubs, boundary changes,
receipt and committed batch state become visible atomically. Register the attempt
first; persist failure diagnostics after rollback, with zero applied rows.
Private readers keep accessing previously committed days during downloads and commits.
Public readers see the latest published days, independent of ingestion progress.
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
`--budget-gib` working-space limit, even inside the same directory. Producer
`init --quota-bytes N` changes private policy live; serving admin `storage quota`
changes serving policy. Ingestion checks it before downloading,
during parsing and before committing pending page growth. Reads remain available
if policy is lowered below usage, and new ingestion pauses. Extracted legs,
manual edits, tombstones, receipts and boundary state are protected: no LRU or
background deletion can erase the sole copy. SQLite WAL/readers can cause a
transient physical overage; later IO rechecks it. Noncontiguous ingestion remains
supported without retaining annual raw archives or per-flight position files.

## Initialization and serving

Initialize ingestion using `python -m ingestion --db var/ingestion/ingestion.db init`.
`FLIGHT_INGESTION_DATABASE_PATH` supplies its independent default. The producer
CLI and Python API reject an existing serving database before fetching source
records. `--data-root` is disposable working storage; `--db` selects the only
pipeline output. A differing `--output-db` cannot redirect writes.

Initialize serving with `python -m admin --db var/hosted/flights.db db --create`
or let an explicit publication initialize a new serving target. `FLIGHT_DATABASE_PATH`
continues to select serving/admin. The public backend and hosted deployment
checks reject ingestion-role files, including renamed files. No ingestion HTTP
write endpoint is exposed. Real serving never seeds demo data.

Admin reads private stores using read-only SQLite connections: overview, flights,
lookup, airports, exports, source status and bounded SELECT inspection. Record
edits/imports, manual approvals, source job launches, schema/admission changes and
quota updates are rejected. Clear/reset are explicit lifecycle exceptions and
refuse held producer/extractor locks. They delete transactionally without
unlinking a SQLite/WAL family; existing serving facts remain untouched.

## Publication

`python -m ingestion --db INGESTION.db publish --serving-db SERVING.db --start YYYY-MM-DD --end YYYY-MM-DD`
is the only producer command that writes serving. Inclusive UTC days publish
independently using an ingestion read snapshot and a short serving transaction.
Both files remain available to readers. Quota failure rolls back the day's facts.
SQLite/SHM/WAL usage is checked, excluding source scratch space.

Publish admitted facts, retaining their confidence labels, source keys, available
aircraft types and all reported times. Partial/cancelled facts remain useful to
lookup/admin; normal routing/calendar eligibility still applies. Partition by the
selected departure basis, falling back to available arrival evidence for partial
records. Copy airport/transfer metadata and relevant source attribution, preserving
existing serving metadata and valid transfers. Reference-only source documents,
unknown-reason exclusions, profiles, receipts and boundary state never publish.

Membership records allow a rerun to retract previously published facts that
became excluded or disappeared, even if a correction moved their time outside the
original day. Reconcile only requested dates. Stable provider keys deduplicate
retries. Serving edits and deletion tombstones take precedence; publication does
not resurrect deletions. Prior explicit human approvals remain protected.
One serving file accepts one ingestion store identity, preventing competing
publishers from deleting each other's records. Clearing/resetting ingestion
increments generation without changing identity; clearing itself never changes
serving. A later explicit publication can reconcile its affected days.

The production ingestor already running in a frozen older runtime may finish
without interruption. Its file is not a clean serving replacement under this
architecture. Preserve it and its explicit human decisions; validate/import into
a dedicated ingestion store before publishing a clean serving replacement.
Never rename/copy the entire ingestion SQLite file over serving. Use the explicit, tested `adopt` handoff described in the
[adoption/audit workflow](ingestion-review.md). No production adoption,
publication or serving cutover occurs automatically.

API routing still supports `scheduled`, `actual` and `observed` modes. Calendars
use the same eligibility predicate; lookup/export retain incomplete facts. The
frontend preserves inferred airport/time labels and aircraft type. Existing
serving admin creation/edit/delete/approval and airport management remain intact.

`tests/test_ingestion_store.py` exercises role guards, private read-only admin,
clear/reset safety, evidence deduplication, publication/retraction, edit/delete
protection and quota rollback. `tests/test_shared_flight_database.py` exercises
extraction through publication to API/calendar/routing/rebuild/replay/export.

VRS admission adds nullable `flight_legs.route_verification`: a typed JSON assertion
binding a pinned reference hash to a callsign and directed airport pair, with
positive, different, incomplete or missing outcomes and the mapped airport chain. It is
separate from `itinerary_reviewed` and never removes original review reasons.
Explicit writers upgrade old stores additively; read-only consumers do not migrate.
A matching assertion changes confidence labels, not time evidence or human approval.
All four known uncertainty categories now qualify for labeled search; unknown reasons
remain blocking. See [VRS policy](vrs-verification.md#explicit-best-effort-route-admission).
Review decisions/acknowledgements are private `ingestion_documents` of kind
`validation`, source `review`; revision-bound fingerprints and before/after facts
are journaled atomically. They never enter the serving database.


## Shared airport and operator reference catalog

The **main serving SQLite** owns `airports` and `carriers`; API requests need no
CSV, private ingestion file or external lookup. Both dimensions also exist in the
private store so producer imports can be validated before explicit publication.
`reference_snapshots` stores the source URL, SHA-256, fetch time and accepted row
count, with license/attribution in `data_sources`.

Airports retain name, ICAO, municipality, ISO country/region and coordinates from
OurAirports. Municipality is descriptive geography, **not** a transfer `city_id`.
The source has no timezone column; imports leave unknown timezones NULL and keep
existing IANA zones and explicit transfer groups. Duplicate IATA assignments are
counted/skipped rather than chosen arbitrarily. Automated publication fills NULL
fields even when coordinates already exist, preserving all populated admin fields.

`carriers(code,name,icao,iata,metadata_source_id,metadata_updated_us)` stores VRS
standing-data airlines (CC0). ICAO is unique; IATA is deliberately non-unique.
Source-owned carrier snapshots refresh atomically and withdraw stale assignments.
The `flights` view resolves a normalized 4–8 character callsign only when it begins
with a known three-letter ICAO operator followed by a digit. It exposes
`operator_icao` and `operator_name`; the API and frontend retain the callsign and
label the name as **operator**. These are current undated reference matches,
not historical operator verification, marketing-carrier claims, or route/timing
approvals. Unknown/private/ambiguous callsigns stay unresolved. No guessed flight
number or airline IATA is written to a flight, and no name is duplicated across
millions of flight rows. Reference-only backfills never change flight revisions,
manual approvals, tombstones, timing, aircraft types or search admission.

Explicit writers add the optional reference extension and refresh the compatibility
view transactionally. Read-only backend/admin processes never migrate a database.
