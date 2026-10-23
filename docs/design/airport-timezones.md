# Airport timezone ingestion and calendar recovery

Design for [issue #21](https://github.com/lichen-liu/flightrepo/issues/21), revised
after the [pre-implementation audit](airport-timezones-audit.md). Rollout remains
an explicit operation after review; implementation does not backfill production.

## Problem and intended behavior

The October 23, 2026 investigation found 8,801 named airports in private ingestion
and zero populated timezones. Serving had the same catalog; its only two timezone
values were the subsequent YYZ/NRT manual repairs. OurAirports refresh and
publication had completed. The missing operation is timezone enrichment.

Airport-local calendar availability requires the origin's IANA timezone.
`search_availability()` rejects an unknown origin timezone, and the browser leaves
the date controls disabled. Destination timezone is not required for this date
query; it is needed for local destination display and explicit local arrival bounds.

After this change, ordinary reference ingestion populates resolvable airport zones,
explicit publication makes them available to serving, and existing airports can be
backfilled without flight archive downloads. An unresolved airport remains usable
through an explicit UTC clock choice.

## Source and resolution policy

Use offline coordinate lookup through `timezonefinder`, with its full,
comprehensive timezone-boundary-builder dataset. Pin timezonefinder 8.2.4, which
supports Python 3.11 and bundles its data. Verify each bundled data-file hash
against `src/ingestion/timezone-data.json`; record the upstream source artifact
hash as well. The newer split data-package layout is not available in this
compatible release. Exercise the adapter under the 3.13/3.14 CI runtimes.
The resolver is a producer dependency, imported lazily; the serving application
never needs polygon data or a lookup service to answer requests.

This choice uses the coordinates already retained for the airport catalog and
does not require matching a second airport catalog by ambiguous IATA codes.
The full dataset matters: the reduced "same since now" product can merge zones
with different historical behavior. The resolver documentation also identifies
overlapping polygons, so accepting its first returned zone is insufficient.
See [dataset variants](https://timezonefinder.readthedocs.io/en/latest/data_format.html)
and [lookup methods](https://timezonefinder.readthedocs.io/en/latest/1_usage.html).

The resolver adapter must enumerate actual containing polygons and return all
candidate zone identifiers. Accept one valid, distinct civil IANA identifier;
record multiple candidates as ambiguous. Reject missing/out-of-range coordinates,
an empty candidate set, ocean-only fixed-offset results such as `Etc/GMT+5`, and
identifiers unavailable to the supported server ZoneInfo database. Do not infer
a zone from longitude, country, nearest airport, or a fixed UTC offset. A valid
civil zone such as `Africa/Abidjan` is acceptable even when its offset is zero.

Record unresolved reasons separately: missing coordinates, no civil match,
ambiguous polygons, or invalid zone identifier. Dataset/package corruption is a
failed operation, not thousands of unresolved airports. Current boundary geometry
plus historical IANA offset rules is not a reconstruction of historical geographic
boundaries. Airports affected by historical boundary changes require a supported
manual mapping and documented evidence.

The resolver code is MIT; boundary data is ODbL. Retain the data license, source
attribution, pinned source identity and reproduction instructions, and distribute
the derived timezone mapping with its applicable data license. Keep timezone
provenance distinct from the OurAirports attribution. See the
[upstream license declaration](https://github.com/jannikmi/timezonefinder/blob/master/docs/3_about.rst)
and [boundary data project](https://github.com/evansiroky/timezone-boundary-builder#licenses).

## Storage and ownership

Continue using `airports.timezone` as the effective public value. Add two compact
reference tables to both stores through a writer-only additive migration:

| Table | Purpose and retained fields |
| --- | --- |
| `airport_timezone_snapshots` | Immutable dataset identity; resolver/algorithm version, bundled data variant, artifact/data-file hashes and source/license information. |
| `airport_timezone_state` | One row per airport; ownership (`reference`, `manual`, `legacy`), last applied value, exact input coordinates/digest, snapshot identity, resolution status, and candidate identifiers or unresolved reason. |

Dataset identity is content-derived, independent of which airports are present;
per-airport state records lookup inputs/results. Repeated identical imports do not append
duplicates or rewrite unchanged rows. Per-run counts/timing belong in operation
reports, not in a snapshot identity that changes on every invocation. Store
coordinates/results and snapshot identities in SQLite; retained raw CSV/GeoJSON
downloads are unnecessary. Installed compiled resolver assets are versioned
producer dependencies, not ingestion workspace downloads.

Do not reuse `airports.metadata_source_id` for timezone ownership: that single
field already attributes names and geography to OurAirports. The existing
`reference_snapshots.kind` constraint permits only airports/carriers; separate
timezone tables avoid changing that contract and accidentally making timezone
refreshes invalidate flight-audit fingerprints.

Treat every pre-existing non-NULL zone without timezone ownership as `legacy`,
which is protected like a manual override. This preserves the two live repairs.
All admin airport creation/import/edit paths must mark an explicitly supplied
timezone as manual, including an edit that pins the same value as a generated
zone. Omitting timezone from an update must not clear it. Preserve current
serving airport import/export compatibility and private-ingestion admin write
restrictions; producer enrichment is the supported private write operation.

A reference refresh may replace a generated zone only if ownership is still
`reference` and the current value equals the recorded last applied value.
A mismatch is a manual/external change and must be protected and reported.
Changed coordinates, resolver versions or data snapshots trigger re-resolution;
unchanged fingerprints reuse successful and unresolved results.

A successful new resolution can replace a previously generated value. A valid
new snapshot that makes that mapping unresolved clears only the still-owned
generated value and records the reason. A failed import leaves the previous
snapshot and values intact. Manual/legacy values are never cleared by enrichment.
Provide a producer-supported way to release a manual override back to reference
ownership; an explicit manual NULL can otherwise remain protected.

## Pipeline and commands

Add `ingestion/airport_timezones.py` for the resolver adapter, pure resolution,
validation, ownership-aware updates and coverage reporting. Keep database
orchestration in `ingestion/references.py`.

The proposed command surface is:

```sh
# Backfill using already stored coordinates; preview without writing.
flyji-ingest --db var/ingestion/ingestion.db references timezones --dry-run

# Apply the same deterministic operation to private reference metadata.
flyji-ingest --db var/ingestion/ingestion.db references timezones

# Existing explicit metadata publication, now including timezone state.
flyji-ingest --db var/ingestion/ingestion.db references publish \
  --serving-db var/hosted/flights.db
```

Both enrichment modes
return populated/unchanged/protected/unresolved/ambiguous/invalid counts, before
and after coverage, changed mappings and the reference identities. Dry-run uses
read-only access and requires an already initialized compatible database. It
does not create tables or receipts. Actual operations use normal producer locks,
quota enforcement and database-role checks.

Integrate enrichment into the shared airport import path so both
`references refresh` and ADSB pipeline startup receive it automatically. Resolve
against the effective coordinates retained after the fill-only airport merge,
not coordinates rejected by that merge. At the daily pipeline boundary, run an
idempotent ensure step after airport insertion and before publication or reuse
of a cached daily receipt. This catches newly introduced stub airports and
already committed raw days. A missing resolver/data installation fails producer
reference preparation with an actionable dependency error; it cannot silently
count as completed timezone ingestion. Individual unresolved airports are reported
and do not block valid UTC flight facts.

Do CPU work outside SQLite write transactions. Under the producer operation lock,
read inputs and ownership, resolve, then acquire a short write transaction and
recheck input/ownership fingerprints before applying. On concurrent changes,
recompute or report a conflict; do not commit results for stale coordinates.
Connection-level helpers must reuse an already-held producer lock rather than
reacquiring it from `daily_range()` or another enclosing operation.

## Publication and replacement

Split timezone merge logic from the generic fill-only `merge_airport()` path.
Timezone publication must support replacement of previously generated values,
while preserving the serving store's own manual/legacy decisions. Copy the
timezone snapshot and applicable state atomically with effective values; never
copy a producer ownership marker onto a protected serving override. Require
matching coordinate fingerprints before applying a coordinate-derived zone to
a serving airport. A serving coordinate override produces a reported conflict
instead of a silently mismatched mapping.

Both `references publish` and ordinary day publication use this helper. Day-run
resume must still ensure/publish pending reference metadata before returning
`daily_already_published`. Timezone changes must not force raw-day downloads,
flight audits, flight revision changes or re-extraction. Keep timezone receipt
identity independent of raw extraction and flight-verification receipts.

Update `serving_controls.controls()` and `prepare_serving()` to retain/remap the
new tables along with manual/legacy ownership and snapshot identities. Older
databases without the tables are valid read-only inputs; explicit writer
initialization upgrades them. Update reset, quota and installed-wheel checks for
the additional dimensions. An old writer must not run concurrently through the
upgrade and lose ownership metadata; stop producer/admin writers for migration
and deploy compatible writers together. Rollback must preserve a verified copy
of the metadata/ownership state and only restore values still owned by the
rolled-back operation, so later manual edits survive.

## Calendar behavior and operational checks

For `airport_metadata_missing`, show a visible message next to the departure
field: "Local dates are unavailable for this airport. Use UTC dates to continue."
Offer a `Use UTC dates` button beside it. Clicking it explicitly changes the
clock and reloads availability. Keep the normal dataset-filtered calendar;
never silently reinterpret a local date as UTC or enable unsupported dates.
Preserve airport/filter inputs, ask the user to review the refreshed date, and
do not automatically submit a search. General storage/network failures retain
retry behavior; they must not be presented as missing timezone metadata.

Expose cheap airport-only coverage counts in producer reports and admin metadata
status. Keep storage readiness about storage. Extend deployment checks to report
local-date capability, check local availability for a known resolved origin,
and exercise missing-zone UTC recovery in browser QA. An empty/unresolved
catalog can still support UTC; it must not be advertised as fully ready for
airport-local dates. Do not add archive-wide flight scans to readiness.

Pin/report the server's effective IANA rules source as well as the boundary
dataset: Python ZoneInfo may otherwise use host files despite an installed
`tzdata` package. Load tzdata 2026.2 resources explicitly through `core.tzdb.zone`,
record that version, and test the configured loader. Browser Intl has its own
rules data; server resolution remains authoritative for query bounds, and public
timezone identifiers must remain compatible with supported browsers. Store no
precomputed offset as the airport timezone. See [IANA's database description](https://data.iana.org/time-zones/tz-link.html).

## Acceptance and rollout

Tests must demonstrate:

- Toronto and Seattle historical DST offsets and 23/25-hour calendar days;
  explicit rejection/disambiguation of nonexistent/ambiguous local times.
- Tokyo without DST and Kathmandu's fractional-hour offset, using real pinned
  resolver data in a small integration test as well as deterministic unit fixtures.
- Missing/invalid coordinates, ocean results, overlapping polygons, invalid IANA
  identifiers, and boundary changes that invalidate a generated mapping.
- Repeated imports are no-ops; new airport batches and cached-day resume enrich
  and publish metadata without changing raw receipts or flight revisions.
- Manual same-value pins, legacy zones, serving coordinate overrides, concurrent
  edits and import/export round trips retain their ownership semantics.
- Publication, fresh serving replacement, reset and failed/quota-exceeded imports
  preserve consistency and roll back atomically. Flight facts, admissions,
  approvals, tombstones, UTC timestamps and transfer groups remain unchanged.
- A fresh browser visit from an ingested catalog can select an airport-local date;
  an unresolved origin can explicitly switch to UTC and complete a search in
  Chromium and WebKit. No public response exposes internal reference evidence.

Implement as two reviewable PRs: (1) producer enrichment, ownership, metadata
publication, migration and backfill; (2) browser recovery and capability checks.
Both are required to close #21 and this calendar incident. Neither PR should
include another hand-edited production timezone patch.

Roll out compatible code first, then pause producer writes, preview the existing
catalog backfill and inspect every changed/protected/unresolved mapping cohort.
Measure actual coverage; do not promise all 8,801 airports will resolve. Apply
private enrichment, explicitly publish reference metadata, then verify YYZ/NRT,
DST fixtures and the missing-zone recovery path on the live service. Retain
before/after operation reports and a metadata rollback snapshot. Resume the
producer and verify a repeat run makes no effective changes. No flight archive
download or historical flight reprocessing is part of this rollout.
