# Timezone design audit before implementation

Decision: proceed after the revisions below. This is a local design audit, not an
independent reviewer approval. Application implementation had not started when
these findings were recorded.

| Question | Strong alternative | Decision and reason |
| --- | --- | --- |
| Missing step or broken publication? | Re-run the existing airport refresh. | Enrichment is absent: 8,801 private airports have no zones. Refresh cannot supply a field absent from its source. |
| Airport timezone catalog or coordinate resolution? | Join a second airport catalog by IATA. | Use existing effective coordinates, avoiding duplicate/reassigned-code joins. Report ambiguous polygons instead of choosing one. |
| Resolver dependency? | Latest timezonefinder, or lightweight tzfpy. | Pin timezonefinder 8.2.4: Python >=3.11, verified full 444-zone data, tested locally on 3.14. Latest 9.x requires >=3.12. tzfpy simplifies boundaries; speed is not decisive for 8,801 points. |
| First match or all matches? | Use certain_timezone_at() once. | Check all polygon bounding boxes/containment with the pinned adapter. First-match APIs hide overlaps. Measured 20 full lookups in about 0.012 seconds locally; benchmark the complete catalog before rollout. |
| Independently pinned data package? | Assume the current split-package API exists in 8.x. | Incorrect assumption in the original design. 8.2.4 bundles data; record the source artifact SHA and verify each installed data-file hash against a checked-in manifest. |
| Global snapshot per catalog result? | Include all airport results in snapshot identity. | Revise: dataset/resolver identity is stable. Airport input/result fingerprints live per row, so adding one airport does not rewrite existing ownership rows. Run reports retain aggregate result digests. |
| Fill NULL only? | Avoid ownership tables and never replace anything. | Insufficient for corrected coordinates and dataset updates. Track generated ownership; protect legacy/manual values and detect external divergence. Explicit same-value admin pins must be recorded. |
| Implicit UTC fallback? | Automatically change the clock after failure. | Require a visible explicit UTC action; refresh dates and require review without auto-submission. |
| Change extraction receipts? | Put timezone snapshots into existing reference_snapshots. | Keep separate tables and reference publication before cached-day reuse. Timezones must not invalidate flight verification or trigger extraction. |
| Rules reproducibility? | Install tzdata but continue using host ZoneInfo defaults. | Load the pinned tzdata resource explicitly through a small shared loader. Installing a package alone does not select it. |

Implementation audits must check atomic airport/enrichment commits, coordinates
actually retained by merges, lock nesting, concurrent ownership changes, legacy
readers, reset/replacement preservation, quota rollback, no-op repeats, and cached
daily publication. Publication must not label serving overrides as generated.
Missing/ambiguous mappings remain NULL; storage readiness must still support UTC.

The exact pinned adapter uses release-specific geometry APIs and must fail on a
version/data mismatch. This is a deliberate compatibility boundary, covered by a
real-data test and hash verification, rather than an unbounded dependency range.

References: [8.2.4 package metadata](https://pypi.org/project/timezonefinder/8.2.4/),
[current package metadata](https://pypi.org/project/timezonefinder/),
[tzfpy accuracy and overlap behavior](https://github.com/ringsaturn/tzfpy).

## Backend implementation audit

The local audit found and corrected these behaviors before the backend PR:

- Publishing reference metadata before a failed flight audit created a serving
  store too early. Ordinary runs retain verify-before-publish; only a previously
  verified cached day takes the separate reference-publication path.
- A coordinate-only admin edit left an old generated zone active. It now clears
  that value immediately; manual zone overrides stay protected.
- Serving-authoritative hydration could roll back newer private reference
  mappings. Reverse hydration now preserves generated producer ownership, while
  still accepting explicit serving overrides.
- A direct producer coordinate/value edit could make stored state stale. Value
  divergence becomes protected legacy ownership; stale reference coordinates
  are reported as a publication conflict instead of being copied.

Evidence: full Python run passed 1,398 tests; the subsequent focused audit run
passed 60 tests. Installed-wheel application/ingestion probes and Python style
checks passed. Local runtime is Python 3.14; 3.13 remains a CI check.

A read-only preview of the current 8,801-airport catalog took 2.91 seconds:
8,767 new values, two protected existing values, 31 ambiguous overlaps and one
other unresolved mapping. This measured preview is not a production backfill.
No implementation migration or enrichment has been applied to the live stores.
