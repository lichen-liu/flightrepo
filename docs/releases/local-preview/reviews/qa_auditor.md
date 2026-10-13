# Independent release QA review

2026-10-13T21:18:12.991418+00:00 · `/root/qa_auditor` · **Accepted for local preview, demo and PR review**.

This attestation applies only to manifest `ebe1f3c721a0f4bfa051cb327df37e70ee61df37a548bb3902860d0604c94fde` and its 96 members. It grants no customer/final visual-direction, accessibility-conformance, public production or merge approval. The actual current-head remote checks must pass before merge. This reviewer authored the audit plans and reviewer records, and made no implementation edits.

## Verified outcome

All 96 final hashes match, all embedded QA source maps match, and the final wheel matches all 42 package files. I independently reran the full suite: **391 passed**, one upstream Starlette deprecation warning, in 3.29 seconds on Python 3.14.7/macOS. The Python source/test bytes remained unchanged through the final freeze. The exact CI Ruff lint/format commands and Git whitespace check pass; 48 Python files are already formatted.

I ran **27 additional independent graph/HTTP/CLI/wheel assertions**, using manually specified expected paths and a temporary database. A direct AAA → EEE flight is better under every unconstrained ranking; with via DDD and limit 1, every supported ranking correctly returns AAA → BBB → CCC → DDD → EEE. Rebuilding after AAA → BBB with immediate next CCC retains the outstanding DDD requirement and chooses its slower valid tail. A via BBB already satisfied by the prefix allows the faster CCC → EEE tail without a second visit. These checks distinguish constraint handling from filtering an already ranked/page-limited response.

Further probes verified directed transfer departure as a required stop, permission enforcement, immediate-only next filtering/counting, explicit-choice contradiction errors, unknown-code/prefix preservation, incomplete explicit choices, partial unknown totals, request identities, HTTP validation and the two previously failing malformed-import shapes. Bad imports and an unconfirmed reset report friendly errors without creating a database. The detailed expectations are in [the JSON attestation](qa_auditor.json).

I also independently extracted the **final** wheel and ran its API outside the checkout against a new temporary synthetic database. Core/server/admin imports came from that wheel; root/assets/readiness, local via search at limit 1 and next-airport rebuilding at option limit 1 passed. The wheel identity is `16e47ecd60ae5e46be9806254a48621a40d716b84f8c6f2de215b47584c9b8c0`.

## Evidence and review quality

I inspected actual routing/rebuild/reader/local-time and storage/admin/ingestion boundaries, current UI controls and state handling, CI/policy, packaging and demo/scope documents. The final 93-check browser record has matching source hashes and no JavaScript runtime errors. I viewed the typed-airports, editor, touch-phone map, mobile journey and repaired partial-empty captures. The new controls and labels are readable in those captures; the partial-empty screen expressly leaves unexamined matches possible. Source inspection and native validation support the direct-code contract; this is bounded mechanics/visual review, not customer preference or accessibility certification.

The research verifier passes with all three actual R09 receipts, preserving its distinct research-plan scope. The prior research manifest remains unchanged. Thirty-four local Markdown file links in manifested documents resolve. Local databases, sidecars and wheels are ignored; demo startup selects a separate DB and loopback host. Reader snapshot and transaction tests pass, including the separate writer-process case. The current benchmark and HTTP records identify synthetic/local scope; their results were read and hash-checked, not independently rerun by this reviewer.

The actual final backend and frontend receipts both accept this same manifest. Each discloses prior implementation authorship, which makes independent whole-release QA necessary. I read their methods, findings and limitations; neither presents an agent receipt as a human signoff.

## Repairs and rejected versions

The initial 4c66 freeze matched at the start of this review, then three files changed during repairs. I detected the mismatch and withheld approval. The team corrected partial absence claims and prospective remote-CI wording, reran browser QA, rebuilt the wheel and froze ebe1. All **96 superseded 4c66 members** are retained under `qa/rejected-partial-copy/source` and independently match their former manifest. Earlier 724f evidence remains a separate historical checkpoint with no final release signoff.

CA01 and CA02 are resolved on this version. The current partial-empty result reports no route returned before stopping; its notice does not describe nonexistent returned routes, and unfinished onward calculations say no complete route was found. Two negative browser assertions and actual recapture support the repair. README now says the remote run will accompany the PR and must pass before merge. The previously caught stale HTML wheel is retained in the build-sequence record; the final package was verified independently.

The earlier L01–L07 admin/configuration/provider-comment/CI/navigation findings have appropriate local dispositions. No blocking finding remains for this reviewed local milestone.

## Gate disposition

A01–A07 and A09 pass for the bounded local preview. A08 passes for PR preparation with remote merge acceptance still pending. TA01–TA15 are accepted in the implemented typed UI/search/via/next scope, with explicit API and count boundaries recorded in the JSON: optional blank UI controls send null, invalid blank API constraints are rejected, the existing rebuild destination contract is uppercase, and data-limited option counts describe examined loaded options alongside visible partial diagnostics. Candidate-limited totals remain unknown.

One required intermediate airport and one immediate next destination are supported; broader pinned comparisons, commitments and customer interaction hypotheses remain proposed. Synthetic schedules and assumed transfer allowances cannot establish travel suitability or booking facts. Customer sessions, final layout choice, native suggestion usability, full accessibility, broader compatibility, real-data trust and public access controls remain open.

**Local preview/demo/PR review: accepted. Merge, customer-final design and public launch: not approved by this receipt.** Later substantive manifested edits invalidate affected review until checked again.
