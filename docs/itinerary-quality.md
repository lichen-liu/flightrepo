# Itinerary confidence and review

Quality is separate from search admission and human approval. `review_reasons`
retains the original extraction concerns. API flight objects additionally expose
`quality.level`, `quality.labels` and `quality.review_recommended`. Labels share
one Python classifier across API, admin and producer review; the frontend renders
those supplied labels rather than inventing its own classifications.

| Level | Label severity | Meaning and examples |
| --- | --- | --- |
| `standard` | none | No retained note; this is not a guarantee of accuracy |
| `noted` | `info` | Worth knowing: observed time bounds, missing aircraft type, undated VRS match or absent reference |
| `uncertain` | `caution` | Airport estimate without a route match, unmatched long observation gap, callsign change or different VRS route |
| `conflict` | `warning` | Retained airport/time windows imply implausible speed |

Search cards show uncertainty/conflict labels. Each leg expands all notes and
explains their limits. A journey only receives the VRS match badge when every leg
has a current positive match; individual legs always show their own evidence.
A manual approval never removes a timing-conflict warning or original reasons.
A VRS match replaces the airport-estimate caution but does not certify historical
timing or exclude an unobserved stop. Its observation gap remains an informational
note. Different current VRS airports remain a caution because the reference is
undated and callsigns/routes can change.

`quality.review_status` is `recommended`, `manual` or `not_requested`.
Admin `flights --needs-review` and API `needs_review_count` use confidence-based
review recommendations, including searchable airport estimates. The legacy
model `needs_review` and search admission still use the previously approved gate:
estimates alone qualify, a current directed VRS match also waives observation-gap
holds, and timing/callsign/other blockers remain held unless individually approved.
**The proposed broader labeled admission is not enabled by this change.**

## VRS is part of publication

The producer `publish` command audits each departure day against the pinned private
VRS snapshot before publication. `--vrs auto` is the default: reuse the snapshot,
or download the two bounded bulk CSVs once on a fresh node. `cached` forbids network
fetches; without a snapshot the receipt says `not_checked`. `refresh` fetches a new
snapshot explicitly. `skip` preserves existing per-record audits without checking
them again. An auto/refresh fetch failure fails the command; it never silently
claims verification. Serving reads never fetch or write.

Audit records matches, differences, incomplete mappings and absent callsigns with
source hash, exact callsign and directed airports. Changed callsign/endpoints
invalidate that record's previous reference. Repeating a day is idempotent;
explicit refresh plus publication reconciles changed reference evidence. Human
edits, approvals and tombstones remain protected. Private reference tables and
review journals never enter serving. See [VRS reference details](vrs-verification.md).

## Review cohorts instead of millions of rows

```sh
python -m ingestion --db var/ingestion/ingestion.db review queue \
  --start 2023-07-01 --end 2023-12-31 --limit 20 \
  --report var/ingestion/review-queue.json
python -m ingestion --db var/ingestion/ingestion.db review show \
  --start 2023-07-01 --end 2023-07-31 --origin YYZ --destination SEA \
  --reason observation_gap --samples 5 --report var/ingestion/review-case.json
```

The queue ranks conflicts first, then large uncertainty cohorts grouped by directed
airport pair, original reasons, endpoint evidence and VRS result. Samples diversify
aircraft types/reference chains and show retained distance/time diagnostics.
Selection and revision/airport-metadata fingerprints bind a case to the inspected
evidence. Decisions fail if members, revisions or airport evidence have changed.

Producer-only decisions require a nonempty explanation:

```sh
python -m ingestion --db var/ingestion/ingestion.db review decide \
  --case var/ingestion/review-case.json --action acknowledge \
  --note 'Inspected representative cases; retained coverage limitation'
```

`acknowledge` removes that unchanged cohort from the pending queue. It does not
approve its flights, change search eligibility or erase labels. Changed/new data
reopens the cohort. `queue --include-acknowledged` includes previous acknowledgements.
For exactly one record, create a fresh case with `review show --provider adsblol
--record-id ID`. Then use `approve`, `reject`, or `correct --replacement FILE`.
Approval is explicit human verification; rejection retains private source evidence
and creates a tombstone; correction requires a complete validated `FlightRecord`
with unchanged source identity. Review never guesses missing airports/times/types.
Only acknowledge is permitted for a multi-record cohort.

Decisions and before/after receipts commit atomically under producer locks and the
private DB quota. They do not modify serving immediately: explicitly publish the
affected departure dates afterward. A corrected departure moved across dates
requires both old and new dates. Admin supports `review queue` and `review show`
read-only, including the interactive shell; it cannot launch review writes.
Admin reset/clear also clears private review journals. Keep archival receipts
outside a database before intentionally resetting it.
