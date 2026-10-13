# Research QA plan

13 October 2026 · independent process reviewer `/root/qa_auditor` · initial plan, not final acceptance.

This round can establish a reviewed research proposal and a traceable experiment backlog. It cannot establish what Flyji customers value most without observed customers. Agent agreement supports internal consistency; it does not provide customer validation, owner acceptance of a final layout, accessibility conformance or readiness for public use.

## Baseline checked

The reviewer read `docs/design/qa-audit.md`, `docs/design/design-decisions.md`, `docs/product/README.md` and `docs/product/qa/final.json`. The R08 record identifies 149 Python tests and 65 browser checks as author QA, and explicitly records no customer research, independent review, remote CI run or public deployment. The three audiences are owner-confirmed; the final visual direction remains open. This audit does not rerun or independently certify those historical tests.

A new SHA-256 comparison against `docs/product/qa/final.json` on 13 October checked all 58 retained source entries in the current checkout and their final source archive. Both comparisons returned zero mismatches. This establishes the checked source identities, not that every capability is correct.

Source/test spot checks verified that:

- `src/server/web_models.py` fixes catalog coverage to `unknown`; `test_catalog_has_facts_but_does_not_claim_coverage` checks the HTTP response. Stored flight counts or date extents therefore must not be presented as provider completeness.
- `tests/test_web.py` covers local normalization, DST gaps/repeated times, strict constraint field names, preserved avoidance and metadata shared with routing facts in one read snapshot.
- `tests/test_planning.py` covers stale/missing chosen flights, read-only rebuild, exact airport/time-zone lookup and explicit snapshot truncation. These tests do not establish protected tickets, physically safe transfers or published minimum connection times.
- `src/server/api.py` documents rebuild as calculation without plan mutation. A future research proposal must distinguish a local saved copy, a canonical search URL and a server-persisted user plan.

The spot checks are source inspection and audit-record consistency checks. Synthetic fixtures, bounded workloads and desktop headless checks retain those limits in every later claim.

## Evidence categories

| Category | Meaning | Permitted conclusion |
|---|---|---|
| Owner instruction | Explicit request in this conversation, with date and quoted or accurately paraphrased scope | Product constraint or approved work strategy; not population preference |
| Primary external evidence | Opened source with publisher, exact URL, retrieval date, supporting section and limitations | The specific published finding or product behavior |
| Observed customer evidence | Consented participant/session record, recent task/context and observation separated from interpretation | A scoped finding about the observed participants/tasks |
| Product verification | Source identity plus executable check or bounded inspected behavior | The tested behavior in the stated environment/data |
| Design hypothesis | Inference, analogy, preference or proposed outcome lacking direct customer evidence | A question/experiment worth testing; not a proven need or priority |
| Scope constraint | Current architecture, data availability or operating limit | Feasibility/support boundary; not a reason to redefine the customer's need |

Each requirement needs an ID, user outcome/context, provenance category/source IDs, confidence explanation, known counterexample, current implementation status and a testable acceptance criterion. Avoid unsupported counts of demand, invented percentages, numeric confidence without a basis and claims that a competitor's feature demonstrates popularity.

## Gates for each iteration

| Gate | Required input | QA check and reviewer | Output allowed after pass |
|---|---|---|---|
| G0 Scope | Latest owner instructions and existing accepted constraints | Facilitator plus opposing reviewer verify accurate scope and unresolved decisions | Research question set; final UI remains open |
| G1 Evidence | Source ledger and dated observations | Reviewer opens sources and checks each material claim, inference and freshness; record paywall/unavailable material | Evidence-backed facts plus explicitly labeled hypotheses |
| G2 Needs | Outcome inventory traced to G1/owner scope | Advocate checks breadth/context; challenger detects solution-first wording, missing audiences and unsupported priority | Candidate needs and critical unknowns |
| G3 Alternatives | At least two plausible solutions per material tradeoff | Roles independently defend and attack alternatives; expose cost, reversibility and rejected options | Experiment candidates; no assumed universal winner |
| G4 Contract | Acceptance criteria, data semantics, interaction/recovery paths | QA tests whether criteria can distinguish success/failure and whether claimed data exists | Reviewable slice specification; unsupported signals remain unknown |
| G5 Product QA | Exact code/prototype version, fixtures, environment and check method | Semantic tests, keyboard/touch/reflow, visual/manual accessibility and failure checks suited to slice | Bounded implementation verification; never transfer another version's pass count |
| G6 Customer learning | Consented recent-task research and counterbalanced prototypes | Independent analysis separates observation from interpretation; records task failures and dissent | Scoped customer findings and revised priorities |
| G7 Decision | Evidence, alternatives, dissent dispositions and reviewer records | Owner explicitly accepts/revises/rejects final choice; QA checks approved artifact identity | Accepted decision within stated scope |
| G8 Operating readiness | Real provider rights/coverage, revisions, accessible end-to-end flows and representative load/recovery evidence | Dedicated technical/data/accessibility/operating reviews | Hosted-pilot or public readiness only for the reviewed conditions |

This research-only round can complete G0–G3 and specify G4–G8. A gate must say `passed`, `needs revision`, `pending evidence` or `not in this round`. A planned check is not a passed check. A customer evidence gate cannot be waived by agent unanimity.

## Debate and dissent records

For each round record the question, input artifact paths/hashes, advocate position, opposing position, claims disputed, evidence added, revision, reviewer result and remaining dissent. Each objection gets a stable ID and one disposition: repaired and rechecked; resolved by source/logic; experiment required; accepted constraint with owner; or deferred outside the slice with rationale and trigger. Do not relabel an unresolved contradiction as consensus.

High-impact dissent includes misleading completeness/bookability, incorrect local dates, silent changes to a selected itinerary, inaccessible core tasks and missing support for an entire intended audience. Such dissent must be resolved or explicitly remain a gate failure. Cosmetic preference can remain a controlled experiment with no final default selected.

Opposing agents should make a concrete falsifiable challenge, rather than manufacture disagreement. The facilitator must preserve the strongest counterargument, including evidence that weakens a favored recommendation. A later round should test the revised artifact rather than repeat a stale objection to old text.

## Reviewer attestations

Record reviewer role and agent/task identity, timestamp, artifact path and SHA-256, reviewed scope, method, findings, disposition and explicit limits. A plain review attestation records an actual agent review; SHA-256 identifies bytes and is not a cryptographic reviewer signature or proof of human identity. Keep any owner reply and customer consent/evidence separate.

An edited artifact invalidates its prior whole-artifact approval. Re-review changed claims and verify unchanged dependencies; retain the earlier record rather than rewriting its conclusion. Final synthesis needs sign-off from both opposing roles and an independent QA reviewer against the same byte identities. Any self-review must be labeled self-review.

## Convergence and reopening

Internal proposal convergence is defensible when all owner constraints have traceable treatment, all material evidence claims have been verified or demoted to hypotheses, no unresolved critical contradiction is presented as decided, opposing roles accept the same research plan, and independent QA accepts its provenance/scope. Unknown customer demand and final design choice remain explicitly pending.

Stop a debate round when additional disagreement can only be settled by a defined new observation or test; retain that question and its next evidence action. Do not run indefinitely or vote an empirical unknown into a fact. Reopen on contradictory customer behavior, a provider/schema change, a failing acceptance check, new owner direction or a new affected audience/context. A completed proposal is the starting point for the next evidence cycle, not permanent consensus.

## Customer and accessibility validation

The existing proposal of nine qualitative sessions across three audiences is a starting research round, not a representative survey or a proof of preference. Recruit by recent task and relevant context, and include disabled people, keyboard/assistive-technology users and real phone contexts. Obtain consent, minimize personal travel data, separate identifiers from observations and define retention/deletion before recording sessions. Do not contact or recruit people without the owner's authorization.

Use equivalent dated datasets and counterbalance the three directions; ask participants to explain local days, connection assumptions, unknown/partial results and what changed after an edit. Record independent completion, prompting, incorrect inference, recovery, time/actions and the participant's explanation. Do not give rankings merely from visual preference. Pilot the questions for leading language. Revise and retest critical misunderstanding before progressing.

W3C describes accessibility evaluation as requiring knowledgeable human judgment alongside tools. End-to-end conformance concerns complete pages/processes and accessibility-supported ways of using technologies; sampled contrast and target measurements are insufficient for a conformance claim. For Flyji, cover query, results, comparison, connection editing, errors, selection restoration and export, with map/globe-equivalent tasks, focus/status behavior, non-color cues, zoom/reflow, reduced motion and screen-reader checks. A local automated run can verify only its stated subset. [Evaluation overview](https://www.w3.org/WAI/test-evaluate/), [WCAG 2.2 conformance requirements](https://www.w3.org/TR/WCAG22/#conformance-reqs).

## Sources verified for this plan

All links below were opened or their specific supporting content located through the web tool on 13 October 2026. They provide methodology/standards, not evidence of Flyji customer demand.

| Source | Supported claim | Application/limitation |
|---|---|---|
| [GOV.UK: Learning about users and their needs](https://www.gov.uk/service-manual/user-research/start-by-learning-user-needs) | Research users' goals/context and current problems; suggestions from others remain assumptions; express the outcome instead of a solution | Method borrowed for Flyji; government-service context does not establish aviation priorities |
| [GOV.UK: Plan a round of user research](https://www.gov.uk/service-manual/user-research/plan-round-of-user-research) | Set actionable questions and assumptions before selecting methods and participants | Supports planned evidence cycles; no claimed universal session count |
| [GOV.UK: Analyse a research session](https://www.gov.uk/service-manual/user-research/analyse-a-research-session) | Distinguish raw observations, interpreted findings and actions; involve observers in analysis | Supports bias checks and transparent synthesis |
| [W3C: Evaluating Web Accessibility](https://www.w3.org/WAI/test-evaluate/) | No tool alone establishes accessibility; evaluate through development | Supports manual and user evaluation alongside automation |
| [W3C: WCAG 2.2](https://www.w3.org/TR/WCAG22/) | Testable criteria and complete-page/process conformance | Proposed evaluation target, not certification or legal advice |

## Initial audit disposition

`Pending synthesis review`. Baseline provenance is internally consistent in the inspected scope. Research demand, customer interpretation, final layout, provider coverage/rights and operating readiness remain unverified. No final sign-off is issued by this plan.
