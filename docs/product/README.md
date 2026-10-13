# Flyji product iteration

R08 · 13 October 2026 · local web workspace · `routing-3`

Flyji now has a working browser experience served by the same FastAPI process that queries SQLite. Search, route cards, globe, schedule and connection editing use the actual shared routing engine. The earlier design alternatives remain available for comparison; a final visual direction and customer validation remain open.

## Try it

```sh
.venv/bin/python scripts/run_demo.py --port 8013
```

Open `http://127.0.0.1:8013/`. The helper seeds the separate ignored `var/connections-demo.db`, uses loopback only, and preserves normal flight data. If the port is occupied, choose a different `--port`. Stop the helper with Ctrl+C.

1. Choose a journey through LHR and AMS.
2. Select **Change connection** after the first leg.
3. Choose **Any time before arrival deadline** and allow a same-city airport change.
4. Find next flights and apply **U2 8002** from LGW on 19 October. The AMS–CDG leg moves to the next day too.
5. Try Undo, UTC leg times, map/globe switching, the schedule cursor, and JSON export. On a phone, use Options, Journey and Map.

The demo contains twelve synthetic flight instances and explicitly assumed transfer allowances. No provider API call or account is required.

For normal imported data:

```sh
.venv/bin/python -m admin stats
.venv/bin/uvicorn server.api:app --host 127.0.0.1 --port 8002
```

The browser reads the configured database; it does not insert fixture data. For a packaged installation, set `FLIGHT_DATABASE_PATH` to the intended absolute local DB path. Initialize it through the internal client before serving. Keep project data and exports under ignored `var/`.

## Customer tasks implemented

| Task | Behavior | Evidence |
|---|---|---|
| Search a dated trip | Exact airports, local departure day or inclusive date range, destination-local arrival deadline, stops, wait bounds, avoidance and ranking | HTTP integration, independent routing oracles inherited from R07, browser constraints test |
| Understand the timing | Full date/offset on every leg, elapsed time, waits, one UTC timeline and a schedule cursor | UTC/local switch preserves dated identities and cursor; timeline labels inspected on phone and desktop |
| Explore spatially | Optional globe/flat map, great-circle flight lines, distinct airport changes, leg focus and camera recovery | 177-country published basemap, responsive projection, map failure recovery |
| Customize a connection | Keep exact prefix, permit short/overnight/any-later/not-before windows, choose an exact flight and rebuild later legs | Real SQLite/API overnight edit, directed airport allowance, avoidance preservation, stale-choice failure and undo |
| Retain evidence | Search URL, selected dated records in JSON, source statuses/refresh times, snapshot and bounded trace | Browser export/restore; actual admin snapshot replay |
| Import/manage local facts | JSON and CSV flights, airport/transfer catalog import/export/query, isolated reset | CSV fidelity and whole-batch failure tests, CLI catalog roundtrip |

“Finished search” describes the loaded snapshot. Provider coverage, inventory, protected tickets, immigration, baggage and published commercial transfer requirements remain unverified. The map cursor illustrates a schedule on a great-circle route; it is not measured aircraft tracking.

## Architecture and defended decisions

- **One Python process plus SQLite.** Static HTML/CSS/ES modules are bundled under `src/server/web/`; no frontend runtime service or build server is required. Browser requests are same-origin. Locally vendored geography/D3 retain licenses; compressed assets work without external runtime requests.
- **Server-owned clock interpretation.** `/v1/itineraries/search/local` resolves origin-local departure midnights and destination-local deadlines with IANA zones, then invokes the UTC engine. A DST day can contain 23 or 25 hours. Missing/repeated wall clocks are rejected or require an explicit fold. The UI sends expected zone references; a changed reference returns 422 instead of silently reinterpreting the dates.
- **Facts and metadata from one read.** Search/rebuild return the airport metadata used by that calculation. Cards, leg clocks and map coordinates use the relevant response. Saved undo selections keep their old facts/metadata and are labeled as restored copies. Local normalization and fact loading share a SQLite transaction snapshot.
- **Explicit edits.** Finding options previews a continuation. Applying an exact choice rechecks it against current facts. It preserves earlier identities and recalculates later legs. A rejected choice leaves the previous journey visible. Avoided airports remain active during the edit. A valid dead-end choice can be retained with an incomplete-plan message.
- **Task views on phones.** A tall desktop layout made the selected journey/map hard to reach at 390px. The revised phone views expose Options, Journey and Map directly. Desktop keeps the linked workspace. The final overall layout direction is still subject to customer observation and owner agreement.
- **Separate presentation and transport state.** Clock/camera/cursor changes use returned facts locally. Latest-response guards protect both search and editor request lanes even if cancellation arrives late. Error and partial states preserve the meaning of older visible results.
- **Strict request contracts.** Unknown constraint fields now return 422. `routing-3` adds rebuild avoidance, response airport snapshots and strict inputs. Older engine replay files are rejected explicitly; preserved R07 source remains available for reproducing that engine.
- **Import fidelity.** CSV dates are filtered after UTC normalization. Supplied actual timestamps, codeshares and `fetched_at` are retained. A missing refresh value records the import time, not evidence of upstream freshness. The CSV namespace and existing ID hashing remain unchanged. Invalid records prevent the whole ingest batch from being written.

## New serving contracts

| Endpoint | Purpose |
|---|---|
| `GET /` and `/assets/*` | Bundled responsive customer workspace |
| `GET /v1/catalog` | Bounded airport picker plus stored-record counts, date extents and oldest/newest fact refresh; provider coverage explicitly `unknown` |
| `POST /v1/itineraries/search/local` | Local-date input, optional expected origin/destination zones, normalized UTC query/result and response airport facts |
| `POST /v1/time/resolve` | Exact airport-local wall clock to UTC, optional fold and expected zone |
| `GET /health` | Process liveness |
| `GET /health/ready` | Checks readable initialized data; empty DB is ready, inaccessible/missing schema returns 503 |

UTC search and rebuild retain their existing paths and richer diagnostics. Response airport metadata is additive. The web catalog includes observed airport codes without metadata; UTC querying remains possible, and missing coordinates are identified instead of fabricated. The picker is capped at 1,000 entries and exposes truncation; larger catalogs need a live lookup interaction.

## Verification and iterations

[QA record](qa/final.json), [Python output](qa/python-tests.log), [browser checks](qa/browser-results.json), [concurrent HTTP probe](qa/api-load.json), [wheel verification](qa/package-verification.json) and final source under `qa/revisions/final/` are retained. This is author QA, not independent audit or customer research.

| Pass | Finding | Change/verification |
|---|---|---|
| Integration | Search parameter abbreviations did not match the UTC engine's fields; rebuild had no avoidance input | Unified field names, forbid unknown constraints, preserve avoidance through reader/policy/explicit choices; regression tests |
| First browser pass | 26 checks passed, including actual overnight edit and undo. Visual review found inline route text, clipped first timeline tick, and excessive phone scrolling | Separate route/time lines; inset time labels; direct phone task views. Initial captures are retained as observations; their exact source revision was not archived |
| Performance/UI refinement | Cursor scrubbing rebuilt all projected geography; display-clock changes reset its position | Reuse basemap elements when only time changes; preserve cursor/camera on clock changes; browser assertions |
| Snapshot review | Separately loaded metadata could differ from the routing snapshot, and displayed input zones could become stale | Include response airport facts; reject changed zone preconditions; real WAL metadata-change test plus injected browser mismatch/retry |
| Import review | CSV used source-offset calendar dates and dropped optional facts; direct service inputs were less strict than the client | UTC filtering, preserved optional fields, validation before provider/storage access, complete-batch tests |
| Final | 149 Python tests, 65 browser checks, zero JS runtime errors | Keyboard edit/apply/undo, 320–1510px fits, touch targets, day/overnight/after windows, actual limits, export, URL restoration, fault recovery and same-origin runtime |

The browser harness also needed two corrections: a globe's first country can legitimately be clipped behind the sphere, and test selectors must distinguish the search form from the connection form. Those failed harness records are retained separately. The initial time-resolver output was made a typed response for an explicit OpenAPI contract and consistent UTC serialization.

The local concurrent probe completed 64 requests with 8 clients, 0 failures and one identical complete-response fingerprint: median **19.32ms**, p95 **23.58ms** in this run. It used twelve synthetic flights, one process and no active writer; it does not establish production capacity. Existing separate-process WAL and routing-snapshot tests cover writer/read consistency. The [R08 core benchmark](qa/routing-benchmark.json) retains six bounded synthetic cases and source/environment hashes; the inherited combinatorial search limits remain explicit.

The built wheel was verified byte-for-byte against all package files, then imported from an extracted installation outside the checkout. Its root page, static assets, readiness and local search passed with an explicit database path. Local QA used Python 3.14; GitHub CI targets 3.13. No remote CI, public deployment or final design approval is claimed.

## Next product requirements

| Priority | Required result | Evidence needed before calling it complete |
|---|---|---|
| 1 | Distinguish stale, failed, missing and deliberately unfetched data | Persist ingestion-run/window evidence separately from flight rows; validate provider endpoint completeness, retention and rights. A successful fetch must not be relabeled as exhaustive coverage without support |
| 2 | Handle realistic provider revisions and physical movement identity | Recorded source-backed fixtures for status/time changes, codeshares, duplicate movements, missing times and cancellations; exact-selection invalidation and replay checks |
| 3 | Validate the experience for all three audiences | Observe real traveler, enthusiast and researcher tasks; include phone and keyboard users; refine date/network/service modes against task success, then record owner agreement on layout |
| 4 | Extend journey constraints from observed tasks | Per-gap stopover/lock policies, via-airport requirements, larger airport lookup and continuation paging; preserve original facts and explicit partial semantics |
| 5 | Establish operating limits for a hosted pilot | Representative network density, measured concurrency/memory/time budgets, bounded ingress and failure recovery, freshness/backup behavior; choose access controls and quotas for the intended audience |

The current milestone is a usable local product workflow. A public launch still needs the evidence above, especially provider coverage and customer validation. The intended product name/domain remain **Flyji** and **fly.boboji.fyi**.
