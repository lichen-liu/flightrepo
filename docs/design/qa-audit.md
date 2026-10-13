# Flyji exploration audit

Review date: October 10, 2026. Status: first exploration round complete; production acceptance and final design agreement pending.

A later [map workspace revision](map-workspace.md) adds a larger map, camera/selection interactions and focused mobile views, with its own 117-check audit. This document and the source/screenshots below retain the first round's evidence. Its map-interaction and mobile-scroll limitations should be read as findings of that earlier version, not as claims about the revised prototype.

The owner confirmed travelers, enthusiasts, and researchers as the intended audiences. All proposed decisions remain in the [decision register](design-decisions.md). This audit is automated browser verification and the author's visual/self review. It is not an independent audit, customer research, or an accessibility conformance report.

## Evidence retained

[Browser results and source/screenshot hashes](qa/results.json) record **91 passed checks**, zero observed JavaScript runtime errors, source identity, and browser environment. [The check script](qa/check.cjs) drives the actual controls in the sandboxed preview. A tall capture viewport prevents the preview wrapper clipping screenshots; task and width checks still use the stated widths.

| Direction | Desktop capture | Mobile capture | Principal tradeoff |
|---|---|---|---|
| Journey board | [Desktop](qa/screenshots/board-desktop.png) | [Mobile](qa/screenshots/board-mobile.png) | Easy time comparison; little spatial discovery |
| Atlas workspace | [Desktop](qa/screenshots/atlas-desktop.png) | [Mobile](qa/screenshots/atlas-mobile.png) | Broad spatial context; more scrolling and label congestion |
| Globe studio | [Desktop](qa/screenshots/globe-desktop.png) | [Mobile](qa/screenshots/globe-mobile.png) | Strong global geometry; occlusion and camera effort |

[Dark appearance](qa/screenshots/globe-dark.png) was captured with reduced motion requested. Maps use embedded published country geometry and sourced airport coordinates. No aircraft-location feed is present.

## Checks and repairs

- All three task modes produce their own dated results: four journey options, seven network path options, and four outgoing service records in the initial fixture. Cancellation inclusion adds one inspectable record. Network path options are not a deduplicated destination or service-frequency summary.
- Direct filtering, selection, comparison pins, and filtered-out pins behave consistently. Selecting the Montréal connection shows 1h 15m waiting, two selected map legs, and the corresponding timeline.
- Event stepping identifies waiting at YUL, the onward AC 202 flight, then arrival at CDG. Calendar date and clock reference appear on the time axis and cursor. The cursor highlights loaded options without filtering them.
- An unsupported date produces a coverage message rather than a no-service assertion. A supported second date produces different dated journeys. Identical origin/destination raises a visible alert and leaves the last applied query intact.
- Search works by button and Enter in the sandbox. The first audit found native form submission was blocked there; the prototype now applies a validated query through the explicit action. Keyboard selection retains focus after result markup refresh.
- The three layouts fit viewport widths 320, 390, 736, and 1,024 pixels. Checks cover horizontal extent of representative controls/text as well as the app surface; screenshots receive visual inspection. At small widths, journey options precede the map.
- Sampled essential text in both appearances meets 4.5:1 contrast and is at least 11px. This samples headings, result text, query action, selection, status and cursor; it does not audit every state, non-text contrast or color perception.
- In a 390px coarse-pointer board view, visible buttons, selects, and summaries reach the 44px touch target aspiration. This does not verify every target in all variants on physical hardware.

The source and preview copy match. The prototype is under the inline size limit, has no product network requests, and has no automatically looping motion. The preview host may load its own utilities. Flight records are explicitly synthetic, timing fit is not called a safe/bookable connection, and source evidence is inspectable.

## Critical review and unresolved issues

| Issue | Impact | Required next evidence / change |
|---|---|---|
| Audience needs remain hypotheses | Attractive screens could solve the wrong task | Observe recent real tasks from all three audiences before prioritizing the backlog |
| No screen-reader, Safari/Firefox, physical-device or low-end performance audit | Browser checks establish only a bounded portion of usability | Independent accessibility review, browser/device matrix, frame-time and interaction measurements |
| No DST, date-line, long-haul or schedule-revision fixtures | Clock labels may look clear while edge cases are misunderstood | Fixtures for skipped/repeated local times, westbound date crossings and revised/cancelled selections |
| A selection excluded by a filter falls back to the first matching result | Could imply a saved itinerary silently changed | Before production, preserve stale selected evidence explicitly; pins already disclose exclusions |
| Task modes retain some inapplicable controls | Inspect mode ignores itinerary stop/wait rules; cancelled checkbox only affects inspection | Adapt control visibility and applied-constraint summary to task intent; test discoverability |
| Map/globe lacks zoom, drag, route picking and camera auto-fit | Rotation can hide a route; the Atlantic fixture does not test global density | Evaluate camera controls and selection with dense global fixtures and equivalent list actions |
| Mobile shows several sections in a long document | A user may miss comparison or evidence below the fold | Prototype a compact mobile view switch with preserved query/selection and test it |
| Variants preserve separate exploration state | Switching concepts is a comparison exercise, not a production view switch | Production views must share query, dated selection and snapshot |
| Arrival deadline, flexible calendar, via builder and period comparison are specified, not implemented | Those customer tasks cannot yet be validated end-to-end | Implement and test only the next agreed slice, then revisit its decision gates |
| Current backend has coverage, temporal horizon and bounded-ranking gaps | Real UI could make false empty/shortest-route claims | Resolve [serving prerequisites](server-support.md) before connecting this exploration to real responses |

These limitations prevent treating this round as final approval. No technical blocker prevents further exploration; owner review and representative customer evidence remain necessary for final decisions.

## Customer evaluation protocol

Begin with nine qualitative sessions: three participants per audience, including two primarily mobile users and at least one keyboard-only participant. This is a proposed starting round, not a statistically representative sample. Ask about a recent task before showing the interface; use the participant's actual constraint vocabulary to revise the need inventory.

Counterbalance the three directions' presentation order. Record task completion, prompting, misinterpretations, actions/undo, time to a defensible answer, and the participant's explanation. Do not use visual preference alone to select the default.

1. Traveler: find a direct Toronto–Paris option on 17 October, describe local departure/arrival dates, then compare it with the Montréal connection and explain waiting time. Explain whether the screen proves a protected transfer.
2. Enthusiast: discover destinations from Toronto, distinguish a direct leg from a connecting path, select one and explain its state at the time cursor. Attempt the same task without moving the globe.
3. Researcher: inspect dated service and a cancelled record, explain scheduled versus flown evidence, then query 19 October and describe what an empty result establishes. Discuss the period comparison/export needed in their recent task.
4. Each audience: change date while retaining a comparison; recover from an invalid query; try the relevant flow on mobile or with keyboard. Ask where the participant expected geography, time, and source evidence to appear.

Log participant consent and anonymized session ID, audience/task, source hash, viewport/input method, query, observed behavior, verbatim short observations with permission, affected N/D IDs, severity, repair, retest and decision. Address and retest each critical interpretation error before advancing. Small-sample success does not establish universal usability.

## Decision and implementation gates

For every D01–D10 decision, retain its need, alternatives, defense, evidence, dissent and owner acceptance/revision/rejection. The current recommendation is a shared task workspace: journey list/time first, network Atlas first, inspect evidence first, globe optional. Owner feedback can revise it.

Before implementing the real frontend, agree the first slice, its time semantics and trust wording. Then define the serving contract from those interactions; validate airport resolution, coverage, stable IDs, truncation and arrival constraints with semantic tests. Use the existing Python/FastAPI/SQLite boundary. Reaudit the live flow rather than transferring this fixture's pass count to production.

## Reproduction and source provenance

This audit used the bundled Visualize renderer, Playwright from the Codex workspace runtime, and an isolated headless Chrome profile. Node/Playwright are design QA tools; they have not been added to Flyji's deployment dependencies.

```sh
python3 "$VISUALIZE_SKILL/scripts/render.py" "$PWD/docs/design/flyji-exploration.html" /tmp/flyji-design-preview.html --force
FLYJI_QA_PREVIEW=/tmp/flyji-design-preview.html FLYJI_QA_OUTPUT=/path/to/writable/qa-output node docs/design/qa/check.cjs
```

Set `VISUALIZE_SKILL` to the installed skill directory and make Playwright available to Node (`NODE_PATH` can point to the bundled runtime). The output directory must already exist. The renderer adds the sandbox and variant navigation. The script selects variants for automation without adding product navigation code.

- D3 7.9.0: [pinned bundle](https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js), [ISC notice](third-party/d3-7.9.0-LICENSE.txt).
- Country geometry: [@d3-maps/atlas 1.0.0](https://esm.sh/@d3-maps/atlas@1.0.0/world/countries/countries-110m), [MIT notice](third-party/atlas-1.0.0-LICENSE.txt), [package metadata](third-party/atlas-package.json). Converted from TopoJSON with topojson-client 3.1.0; no outlines were hand-drawn.
- Airport coordinates/names: [OurAirports public-domain data](https://ourairports.com/data/), retrieved October 10; retained in [airport reference](airport-reference.json). IANA zones were assigned for the seven fixture airports and are not supplied by that CSV.
- Synthetic flights: authored solely for these interactions, limited to October 17–18, 2026. Commercial-looking flight numbers and times are illustrative and do not assert actual service.

License notices are also retained inside the offline source. Production reference-data accuracy, licensing, coverage and update policy remain separate work.
