# Adoption, audit and publication

These producer tools use only retained ADSB.lol itinerary facts and the existing
OurAirports reference. Source licences remain [ODbL/public domain](ingestion.md#sources-and-licences).
The core audit does not query external flight sites or certify commercial service/flight completeness.
After adoption, the separate [VRS reference tool](vrs-verification.md) adds private
undated route checks. Publication audits a pinned VRS snapshot automatically. All four known uncertainty
categories qualify for labeled search; VRS matches update confidence, not human
approval or historical timing. Unknown reasons remain excluded. Future schedule
ingestion is deferred; Flight.info/FR24 historical timing access is not yet established.
Admin can inspect the private SQLite tables but cannot launch these writes.

## 1. Finish the existing job

Keep its pinned runtime and successful days. The July–December 2023 job includes
**2024-01-01 as an arrival-context input**, retaining 2023 departures. Do not restart
it against the new store or overwrite its raw-day directory. Final validation must
check every expected input day, not only the latest date. Preserve its source DB
as a rollback copy.

## 2. Adopt into a new private file

```sh
python -m ingestion --db var/ingestion/ingestion.db adopt \
  --from-db var/hosted/flights-2023-relaxed.db \
  --source-root var/hosted/adsblol-2023-jul-dec-v5 \
  --policy-db var/hosted/flights.db --profiles-db var/hosted/flights.db \
  --start 2023-07-01 --end 2024-01-01 \
  --report var/ingestion/adoption.json
```

`--start/--end` here are inclusive **input** dates, including context. Adoption
requires completed v5 receipts and no running batch. Producer/extractor locks
must be free. It uses a consistent SQLite backup into a temporary file and
compares row counts and SHA-256 digests of every original table before and after
initialization. Flight identities, types, time bounds, manual approvals/edits,
tombstones, receipts, boundary summaries and extraction configuration remain intact.
Only the verified copy receives the private role and publication index.

The destination must be new: existing files/sidecars are never overwritten.
Integrity, foreign keys and quota are checked before atomic no-clobber creation.
`--policy-db` copies the latest persisted quota once; the new file then owns its
policy. `--profiles-db` preserves producer profiles from the old selected DB in
the private store, updating the ADSB output path to its new destination. Source
extraction configuration remains unchanged; profiles never enter serving.
Raw/staging space remains excluded. No source or serving facts change.
Adoption is an explicit compatibility handoff, not an automatic migration at startup.

## 3. Audit and group held records

```sh
python -m ingestion --db var/ingestion/ingestion.db audit \
  --report var/ingestion/audit-before.json
python -m ingestion --db var/ingestion/ingestion.db audit --apply \
  --report var/ingestion/audit-applied.json
python -m ingestion --db var/ingestion/ingestion.db audit \
  --report var/ingestion/audit-after.json
```

A read-only audit can also inspect the still-running legacy archive. Its consistent
snapshot is preliminary: `pipeline.active_batches` and input coverage identify
unfinished work. `--apply` is allowed only for private stores with no active job.

Reports include integrity/FKs, duplicate source identities, search/hold/type counts,
month coverage, missing metadata, reason counts, possible duplicate signatures and
ranked airport-pair/reason cohorts with bounded samples. Exact same-source signatures
are review hints; no physical flights are automatically merged or deleted.
Counts are measured **before corrections**. Applied/skipped counts are separate.

Supported automatic changes are deliberately narrow:

| Retained evidence | Supported change |
| --- | --- |
| Airport distance requires over 1,500 km/h across the widest observed window | Add a missing timing-conflict warning |
| Complete known airport/time fields no longer meet that same timing rule | Remove a stale machine timing flag |
| Stored evidence class is proximity/ground brackets | Align the airport-estimate label with that class |
| Missing coordinates or incomplete timing | Retain the timing uncertainty; report missing evidence |
| Observation gap, unknown aircraft type or uncertain airport truth | Keep unresolved; no guessed values or blanket approval |
| Explicit human approval or local edit | Preserve unchanged, including any timing exception |

Corrections change review flags only. Original endpoints, times, aircraft type,
source identity and human decisions remain intact. Each applied change is recorded
in `ingestion_audit_changes`, including before/after reasons, revisions and retained
geometric/time evidence. A compact `ingestion_audit_runs` receipt identifies the
scope/version/generation. Changes and journal commit atomically; quota failure
rolls them back. Revision/airport checks skip concurrent changes; rerun if any are
skipped. Clear/reset also clear these private audit artifacts.

Review cohorts, rather than individual millions of records:

```sh
python -m ingestion --db var/ingestion/ingestion.db audit \
  --reason observation_gap --origin YYZ --destination SEA \
  --start 2023-07-01 --end 2023-07-31 --sample-limit 5 \
  --report var/ingestion/review-yyz-sea.json
```

Use common/repeated patterns and severe timing cohorts first. A plausible distance
or a current route webpage cannot exclude an unobserved stop. Selectively reprocess
the relevant original UTC day plus adjacent context when raw observations are needed;
never clear the full dataset for a local review. Reprocessing must preserve stable
keys and explicit human decisions. Missing historical metadata stays unknown until
supported by source evidence. No automated raw-day reprocessing is triggered by audit.

## 4. Publish into a clean serving replacement

```sh
python -m ingestion --db var/ingestion/ingestion.db publish \
  --serving-db var/hosted/flights-published.db \
  --preserve-serving var/hosted/flights.db \
  --start 2023-07-01 --end 2023-12-31
```

The new target is seeded with canonical quota, airport metadata/transfers, manual
routes, explicit edits/approvals and tombstones. Old automatic flight records and
pipeline state are excluded. Publication then copies eligible audited facts one
day at a time, retaining unified notes, uncertainty and conflict warnings. Unknown
reason codes remain excluded; private references and journals never publish.

The seed records a fingerprint of canonical administrative state. Retries verify
it; if canonical edits/configuration changed, rebuild a new replacement from the
latest canonical snapshot. This avoids silently choosing between conflicting edits.
Before cutover, stop canonical writers and call
`ingestion.serving_controls.verify_serving_controls(canonical, replacement)` again.
Preserve deployment configuration and rollback files, deploy compatible tested code,
and use the documented SQLite stop/checkpoint/replacement procedure. Verify real
readiness, actual route search, calendar availability and aircraft types locally and
publicly. Only a **serving** replacement may replace canonical serving; never rename
the private ingestion file into that path. PR merge still requires user approval.

## Confidence labels and smart review

Use the [unified quality and cohort review workflow](itinerary-quality.md) to
separate informational notes, uncertainty and conflicts, inspect representative
samples and record revision-bound producer decisions. Admin remains read-only.

## VRS audit and approved labeled admission

Publication uses `--vrs auto` by default, reusing a pinned bulk reference or
fetching it once if absent, then auditing each day. For a separate preview run
`vrs audit`, then `vrs audit --apply` for the chosen range (`admit` remains an alias).
Exact directed pairs and adjacent legs remove the unconfirmed airport caution,
while missing/different references remain visible. All four known uncertainty
categories are searchable with labels; an audit does not erase original concerns
or bulk mark human approval. Republish/deploy compatible code to expose evidence
changes. See [VRS audit semantics](vrs-verification.md#explicit-best-effort-route-admission).
