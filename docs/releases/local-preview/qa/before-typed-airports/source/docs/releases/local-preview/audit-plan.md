# Local preview release audit

13 October 2026 · independent reviewer `/root/qa_auditor` · initial plan and findings; final acceptance pending current-version verification.

The release under review is a local Flyji preview: Python/FastAPI, project-local SQLite, separate internal ingestion/admin and read-only serving, plus the bundled customer workspace and retained UX research. A green light can establish that this reviewed build is fit for a local demo and reviewable PR. It cannot establish a customer-approved final design, all 84 research questions implemented, provider completeness, booking/transfer suitability, accessibility conformance or public production readiness.

The R09 research-plan signoff applies to its frozen manifest, not later implementation bytes. The R07/R08 records remain historical bounded evidence. This audit will use fresh source identities and checks after repairs; an earlier pass count will not be transferred to the release.

## Acceptance gates

| Gate | Required evidence | Green-light scope |
|---|---|---|
| A01 Scope and claims | Implemented/deferred/pending table, exact entry/demo commands, customer/layout/public gates explicit | Honest local milestone; no whole-product or all-needs completion claim |
| A02 Package boundaries | Current source inspection and boundary checks; core independent; ingestion/server separated; admin deliberately integrates both | Current module organization is reviewable and future separation preserved |
| A03 Data and admin | Ignored local DB/sidecars, no normal-data reset by demo, whole-batch import validation, guarded reset, friendly bad-input failures | Local data management works without hidden mutation/data-loss paths |
| A04 Routing and serving | Backend review plus current semantic/oracle/snapshot/limit/replay tests; HTTP errors/normalization/read-only boundaries | Exact tested schedule-feasibility behavior with explicit limits |
| A05 Customer workspace | Frontend review plus current browser, keyboard/touch/reflow, temporal/identity/recovery and error checks; actual captures | Local demo usability/mechanics in reviewed environments; human/customer acceptance remains open |
| A06 Packaging and demo | Built-wheel byte comparison and outside-checkout probe; synthetic isolated one-command demo; asset licenses | Current bundled installation/demo works within stated configuration |
| A07 Reproducible evidence | Fresh test outputs, environment and source hashes, preserved failures/repairs, independent reviewer dispositions | Results apply to the reviewed bytes, with limitations retained |
| A08 CI and PR | Valid workflow and required-check name/policy; actual current-head PR run when available; no bypass of required checks | Local green light may precede remote CI; merge acceptance requires the configured current-head result |
| A09 Repository hygiene | Clear current README/docs index, ignored generated data, no unrelated/secrets/output changes, preserved frozen audit records | Focused reviewable diff and durable evidence without destructive cleanup |

Final receipts should identify reviewer/role, time, manifest/hash or exact source set, performed method, resolved/unresolved findings and scope. Plain agent attestations must not be presented as human signatures. Any substantive code/test/doc repair after review invalidates the affected acceptance until checked again.

## Initial inspection

The reviewer inspected the current package tree, API, configuration, ingestion/CSV adapter, updater/schema, admin client/reset/demo helpers, package verifier, dependency-boundary tests, import tests, README and saved CI/ruleset policy. No code was edited by this reviewer. Other agents are reviewing backend and frontend semantics, so their actual current-version reports are required before final acceptance.

### Findings requiring repair or a clear scoped disposition

| ID | Finding | Consequence and acceptance evidence |
|---|---|---|
| L01 | Admin import shape errors leak `TypeError`: top-level JSON `null` in `import-json` and `transfers: null` in `import-airports` are outside `main`'s ValueError/OSError handler | Independently reproduced with temporary DB/files. Reject invalid container shapes with a readable error, preserve existing facts, test CLI exit behavior and complete-batch safety. |
| L02 | `scripts/reset_local_data.py` invokes `run` directly, so refusal/errors can emit an uncaught traceback instead of the client's friendly failure path | Keep the existing project-`var` guard and explicit `--yes`; make helper errors legible and test missing confirmation/outside-path behavior without deleting normal data. |
| L03 | Public serving bounds exist, but admin flight/route limits only require positivity and can load arbitrarily large results; several GET airport input strings also have looser normalization than typed POST queries | Establish intentional internal bounds/normalization or document the exact scope; valid documented examples and bad inputs should have consistent behavior. An internal full export is a different deliberate action. |
| L04 | Settings and `.env.example` advertise unused `admin_token=change-me`, though no API authentication consumes it | Remove stale configuration or plainly state the actual local access boundary. Do not imply a configured token secures the API. Hosted access controls remain future work. |
| L05 | AeroDataBox's source comment claims twelve-hour windows work across all current paid tiers without retained source-contract evidence | Remove/demote the temporal commercial claim. No provider account/call or exhaustive/rights signoff is needed for a synthetic local preview. |
| L06 | Current CI runs Python 3.13 pytest only; installed-wheel/browser/research checks are local evidence, and `requires-python >=3.11` exceeds the locally tested version coverage | Keep the owner-approved label-trigger/current-head/main/linear-history policy and required check identity. Add meaningful durable CI coverage where appropriate or state the verified environments and pending remote checks. Do not claim every declared Python version independently verified. |
| L07 | README foregrounds many chronological prototype rounds before the current task/demo and omits the latest research index | Lead with the current local preview, quick demo and accurate feature/gap table; place history behind a clear index. Preserve frozen audit bytes and the owner's unanswered final-layout decision. |

L01 reproduction used `TemporaryDirectory` plus patched settings, so no normal or demo flight records were changed. `client.run` raised `TypeError` for both examples; neither exception belongs to the current `main` handler's accepted tuple. These are input-quality failures, not evidence that valid batches are partially committed.

### Existing boundaries inspected

The API currently exposes read-only query/calculation endpoints; ingestion/reset/delete remain internal CLI actions. SQLite connections use read-only/query-only mode in the reader and foreign keys in both roles. The updater enables WAL and performs transactionally validated airport/transfer updates; reset deletes tables' data transactionally instead of unlinking a live DB. The demo selects a separate ignored DB and binds loopback. A packaged installation still needs the documented explicit absolute database path; the repository-anchored default is an editable-checkout behavior.

The wheel verifier checks package-file bytes and imports the extracted wheel outside the checkout with an explicit DB. That is valuable evidence, but its historical R08 success does not cover later changes. No provider coverage/licensing or published commercial transfer requirement was independently verified here.

## Historical evidence and size

Initial filesystem inventory: about 53 MiB under `docs/`; `docs/design` contains 208 files and 48,974,373 bytes, including repeated retained HTML revisions of roughly 830 KiB each. `docs/product` contains 83 files and 4,912,062 bytes. The retained revisions/screenshots explain earlier reviews and source identities; size alone does not justify deleting proof. The wheel package targets `src`, and pytest's configured test path is `tests`, so archived sources should not become runtime code or accidentally collected tests.

Cleanup should remove/ignore newly generated caches and disposable working outputs, consolidate navigation and identify current versus historical material. Do not alter frozen R09 manifests/receipts or R07/R08 source archives to make current hashes appear unchanged. If a future archive is moved or excluded from Git, explicitly record its availability and update affected links without pretending the original record moved unchanged.

## CI source verification

The configured `actions/checkout@v7` and `actions/setup-python@v7` tags exist in their official repositories as checked on 13 October 2026. No nonexistent-action blocker was found. This does not certify the workflow's actual run, supply-chain immutability or live repository settings. [Checkout release](https://github.com/actions/checkout/releases/tag/v7), [Setup Python release](https://github.com/actions/setup-python/releases/tag/v7).

## Current disposition

**Pending repairs and final current-version review.** The research plan is already accepted in its own exact scope; local implementation/demo release acceptance remains conditional on A01–A09, especially the independent backend/frontend reports and fresh test/package evidence. Owner/customer design, accessibility conformance, real-data trust and public readiness remain open even after a local preview green light.
