# Round 3 QA findings

13 October 2026 · `/root/qa_auditor` · disposition: **needs revision**. This is a repair report for the initial synthesis, not final acceptance. The final manifest and review receipts were not present at inspection.

## B01 · Incorporate the clarified task before freezing

The latest owner direction emphasizes “exploring, selecting, analyzing, ‘play around’ each flight option.” The inspected recommendations still put the unpriced travel-purchase value question first and frame the traveler outcome as an affordable purchasable journey. That is a valid wider context, but it should not override the owner's clarified initial product job.

Repair: record this as a dated owner input, not an observed customer finding. Prioritize the exploration lifecycle: enter from a place/flight/question; discover options; select exact dated evidence; inspect space/time; compare; change a leg or constraint; understand the consequences; recover/Undo; retain a useful exploration. Compare both goal-directed and self-directed use. Define what makes exploration useful or enjoyable beyond a novelty click, while preserving price/protection/ground/access questions as broader unmet context. Preserve the three audiences and final-layout uncertainty. Recheck narrative, structured priorities, tasks and debate for the same scope.

Acceptance: the first experiment measures the clarified exploration/analysis/reversible-edit job, and the commercial-context test cannot erase enthusiast/research or schedule-exploration value merely because the entire purchase decision remains unanswered.

## B02 · Evidence edges must say what they support

All U rows are appropriately labeled hypotheses, but the generic `evidence` arrays can still suggest unsupported source provenance. Examples in the inspected register: U34 freshness/completeness points to E04 Heathrow transfer instructions; U36 scheduled versus flown points to E04; U48 unusual routes/flight reconstruction points to E04; U40 enjoyment/return points to E05 WCAG; U50 export/reproducibility points to E07 research planning. The sources do not establish these particular needs or proposed outcomes.

Repair: use explicit provenance relations. Retain a source only with a narrow explanation of its context/standard contribution; separate owner requirement refs, author hypotheses and genuinely source-supported claims. Empty public-source support is legitimate for a new hypothesis. Do not fill every row with an apparently relevant source solely to make the trace checker pass. Preserve the source-demand distinction and require this relationship in review/checks.

Related small repair: `recommendations.md` traces the honest-empty/fresh-results row to U48, although U48 concerns unusual routes/reconstruction. Use U33/U34/U52 or state the intended relation. E01 should identify its selected examples as a subset rather than imply the five listed entries are the entire chart. Link the CAP3213 landing page for its publication date; that date is not present in the PDF's substantive chart.

Acceptance: each retained evidence edge has a supported, bounded relationship; reviewer can tell owner provenance, standards, public context and unsupported demand hypotheses apart without guessing.

## B03 · Integrity helper currently accepts misleading records

Independent in-memory corruption probes imported `scripts/verify_research.py` and passed temporary register/manifest files without altering real artifacts. The following invalid records all returned `ok=true`:

| Probe | Why it matters | Required detection |
|---|---|---|
| Set G05 `human_required=false`, `state=passed` | The customer gate can be promoted by changing the flag that decides whether it is checked | Require the expected human gate IDs, required flags and pending states for this desk-only campaign |
| Set T01 `method` to an empty string | The helper claims task integrity while accepting a task without a method | Validate required nonempty task title, fixture, method, truth/observation and status |
| Remove U21 | Union coverage of N01–N26 still passes although one declared U question disappears | Require the campaign's declared complete U/T/Q/O/E/G sets, or bind declared expected counts/IDs independently of input arrays |
| Manifest has only `needs.md` plus one historical input | Register and verifier can be absent from the reviewed artifact set | Require register, verifier and declared core synthesis artifacts in the manifest; ensure the CLI register is the reviewed register |

Repair: add meaningful negative cases for these paths and stale artifact/manifest/receipt hashes, duplicate IDs, broken references, missing receipts, forbidden human signoff and absent owner clarification. Validate source metadata and provenance relationships introduced by B02. Keep the helper's limitation explicit: a hash or ID check does not verify customer truth, source interpretation or a genuine reviewer action.

Acceptance: normal artifacts pass after freeze; each deliberately corrupted case fails for the expected reason; no customer/owner gate can pass by weakening its requirement flag. Preserve actual reviewer attestation actions separate from machine-validated JSON.

## B04 · Some task links are topical, not acceptance coverage

U04 asks how to return or visit several places, but its only T18 task checks ticket/booking scope, not ordering of places, stay duration, return dates or coherent outbound/return selections. A valid task ID therefore does not establish that the candidate question has a proposed test capable of finding its failure. Similar broad activity cards must not imply every linked U question will be attempted in a session.

Repair: add the missing outcome checks to an appropriate planned task or explicitly defer the outcome outside the next slice with its future test. For the clarified core exploration slice, trace each important outcome to the actual task step and observable success/failure, including meaningful discovery, selection identity, comparison, changed-suffix explanation and reversible recovery. Mark prototype/data prerequisites for unimplemented concepts before recruiting for those tasks.

Acceptance: a reviewer can locate an observable criterion for each included outcome, and unattempted/deferred outcomes remain unvalidated. No 20-task catalog is presented as evidence that all 64 questions have been tested.

## Inspection identity and limits

These findings concern the following initial artifact identities. Revisions require fresh review; the table is not a final manifest.

| Artifact | SHA-256 |
|---|---|
| `README.md` | `593639bd2db4f1e0158bd40e61519133c7ff0a80951fcbf4c37b2c5122a2ddea` |
| `recommendations.md` | `821214acf7cbd95a05073c51009c08cc7ef33bf9bc85a08cc305e579536948f7` |
| `needs.md` | `aece3a2d8bcb1af9848d7de6dea860aed1e7f62c85aef88d157bc9096125a145` |
| `evidence.md` | `ddf19b26372958f9d2720fccf11434cc2c914f2983e5580be438fca978be541c` |
| `validation.md` | `a245cf73d2990f642cb5d02bfc30dfa17569f6a577f2ca4574528ccc728518ca` |
| `register.json` | `22618906ee6de30d38e9a8932617c3f1f5ee4e9095cee3bcc4bfb22d36938e40` |
| `debate.md` | `35d95c65ade2bffe5babfddf8e010f38c747dbb595fc581ecfaed635727eb62a` |
| `scripts/verify_research.py` | `29bb68112f4d5f433ca0982c4c26a2e7f6f31b666d3cdf103822d65d53f1135c` |

Markdown relative links in the inspected synthesis resolved. The seven narrow external source claims were independently checked through primary-source pages/sections; those source checks do not repair the unsupported U-level evidence relationships. CAA Q7 semantic/chart text and landing metadata were verified; the screenshot response in this runtime supplied only a citation, so no chart visual-inspection claim is made by this reviewer. Historical R08 code QA has not been rerun. No human/customer or final design signoff is issued.
