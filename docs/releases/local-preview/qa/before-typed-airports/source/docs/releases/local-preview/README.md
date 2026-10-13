# Flyji local preview

This milestone brings the design exploration into a working local flight workspace: search dated options, inspect geography and schedule, change a connection, and recalculate later legs. It runs from one Python application and a local SQLite file. The [demo walkthrough](demo.md) covers the main interaction.

The release decision and reviewer dispositions are recorded in `signoff.json` and `reviews/`. They apply to the source set in `manifest.json`. The audit covers the investigation, design rationale and implementation as a local preview suitable for a demo and PR review. Customer acceptance, choice of a final visual direction and public launch are separate decisions.

## Evidence

| Check | Result | Record |
|---|---|---|
| Python suite | 182 passed on Python 3.14.7; one upstream Starlette deprecation warning | [Test output](qa/pytest.txt) |
| Served browser flows | 76 checks; no JavaScript runtime errors; local assets only | [Results](qa/browser/results.json) |
| Package | All 42 package files match the wheel; API/assets/search run outside the checkout | [Wheel probe](qa/package.json) |
| Concurrent reads | 64 requests, eight clients, no failures; one stable response fingerprint | [HTTP probe](qa/api-load.json) |
| Routing performance | Six synthetic cases, including bounded partial search and 30,000 unreachable records | [Benchmarks](qa/routing-benchmark.json) |
| Research integrity | R09 register and three exact-manifest reviewer receipts pass | [Research review](../../research/2026-10-13/verification.json) |
| Style and hygiene | Ruff checks/format and Git whitespace check pass; local data remains ignored | [Environment and check record](qa/checks.json) |

Browser evidence includes keyboard interaction, touch targets, reflow at 320–1510 pixels, failed storage/map recovery, timezone changes, partial search, stale responses, retained flight identity and undo. The routing tests include independent exhaustive comparisons, bounded diagnostics, DST and separate-process SQLite snapshot behavior.

The concurrency and benchmark results are synthetic local measurements, not production capacity claims. The initial HTTP probe was blocked before sending a request by sandbox socket permissions; the permitted rerun passed. That environment failure is [retained](qa/environment-failure.json).

## Changes from the reviewed prototype

The final review repaired malformed-input validation, UTC overflow and replay errors; invalid imports now fail before creating a database. The reset helper reports refusals cleanly. Next-flight clocks and map metadata use the calculation snapshot, missing metadata stays unknown, and ground changes no longer imply an observed location. Full timestamps include the year; camera buttons support keyboard and touch. Current code and documentation were cleaned up without changing the frozen research and earlier QA records.

CI retains label-triggered PR checks, automatic `main` checks and the required `pytest (Python 3.13)` identity. It now also tests Python 3.11, verifies the wheel and research receipts, and exercises the demo in Chromium. The current-head remote run is attached to the PR; this local evidence does not substitute for it.

## Remaining work

The [research register](../../research/README.md) contains hypotheses and planned customer tasks, not completed customer sessions. All three visual directions remain available for comparison. Multiple pinned options, protected commitments and further flight exploration controls remain proposed. Provider completeness, physical movement identity, commercial transfer rules, booking suitability, accessibility conformance and public access controls require further evidence before a public product release.

Historical R06–R09 prototypes, screenshots and receipts are preserved for traceability. They account for most repository size and are excluded from the runtime wheel. The current source manifest deliberately does not make old passes certify new code. Reviewer receipts are agent attestations with recorded methods and hashes, not human signatures.
