# Ingestion, serving and public API contracts

**SQLite contract: `flight-legs`, version 1.** The authoritative
[shared DDL](../src/core/schema.sql), [private ingestion DDL](../src/ingestion/schema.sql)
and [storage adapter](../src/core/storage.py) define the implementation. SQLite
3.38+ is required for STRICT tables and built-in JSON. Read-only startup does not
initialize, migrate or seed real databases.

## Store boundaries

Private ingestion collects candidates, reference evidence and pipeline state.
Explicit publication copies flight facts and shared airport/operator dimensions
into serving. The stored ingestion role prevents using a renamed private file as
serving or opening it through manual serving writers.

Both stores use the same typed leg contract. Connecting journeys are computed at
query time. Keep airport pair, identifiers, aircraft type, status, time semantics
and provenance; discard raw tracks/positions/speeds. Missing facts are NULL, not
guessed flight numbers, schedules, timezones or transfer-group membership.

| Table/view | Purpose |
| --- | --- |
| `flight_legs` | Source identity, aircraft/airport/timing facts, evidence, revision and local edit state |
| `flight_codeshares` | Ordered source-reported marketing designators |
| `airports` | IATA identity and nullable names, ICAO, geography, coordinates, timezone and transfer group |
| `carriers` | Normalized operator reference; unique ICAO, deliberately non-unique IATA |
| `airport_transfers` | Explicit directed allowances between distinct airports |
| `data_sources`, `reference_snapshots` | Attribution/licence and shared reference URL/hash/fetch/count provenance |
| `flight_tombstones` | Deletion intent that survives passive reingestion |
| `schema_metadata`, `database_policy` | Contract and persisted DB-family quota |
| `flights` | Backend/admin compatibility projection of typed facts |
| `searchable_legs` | Shared admission and selected departure/arrival intervals |
| `ingestion_batches` | Input attempts/diagnostics; common FK structure exists in serving but publication leaves it empty |
| `ingestion_store_metadata`, `ingestion_profiles` (private) | Store identity/reset generation and producer configuration |
| `ingestion_documents` (private) | Normalized reference/review documents, validity, hash and revision |
| `ingestion_vrs_snapshot`, `ingestion_vrs_airports`, `ingestion_vrs_routes` (private) | Atomic route snapshot, explicit mappings and indexed callsign chains |
| `ingestion_adoptions`, `ingestion_audit_runs`, `ingestion_audit_changes` (private) | Verified handoff and correction receipts/journals |
| `publication_origin`, `publication_membership`, `publication_runs` (serving) | Owning publisher, source keys and per-day reconciliation receipts |

Extraction receipts, policy/configuration and compact overnight boundaries remain
private after raw deletion so adjacent and disjoint backfills can refine legs.
`itinerary_legs` is an extractor-only compatibility view. Private documents,
profiles, extraction receipts and boundaries never enter serving.

## Identity, updates and concurrency

`UNIQUE(source_id,source_record_key)` makes overlapping ingestion idempotent.
ADSB.lol's deterministic leg key uses the aircraft's final airborne arrival
observation; a departure backfill refines that record rather than making another
flight. More complete evidence may replace an estimate; weaker input cannot erase
stronger context. Identical inputs count as duplicates, not revisions.

Local edits, explicit approvals and deletion tombstones take precedence over
passive reingestion/publication. Do not deduplicate physical flights using only
callsign, airport pair or date: repeated services, marketing codeshares and
provider disagreements require separately validated matching. Suspected duplicate
signatures are review hints, not automatic merges.

WAL permits concurrent readers and a short writer transaction. Download/parsing
happen outside that transaction; one day's facts, airport stubs, boundaries, receipt
and committed batch become visible atomically. Failures apply zero rows and retain
diagnostics. Calculations use consistent read snapshots. Private readers see
committed days; public readers see published days, independently of active ingestion.

Each DB owns its persisted quota (default 20 GiB) over SQLite/WAL/SHM, excluding raw
and daily staging. Lowering below usage preserves reads/facts and pauses ingestion
at later IO; no LRU erases the sole retained copy. Pending page growth is checked;
WAL/readers may temporarily affect physical use. See [storage and recovery](ingestion.md#storage-placement-and-ram-backed-workspace).

## Time evidence and routing

All stored `*_us` timestamps are signed integer UTC microseconds. Precision is
preserved through SQLite projections and JSON serialization.

| Basis | Fields/meaning |
| --- | --- |
| `scheduled` | Nullable departure/arrival pair; published schedule, not an actual event |
| `actual` | Nullable reported pair; event kinds distinguish gate-out/takeoff and gate-in/landing, or unknown |
| `observed` | Four ordered bounds: departure lower/upper and arrival lower/upper; observation-based estimates |

Scheduled/actual partial facts remain available for lookup but require both ends
for routing. Observed bounds must be complete and ordered. A leg may retain several
bases; a calculation selects one without mixing schedules into missing actuals.
The current ADSB archive powers historical **observed** mode, not future schedules.

For connection minimum `m`, require
`next.departure_lower - previous.arrival_upper >= m`; for maximum `M`, require
`next.departure_upper - previous.arrival_lower <= M`. Add explicit airport-transfer
time. The full first departure interval must fit the departure window, and the
final arrival upper bound must fit the horizon. Rank conservatively using latest
arrival/maximum elapsed duration. A runway event cannot imply passenger gate
availability; historical timing does not establish ticket or protected connections.

Calendar, routing and continuation use the same admission/time-basis rules. Local
calendar days require both departure bounds on that date in the airport's IANA
zone; UTC mode remains available without timezone metadata. Actual event kinds and
time ranges remain public itinerary semantics, not source provenance.

## Confidence and search admission

Admission is separate from accuracy and human approval. The four known concerns
are searchable: airport estimates, observation gaps, callsign variation and
implausible timing. Unknown reasons fail closed; cancellation, tombstones and
unusable time evidence still exclude records from routing/calendar.

Internal levels are `standard`, `noted`, `uncertain`, `conflict`. Original concerns,
evidence classes, source/reference identity and explicit review decisions remain
backend facts. A positive VRS match can reduce an airport/gap concern but cannot
verify historical operation, exact timing or continuity. Callsign/airport changes
invalidate old matches. Different/missing undated associations do not disprove a
past operation. Human approval never hides a genuine timing conflict.

Public summaries are only **Uncertain itinerary**, **Timing conflict** and
**Approximate times**. Admin/producer retain detailed evidence and confidence-based
review recommendations; review counts do not mean excluded from search. Cohort
acknowledgement is not bulk approval. See [audit and review commands](ingestion.md#audit-review-and-publication).

## Shared airport and operator reference catalog

Both stores own `airports`, `carriers` and snapshot provenance. Serving requests
need neither private ingestion nor external CSV/API access. OurAirports imports
names, ICAO, municipality, country/region and coordinates. Municipality is not a
transfer `city_id`. Duplicate IATA assignments are counted/skipped. The source has
no timezone column; unknown zones remain NULL and existing IANA zones/manual
metadata are preserved. Timezone enrichment is tracked in
[#21](https://github.com/lichen-liu/flightrepo/issues/21).

Carrier snapshots replace source-owned assignments atomically. A 4–8 character
callsign resolves an operator only when it starts with a known three-letter ICAO
code followed by a digit. The API shows the current undated operator name beside
the unchanged callsign. It does not invent a marketing number or airline IATA,
or claim historical operator/type/route verification. Unknown/private/ambiguous
callsigns remain unresolved. Names are not duplicated across millions of rows.
Reference-only publication fills missing airport metadata and updates operators
without changing flight revisions, times, types, decisions or admission.

## Initialization, admin and publication

`python -m ingestion --db INGESTION.db init` creates the private role;
`FLIGHT_INGESTION_DATABASE_PATH` supplies its default. `FLIGHT_DATABASE_PATH`
independently selects serving/admin. Producer guards reject serving destinations
before downloads. `--db` is the sole pipeline output; scratch paths cannot redirect it.

Admin can inspect/query/export either store. Only serving permits manual flight
and airport edits. Private producer launches, edits/imports/approvals and quota
changes are rejected by admin; clear/reset are explicit lifecycle exceptions that
refuse active locks and do not unlink live SQLite families or alter serving.

Flight `publish --serving-db PATH --start DATE --end DATE` copies an explicit range
one day at a time; `references publish` updates shared dimensions separately.
Partial/cancelled facts may remain available for lookup while routing filters apply.
Publication membership retracts missing/newly excluded owned facts, including
corrections moved across dates. Serving edits, approvals, tombstones and populated
airport metadata win. One serving file has one publisher identity; reset increments
private generation without changing that identity or serving immediately.

For a clean replacement, `--preserve-serving CANONICAL.db` copies canonical admin
state and quota, excluding old automatic flights. Drift in that state requires a
new replacement. Stop canonical writers, verify preservation again, checkpoint and
cut over only a validated **serving** replacement. Never copy/rename ingestion over
serving. Legacy `adopt` is an explicit verified copy into a new private file, not a
startup migration. [The operator guide](ingestion.md#audit-review-and-publication)
covers commands, reviews, recovery and cutover.

## Public API boundary

`server.public` allowlists responses for flight lookup, local/UTC search, legacy
itineraries and every nested rebuild option/tail. It retains flight/callsign,
operator, aircraft type, airport pair, status, available scheduled/actual times and
selected bounds. It excludes provider/key, refresh timestamps, original reasons,
evidence kind, reference results/chains/hashes, human-review status, connection
policy source descriptions and replay/trace details. Catalogs exclude source lists,
URLs, attribution objects and refresh metadata. Complete provenance stays in the
backend/admin; public confidentiality does not mean erasing internal auditability.

### Public identity and editing

Public `prefix` and `chosen_next` references use `{"flight_id":"flight_…"}` in
`POST /v1/plans/rebuild`. Provider/key pairs are rejected. Local admin, producer
APIs and replay retain their internal provider/key contract. **Release compatible
frontend/backend together**; this intentionally changes the public API contract.

IDs use an indexed row locator plus an opaque identity fingerprint. The check
prevents recycled row IDs after reset/replacement from silently selecting another
flight. They stay stable while the same identity occupies that row; they are not
authentication tokens. A full replacement may invalidate saved selections and
require a new search. No mapping table or DB migration is needed.

Public diagnostics retain completion/limits and route counts, excluding engine
versions, request/snapshot hashes and events. Public trace requests do not enable
trace capture. Errors allowlist necessary timezone/connection context and omit
internal flight identities. OpenAPI describes public contracts, not internal
`FlightRecord`, `RouteVerification` or replay models. Accuracy warnings remain
visible without source-specific evidence.

### Data sources, licences and exports

**Data sources & licences**, `/data-sources`, is the intentional public source
notice required by the data terms. It is rendered server-side from serving notices,
with escaped source names/homepages, attribution and licence links. Raw download
URLs, input paths, snapshot hashes and individual matches are excluded. The footer
links the notice, and API responses carry `Link: </data-sources>; rel="license"`.
This separates operational provenance; it does not promise unidentifiable origins.

Journey format `flyji-journey-2` exports only the public selection/query plus an
absolute `data_licence_notice` URL. Local admin exports stay internal and preserve
source identity. Moving the notice does not change redistribution rights.

Regression coverage includes nested responses, schemas, errors, conflicting
references, stale/tampered IDs, indexed/chunked resolution, notice escaping,
source-free exports, installed packaging and browser editing. The canonical DDL
and tests are authoritative for field-level details.
