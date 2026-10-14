# Shared flight database proposal

**Status: design, not deployed.** [Executable SQLite DDL](database-schema.sql)
defines the proposed serving tables. It has no effect on the running ingestion
or website. The hosted database remains `var/hosted/flights.db`.
The proposal requires SQLite 3.38 or newer (STRICT tables and built-in JSON).

## Start with the consumer

Ingestion, backend, calendar, routing and admin must share one flight-leg
contract. A committed leg must be browseable and searchable without a separate
export or pretending that an observation is a schedule. The current split between
`flights` and `itinerary_legs` fails this requirement, even in the same file.

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
| `schema_metadata` | Versioned contract for startup and migration checks |
| `searchable_legs` (view) | Common departure/arrival intervals, filtered by explicit time basis |

The DDL covers the shared facts. Retain existing `ingestion_profiles`, extraction
config/day receipts/boundary state, and cache policy/catalogue/LRU/locator tables
during migration. They support resumability and storage, rather than defining a
second kind of flight. Boundary summaries contain compact airport/time evidence,
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
overrides from subsequent ingestion; edits use compare-and-swap on `revision`.
Delete writes a tombstone in the same transaction as removing the leg. Only an
explicit restore clears it. The view also hides any tombstoned cached row.

Deduplication is guaranteed **within a source identity**. Callsign plus airport
pair plus day is insufficient to merge different providers or repeated daily
flights. Cross-provider matching requires a separately validated policy; until
then report counts as source leg records, not unique physical flights.

`flight_number`/`airline_iata` hold source-reported commercial identifiers.
ADSB callsigns go in `callsign`; they do not populate either commercial field.
`aircraft_type_code` is the observed/reported ICAO type designator, not a
registration or seat configuration. An ADSB candidate has
`evidence_kind = inferred_ground_brackets` and `status = unknown`; detecting
ground brackets does not certify a commercial service or an official landed
status. Filter membership belongs in batch provenance, not a fabricated fact.

## Time model and routing

All instants use integer Unix microseconds in UTC, preserving current precision.
SQLite checks chronological order; the application validates timezone names,
airport/code normalization and the meaning of each provider's timestamps.

| Evidence | Stored values | Meaning |
| --- | --- | --- |
| Scheduled | Nullable departure/arrival pair | A provider's published schedule; can include future dates |
| Actual | Nullable departure/arrival pair | Provider-reported events; event semantics must be documented by the adapter |
| Observed | Four bounds: departure lower/upper, arrival lower/upper | Inferred ground/air transitions; neither scheduled nor gate times |

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
not establish completeness. A no-data response differs from download failure.

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
identity index is needed for deduplication and lookup. These SQL indexes are
distinct from a downloadable catalogue of URLs.

The [disposable validation report](database-schema-validation.json) copied
**1,324 already extracted legs** without changing identity or time precision.
Repeating their field distribution with fresh keys produced a 132,400-row size
probe: **244 bytes/leg** for the proposed typed table, identity index and two
observed-time query indexes, versus **497 bytes/leg** for the existing JSON table
and identity index. Both databases were compacted before measurement. This is a
storage experiment, not additional real flights or a complete ingestion budget;
receipts, boundaries, airports, backups, WAL and daily working inputs cost extra.
Scheduled/actual-rich records will also be larger.

Constraint probes rejected duplicate identities, invalid endpoints/types/time
bounds and missing actual-event semantics. Query plans used the intended
origin/window and global-window indexes; a two-connection WAL probe verified
old-snapshot visibility during a write and new records after the read restarted.

The 20 GiB quota includes database/WAL, disposable daily inputs and cached chunks.
Keep the existing live admin policy and next-IO LRU enforcement. Retire only
recoverable downloads. Extracted legs, edits, tombstones, receipts and boundary
state are durable; raw upstream archives are not a cheap per-leg fetch service.
If protected facts exceed a lowered quota, expose the excess and refuse new
downloads/writes that need space while continuing reads. Do not erase the only
copy of itinerary data. Noncontiguous dates remain supported; no annual raw
archive or per-flight position cache is needed.

For an optional normalized HTTP source, cached chunks must implement this same
versioned leg contract. Shared reader methods merge durable rows and downloaded
rows, respecting local overrides and tombstones, for **every** consumer. Keep
simple chunk URL/hash/window metadata; ADSB.lol raw archive URLs do not supply
on-demand itineraries.

## Migration and release sequence

1. Validate this design in a disposable DB. Implement the new models, writer and
   shared reader together, then route catalog/stats/dates/lookup/search/rebuild/
   exports/admin through them. Upgrade normalized cache format and saved routing
   snapshots explicitly; do not reinterpret version-1 scheduled fields.
2. Prepare a versioned, transactional migration and verify it against a consistent
   SQLite backup of the real DB, using the backup API rather than copying a main
   file with outstanding WAL changes. Copy `flights` as source-reported schedule/actual facts;
   copy `itinerary_legs` as observed candidates, retaining their keys and all four
   bounds. Add airport stubs, then enrich from the saved airport snapshot without
   invented timezones. Preserve metadata/state/tombstones; validate row counts,
   identities and exact UTC microseconds. Keep old tables through validation,
   then retire them to release pages at a planned maintenance boundary.
   Invalid legacy records fail migration with diagnostics rather than disappearing.
3. Before the live migration, finish the current day and pause the existing
   ingestor. Its current empty-`flights` guard must be replaced by version/source
   checks before resuming. Budget backup/migration space explicitly. Do not
   change schemas beneath an active old writer.
4. Install the coordinated backend/frontend/admin update, with an explicit
   database path shared by ingestion and serving. Replace the deployment's
   demo-only validation with schema/version/readiness checks. Demo seeding is an
   explicit isolated operation, never a fallback for empty production data.
5. Resume daily ingestion. A new day's commit must change API counts and eligible
   dates immediately; test historical search, connections, bounds/type display,
   exports, overlapping imports, manual edits/deletes, quota changes and
   concurrent readers against this exact file. Compare database schema version,
   resolved path and counts through admin and the deployed API.

This design does not make the current website serve extracted records. That
requires the coordinated implementation and deployment above, not just DDL.
