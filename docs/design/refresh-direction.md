# Refreshing Flyji: visual direction and UX philosophy

R04 · 11 October 2026 · proposed directions, pending owner review and customer observation.

The owner requested a refreshing UI and UX philosophy, richer graphical presentation, and a more rigorous workflow. This round tests how visual hierarchy changes exploration on a shared journey task. It does not choose a final product default or replace the earlier interaction families.

## The proposed philosophy: explore freely, then get precise

Start with the person's question and make the consequences of each choice visible. Curiosity should lead to a readable decision, with enough evidence to explain it. The spatial representation and the local day are equally important; a beautiful line across the ocean is insufficient if the arrival date is ambiguous.

| Principle | Concrete interaction | Strongest objection / evidence needed |
|---|---|---|
| Invitation before machinery | A clear route, a departure day and optional arrival deadline lead the page; connection exclusions are disclosed progressively | Editorial language can consume space or feel vague. Observe how quickly each audience finds the first useful action. |
| One selection connects the representations | Selecting an exact dated journey updates its geographic path, time lane, local clocks and flight evidence | Consistent color alone is insufficient. Names, patterns and explicit selected/compared labels remain necessary. |
| Time should resemble a real day | Local departure and arrival dates sit beside a shared UTC comparison; a cursor shows flying, waiting, not departed or arrived | A UTC axis requires explanation. Test whether users confuse a schedule cursor with an arrival constraint. |
| Exploration is reversible | Filters retain excluded selected and compared evidence; Undo restores the last constraint; Restore its query is explicit | Retained evidence could look like a match. Its exclusion must be visible and understood. |
| Depth should serve orientation | Actual country geometry, sourced airport coordinates, great-circle paths, route emphasis and a spherical Earth give spatial context | Glow, curvature and occlusion can conceal relationships. No animation or Earth gesture is required for a complete answer. |
| Confidence follows evidence | Loaded counts, complete/partial/unknown coverage, scheduled basis and unknown commercial details remain readable | “Complete fixture” is not commercial exhaustiveness. Real provider coverage and engine limits still need a contract. |

Progressive disclosure draws on [NN/g's guidance](https://www.nngroup.com/articles/progressive-disclosure/). Reflow, target sizing and optional motion draw on [W3C reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html), [target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html), and [animation from interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html). These are design inputs, not customer evidence or a conformance claim.

## Three directions, three different emphases

| Direction | Visual and interaction treatment | What it helps someone decide | Defense | Cost / rejection condition |
|---|---|---|---|---|
| Airlight | Cool open surfaces, confident large type, blue selected route, coral comparison, a large Atlantic atlas before the options | Understand the geography, then compare exact journeys and arrival consequences | Geography is visible without managing a camera; a distinct selected identity links the views | It pushes precise time below the map. Reject as universal default if time-sensitive tasks become slower or less accurately understood. |
| Journey canvas | Warm paper, serif invitation, botanical route identity, visible departure-day choices, common-clock comparison before geography | Choose a day and trade a departure time against arrival time or a connection wait | The first graphic answers a real temporal decision. The atlas remains available for detour/context | Seven days are a fixture slice, not a complete month. Test date-density, long mobile flows and whether the softer treatment obscures research precision. |
| Orbit studio | More spatial depth, blue atmosphere, a spherical geographic Earth, dated choices beside it, camera controls disclosed locally | Understand a route's shape on Earth while preserving the complete dated journey | Globe exploration can be immersive without becoming live-tracking theatre; equivalent choices and timing remain readable | Backside occlusion, lower spatial density and rendering cost. Keep optional unless task benefit is demonstrated. |

All three use the same query, selected journey, comparison, cursor and fixture. Switching direction changes emphasis, not the selected evidence. Camera state belongs to each direction. A shared physical route offers complete dated choices rather than silently identifying one flight.

## Working slice and honest boundaries

`flyji-refresh.html` uses the final R03 synthetic fixture: fourteen loaded complete journey options across 17–23 October, with five choices on the initial date, including two seven-hour nonstops with three hours' difference in arrival. There are 21 individual scheduled flight records. The departure cohorts, connecting gaps and coverage states match R03; country geometry and airport coordinates come from the preserved R02 sources.

The fixed demo route is Toronto Pearson (YYZ) to Paris Charles de Gaulle (CDG). Departure date, nonstop constraint, connection exclusion and next-day Paris deadline are functional. Date counts reflect those constraints. It supports one selected journey and one comparison, exact dated evidence, local clocks, proportional flights/waits, schedule scrubbing, event stepping, airport inspection, hub exclusion/undo, nearest-route picking, camera movement/zoom and restorable local state. Selected and compared schedules use circle/diamond markers; shared legs preserve both line identities. Mobile Map/Options/Time views keep a selected local-day summary visible. It does not imply that an origin/destination search, fare query or booking flow has been implemented.

The Paris deadline is restricted to next-day 05:00 or 07:00 on these October fixture dates, where Paris is UTC+2. This is a deliberately bounded fixture conversion, not a general DST-safe date/time parser. Scheduled dots interpolate great circles by elapsed scheduled time; they are not observed aircraft locations, actual flight routing, altitude or live tracking.

## Enhanced workflow for this campaign

1. Attach the same task and fixture to competing visual treatments: B02, B03, B04, B05, B06, B17–B21, B34 and B36.
2. Make the decisive interactions work before discussing taste: select, compare, change date/deadline, inspect a shared line, retain an excluded flight, and undo.
3. Obtain a first passing browser baseline, with source, test script, environment, screenshots and hashes.
4. Critique the actual captures and interactions for spatial ambiguity, visual hierarchy, mobile effort, focus, contrast and unnecessary visual weight. Record author findings separately from customer or independent review.
5. Repair the specific weaknesses, extend the checks where the baseline missed them, and retain both baseline and final evidence.
6. Compare these same tasks with people from all three audiences, counterbalance presentation order, and record errors, prompting, recovery and visual preference separately. Measurements remain blank until observed.
7. Ask the owner to accept/revise/defer concrete decisions and versions. Then derive the reader contract from the accepted interaction semantics.

No scores, participant observations, independent review, statistical preference or final approval are inferred from screenshots or a check count. This campaign supplements the twelve-family program; F08, F09, F11 and F12 remain open experiments.

## QA and critique

The final author browser run passes **165 checks with zero JavaScript errors**. All seven final captures were inspected. Verification used headless Chrome 154.0.8037.98 on Darwin 27.0.0, widths 320, 390, 736 and 1024px, 1100px desktop captures, and a separate 390px coarse-pointer/touch context. This is bounded author QA, not customer validation, an independent audit, or complete accessibility conformance.

| Version | Evidence retained | Checks | Source SHA-256 |
|---|---|---|---|
| R04a: first passing baseline | `qa/revisions/refresh-r04a.html`, `refresh-r04a-check.cjs`; `qa/refresh-initial-results.json`; `qa/refresh-initial-screenshots/` | 121 | `9350aa165824334eb88307b77fe2c712ec3dc988ff01aac05ffea2347ab9f057` |
| R04b: comparison, mobile, focus and rendering repairs | `qa/revisions/refresh-r04b.html`, `refresh-r04b-check.cjs`; `qa/refresh-interim-results.json`; `qa/refresh-interim-screenshots/` | 153 | `2d10ddeaa7e3a9ac95c353c62a8c368679f9575735c2b2a3d777143296ee3005` |
| R04c: final route/label occlusion repair | [Current source](flyji-refresh.html), [check script](qa/refresh-check.cjs), [results](qa/refresh-results.json), `qa/refresh-screenshots/` | 165 | `737b7dce72c83f97129df6a0319723e82990457960b85a6d546d03585784435b` |

Each result file records the corresponding script and screenshot hashes. The final fragment is 790,285 bytes; its script SHA-256 is `726817fb34ad956eb40017fc2f4002494ce9e12a48cfb199206bbbc5e7a049e5`. The preceding time-label-spacing failure source/script remain at `qa/revisions/refresh-preflight.html` and `refresh-preflight-check.cjs`; these are not a passing audit.

| Observed author finding | Revision / defense | Verification state |
|---|---|---|
| Orbit text rows touched at one desktop width; measured rectangles overlapped by a small fractional amount | Add actual vertical spacing to the lane/axis rows; require about 4px label separation | Revised checks cover each supported width |
| The two nonstops shared one blue line, although the legend included a compared journey | Render both line identities on shared geography; show selected circle and compared diamond at their distinct scheduled instants | New shared-line, marker and exact-flight phase checks |
| Phone captures stacked map, all five choices, time and evidence into a long page | Add Map/Options/Time views, preserve selected local-day detail and state | Narrow-width keyboard checks and fresh-context touch activation pass; selected identity stays visible in each view |
| Large alternatives left different amounts of empty desktop space; the canvas query had a redundant grid column | Give the canvas its own query grid, tighten its repeated choices/map, and give Airlight a larger spatial surface | Final captures inspected; alternatives retain a shared carousel stage height, with remaining empty space below shorter views |
| Cursor edits rebuilt the 177-country geography despite changing only scheduled positions | Reuse geometry/airport controls and update the marker layer; retain user-driven interpolation and reduced-motion behavior | Retained-node assertion passes; paired R04a/R04b timing evidence is reported below |
| Undo, Restore and Clear comparison could remove the focused action; evidence could collapse during a state echo | Return focus to a stable query/cursor control and retain an open same-journey evidence disclosure | New native focus and state-echo checks |
| Disclosure labels lacked a strong visible affordance in the captures | Restore native summary markers instead of imitating disclosures with generic text | Final visual inspection and coarse target checks pass |
| Scoping general control styles raised their CSS specificity and enlarged airport targets at 320px | Scope them through `:where(.fr-app)` so component sizing still wins and host carousel controls remain separate | Narrow-width airport target/overlap checks pass |
| The passing R04b dark Earth capture still placed an airport label over the compared diamond | Place airport labels outside the selected/compared route corridor, with leader lines to their coordinates; retain labels while scrubbing | Twelve additional checks cover both marker identities in three directions at four widths; final Earth captures show both markers |

The expanded harness also needed repair. It now waits for the host carousel to initialize, switches directions through its native control, waits for attached hidden options appropriately, compares stage heights after view stabilization, and captures/restores through the renderer's state API. A trace showed that manually hiding carousel sections could activate the wrong direction; responsive checks now use native keyboard activation, while the separate fresh touch context uses actual taps. The diagnostic capture is retained as `qa/revisions/refresh-mobile-harness-failure.png`. These are harness findings, not customer observations.

Final checks cover exact dated selection/comparison, proportional connection waits, local endpoint dates and cursor clocks, before/waiting/flying/arrived phases, constraint-aware date counts, complete/partial/unknown coverage, retained excluded evidence and recovery, shared-line choices, camera controls, focus, state echo/restoration, responsive geometry, text/control fit, airport/marker overlap, sampled text contrast in both schemes, coarse targets, no autoplay and reduced motion. Sampled text contrast minima exceed 4.5:1 in all three directions and both schemes; this does not test every visual state or replace a screen-reader review.

### Bounded rendering measurement

[Retained timing evidence](qa/refresh-performance.json) compares **R04a with R04b**, before the final label repair. One local paired run used three warmups and fifteen samples per version at 1100×1000px, Airlight visible, reduced motion enabled, with all three directions updating.

| Version measured | Median | p95 |
|---|---:|---:|
| R04a | 36.2ms | 38.4ms |
| R04b | 8.9ms | 9.6ms |

The interval includes synchronous native input dispatch, DOM update and forced layout. It excludes paint, host overhead and network. This supports keeping static geometry when the cursor changes; it is not a frame-rate promise, production budget, device benchmark or statistical study. R04c retains this mechanism, but these measured numbers belong to the archived R04b source. The [timing script](qa/refresh-performance.cjs) records the source hashes it measures; rerunning it against the current source produces a separate current-version measurement.

### Reproduce final browser checks locally

The source is a visualization fragment. Render the preview, then use the installed Node/Playwright runtime and Chrome. Keep new output outside the retained audit directories:

```sh
python3 /Users/lichen/.codex/plugins/cache/openai-bundled/visualize/1.0.49/skills/visualize/scripts/render.py docs/design/flyji-refresh.html /tmp/flyji-refresh-preview.html --force
NODE_PATH=/Users/lichen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules FLYJI_QA_OUTPUT=/tmp/flyji-refresh-recheck node docs/design/qa/refresh-check.cjs
```

### Proposed next decision and remaining evidence

Airlight is the leading author proposal for the base visual language: its large flat atlas keeps routes accessible without managing a camera. Journey canvas supplies a competing time/date emphasis for deadline-sensitive tasks. Orbit remains a working optional spatial mode whose added value must justify occlusion and camera effort. These are proposals for comparison, not an accepted hybrid or an audience ranking.

Next, compare the same dated tasks with travelers, enthusiasts and researchers before accepting a default. Prioritize arrival-date comprehension, excluded-selection recovery and phone effort; keep visual preference separate from answer accuracy. Independent accessibility review, real phones/Safari, dense route/date fixtures, actual slow-device/offline behavior and live snapshot changes remain outstanding. Seven-day phone layout and the shared carousel's remaining whitespace need observation, not an inferred success score. DST/date-line semantics, fares and booking value remain outside this bounded slice. Record owner acceptance/revision/deferral of D17–D20 and the exact version before deriving implementation priorities.

## Customer-led server implications

The new comparison requires both selected dated identities to stay attached to the same evidence snapshot, even when excluded by a later filter. Responses must distinguish the full query count from visible route aggregation and return each line's supporting dated journeys. The client owns the camera, visual direction, cursor and comparison presentation.

The reader still needs truthful date-specific coverage/counts, airport names/coordinates/zones, stable journey/flight IDs, snapshot identifiers and destination-zone arrival constraints. A spherical map or richer visual treatment does not require a new deployment service. No server endpoint or ingestion module is changed by this round.
