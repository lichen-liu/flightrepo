# Flyji map workspace revision

Date: October 10, 2026. Status: working exploration, proposed design. The owner requested further improvement and more visuals, including a map. That authorizes this experiment; it does not approve the final default or validate customer demand.

`flyji-map-workspace.html` contains two alternatives: Atlas explorer and Globe explorer. The first round's three alternatives and 91-check audit remain unchanged. All flight records are illustrative; country geography and airport coordinates use the same sourced references and retained licenses as the first round.

## What changed and why

| Customer action | Implemented exploration | Defense and tradeoff |
|---|---|---|
| Understand where options go (N01/N07) | A large 2D map, selected legs emphasized, context legs quieter; Earth is an alternative projection | Geography gets room to explain the network. Map-first opening remains a hypothesis; precise time decisions still need readable options. |
| Explore an airport (N01/N06/N13) | Selectable airport buttons with full name, zone, cursor-local time and matching-query count; destination and avoid-hub actions | Airport inspection becomes useful to the query. Counts describe dated options in loaded data, not total airport frequency. |
| Pick a complete option (N07/N08) | Click a visible route; one matching dated option selects directly, multiple matches expose complete journey choices | A geographic edge is not a unique itinerary. Explicit choices prevent choosing a direct London trip when the person wants to continue to Paris. |
| Orient the geography (N07/N17) | Fit network, fit journey, world framing, zoom, mouse drag and native directional controls | Fitting reduces camera work. Native controls provide keyboard/touch alternatives. Dense global networks still require testing. |
| Understand time and connections (N04/N05/N14) | A map-local schedule cursor synchronized with comparison lanes; next-event stepping; a hollow wait ring distinct from a flight dot | Scrubbing shows journey phase while the timeline retains elapsed-time evidence. Positions are schedule illustrations, not live tracking. |
| Change perspective without losing work (N08/N14/N17) | Map/Earth switching retains applied query, dated selection, comparison pins and time | Projection changes presentation, not search semantics. The two carousel alternatives retain separate review states. |
| Explore on a phone (N17) | Compact applied-query summary; Edit search; focused Map, Options, Time and Details views | The map is reachable without a long filter/results stack. Hiding other panels increases navigation cost, which user tasks must evaluate. |
| Understand and undo a map action (N06/N15) | Removable applied-constraint chips remain visible outside the collapsed editor | Excluding a hub must not silently change the results. The displayed constraint and a direct undo make the consequence reviewable. |

The Atlas layout places geography above aligned time comparison and evidence, then readable option cards. Globe explorer tests the sphere beside evidence. The map's amber route identity matches selected time lanes and cards. Non-selected routes use restrained neutral lines; no automatic spin or playback competes with the task.

Airport buttons retain 44px targets and leader lines to their true coordinates. Label positions are resolved separately from airport coordinates so adjacent airports can remain selectable. Airport inspection and timeline selection retain keyboard focus after drawing updates. The list supplies the equivalent dated choices without requiring pointer selection of paths.

Touch scrolling remains native. Native zoom and directional controls supply camera movement; pinch-to-zoom and touch-drag are not implemented. “Fit” resets camera presentation without resetting the search. Optional Earth rotation can hide backside routes, so options and time remain available.

## Evaluation tasks

1. Select Montréal, inspect its clock, exclude it as a connection, and remove the visible exclusion. Explain how the query changed.
2. In Discover routes, select Toronto–London and choose the complete Toronto–London–Paris journey. Explain why the line offers more than one choice.
3. Select the Montréal connection and scrub into its waiting interval. Describe which airport's local clock you are reading and whether the position is live.
4. Fit the journey, zoom, switch Map/Earth, then use mobile Options/Time/Details. Verify that query, selected journey and cursor remain the same.
5. Complete a precise departure/arrival task using Options and Time without manipulating the map; compare the effort with the first round.

These tasks extend the [qualitative evaluation protocol](qa-audit.md#customer-evaluation-protocol). No participant sessions have been conducted. Decisions D11 and D12 in the [register](design-decisions.md) remain proposed; this revision does not override D01's unresolved default decision.

## Backend implications

The new actions require airport metadata and explicit dated-flight/itinerary membership from the same query snapshot. Destination/avoid actions remain reader queries. Camera and cursor changes stay in the browser and need no extra services or per-frame API requests. See [map interaction requirements](server-support.md#map-interaction-requirements).

The source remains a synthetic interaction prototype. FastAPI, ingestion, SQLite and production endpoints have not changed. Real-data integration still depends on coverage, stable identities, local-day semantics, temporal search corrections and honest completeness from the first audit.

## Revision audit

[Browser results](qa/map-results.json) retain **117 passed checks**, zero observed JavaScript runtime errors, source/script hashes, screenshot hashes and the environment. The [check script](qa/map-check.cjs) exercises actual controls and clicks projected route positions. This revision's evidence is separate from the first round.

| Direction | Desktop | Mobile |
|---|---|---|
| Atlas explorer | [Capture](qa/map-screenshots/atlas-desktop.png) | [Capture](qa/map-screenshots/atlas-mobile.png) |
| Globe explorer | [Capture](qa/map-screenshots/globe-desktop.png) | [Capture](qa/map-screenshots/globe-mobile.png) |

[Dark Earth capture](qa/map-screenshots/globe-dark.png) uses reduced-motion media settings. Author visual review covered these captures. The source is 788,270 bytes, below the inline limit, and matches the shown copy. No production network/data service has been added.

Verified behavior includes airport inspection and focus, avoid-hub action and visible undo, unique/shared route picking, linked cursors and wait-ring phase, camera controls, projection/selection continuity, mobile panel switching and preserved selected evidence beside comparison pins. Existing dated query, cancellation, coverage, invalid-query and comparison behaviors were rerun in both alternatives. Responsive checks cover 320, 390, 736 and 1,024px; airport target boxes do not overlap at those widths. Sampled essential text meets 4.5:1 contrast in both appearances. A 390px coarse-pointer Atlas check verifies visible controls at the 44px target aspiration and keyboard operation of native directional buttons; this is not every state or physical hardware.

Repairs retained in this revision:

- Overlapping transparent route hit areas could give the last-drawn path precedence. Selection now resolves against the nearest visible curve with a bounded tolerance. A shared physical leg still requires an explicit dated journey choice. Per-path hover labels were removed because drawing-order hover could disagree with the selected nearest curve. Dense near-coincident curves remain a future fixture requirement.
- A selected journey outside the pinned set now remains visible in the timeline. Keyboard selection in that timeline retains focus after updates.
- Airport-applied filters remain visible and removable when the query editor is closed. Phone instructions name the available native camera controls; they do not suggest unsupported touch drag.
- The search editor collapses to an applied-query summary on mobile, panels switch locally, and minimum page height no longer leaves artificial blank space in shorter views.
- Pointer QA uses the SVG screen transform and a frame-aware click position. Raw top-page coordinates were unreliable after scrolling the sandbox preview; the script now waits for the expected shared-leg choice, rather than accepting a stale panel with the same option count.

The audit is author QA, not independent review, customer research or accessibility conformance. Acceptance still requires real devices, broader browsers, screen-reader review, dense global fixtures, DST/date-line cases, schedule revisions and measured performance. The first audit's backend prerequisites, excluded-selection fallback and inapplicable task controls remain unresolved; improving the map does not resolve those semantics.

Reproduce with the same installed renderer and an available Playwright runtime:

```sh
python3 "$VISUALIZE_SKILL/scripts/render.py" "$PWD/docs/design/flyji-map-workspace.html" /tmp/flyji-map-preview.html --force
FLYJI_QA_PREVIEW=/tmp/flyji-map-preview.html FLYJI_QA_OUTPUT=/path/to/writable/qa-output node docs/design/qa/map-check.cjs
```

The output directory must exist. `NODE_PATH` can point to the bundled Node dependency directory. Chrome is the default isolated headless browser; `FLYJI_QA_BROWSER` can select another installed Playwright channel. These tools are design QA dependencies, not Flyji deployment dependencies.
