# Design alternatives and decision register

All UI decisions here are proposed. Product-owner consensus is pending. Owner-confirmed scope: serve travelers, enthusiasts, and researchers. Proposed task modes share data, selections and evidence; the opening screen does not require a persona choice.

Existing agreed constraints: Flyji branding, local Python prototype, SQLite WAL, read/write package separation, FastAPI serving, no fares/inventory/booking.

## Three alternatives to test on the same data

### Journey board

Readable options and time comparison are primary. Exact dates, stops, durations, next-day arrivals, and waiting time are easy to scan. Geography is explained by airport names and ordered legs. Lowest implementation/device cost; less spatial discovery and weaker emotional distinctiveness. Best candidate for precise arrival/departure decisions and the first working slice.

### Atlas workspace

A geographically accurate 2D map and a compact result list share a selection; aligned time lanes sit below. The same itinerary can be identified in both views. The map answers where, the timeline answers when, and the list provides accessible detail. More geography is simultaneously visible than on a globe. Route congestion must be managed by showing selected legs strongly and context routes quietly. Candidate default after the basic journey flow is proven.

### Globe studio

A rotatable orthographic Earth view emphasizes global connections and great-circle geometry. Time cursor highlights illustrate whether selected flights have departed, are in flight, are waiting at a connection, or have arrived. A constant UTC clock keeps elapsed time comparable, while endpoint cards show airport-local dates/times. It remains paired with list/timeline alternatives.

This prototype renders a spherical geographic projection, not a WebGL terrain engine. It tests the interaction idea without adding a production rendering dependency. Dots interpolate great circles according to synthetic scheduled elapsed time; they do not represent measured aircraft positions, exact routing, altitude, or live tracking. No auto-spin or autoplay. Backside routes may be hidden: keyboard-accessible rotation and the equivalent result list prevent the globe being the only source of information.

The globe's appeal is a hypothesis. Its cost includes occlusion, orientation, motion sensitivity, label collisions, and low-end/mobile performance. It earns default status only if testing shows task benefit, not because it looks impressive.

## Time representations: select by task

| Representation | Useful question | Cost / guardrail | Stage |
|---|---|---|---|
| Endpoint local dates/times + elapsed duration | What does this journey mean for my day? | Always show local date, zone and +day differences; UTC only is insufficient | First |
| Aligned elapsed-time lanes on a common clock | Which option spends less time traveling or waiting? | Flights and layovers use different patterns; bar lengths proportional to elapsed time | First |
| Service-day calendar | Which dates have supported departures? | Counts must be date-specific; unknown day differs from zero | Second |
| Departure-vs-arrival scatter/brush | Can I trade departure time for earlier arrival? | Advanced, requires axis literacy and textual equivalent | Second experiment |
| Schedule time cursor + map/globe | Where is the journey in its sequence at this moment? | Snapshot scrubber, not search filter; synthetic position labeled; no implied live tracking | Globe experiment |
| Weekly timetable by airport | How often and when is this airport connected? | Date zone and operational/scheduled basis must be explicit | Research/enthusiast |
| Time-expanded graph | Which next leg can follow this arrival? | High cognitive load; next-step builder is clearer for most travelers | Third experiment |
| 3D time extrusion/spirals | Could altitude encode time? | Distorts geographic altitude meaning, occludes and complicates selection; reject as default without task evidence | Deferred |

## Proposed decisions and defense

| ID | Need | Proposal | Defense / alternative | Evidence gate |
|---|---|---|---|---|
| D01 | N01–N08 | Journey mode starts with time/list; network mode with Atlas; inspect mode with evidence; globe is optional | Time comparison is legible without camera control; user can still explore geography | Owner agreement + task observations; unresolved |
| D02 | N03/N04/N14 | Separate search dates, deadline, and display cursor | Avoid confusing a highlight operation with a filter | Participants correctly explain the distinction |
| D03 | N04/N05 | Local endpoint times; one shared time axis for comparisons | Prevents comparing different clocks as elapsed time | Overnight, DST, and date-line QA; user interpretation |
| D04 | N05/N11/N15 | Describe “timing fits”, not “safe/bookable connection” | Generic layover bounds are not airport MCT, immigration, baggage or protected tickets | Users do not infer guaranteed transfers; known/unknown data review |
| D05 | N08 | Explicit least duration / fewest stops / earliest arrival | No hidden “best” score or price metric without evidence | Sorting explanation and deterministic ties |
| D06 | N01/N17 | Every map/globe result has a list/timeline counterpart | Supports mobile, accessibility, occlusion, and low-power devices | Full task completion without map manipulation |
| D07 | N15 | Coverage and freshness are part of response/UI contract | Prevent false certainty from incomplete data | Empty/partial/error/truncated fixtures produce different messages |
| D08 | N06/N07 | Reveal advanced constraints progressively; builder selects dated legs | Keeps initial choice small without discarding advanced capabilities | Novices complete first task; experts find required constraints |
| D09 | N14/N17 | No autoplay; time cursor and rotation controls; respect reduced motion | Motion is optional, user-controlled and does not substitute for evidence | Reduced-motion, touch, keyboard and low-end-device checks |
| D10 | N16 | Anonymous local shortlist first, share canonical query later | Avoid accounts without a clear cross-device need | Saving remains understandable and exposes stale records |
| D11 | N01/N07/N14 | Test a map-first workspace with an optional Earth projection | Owner requested more visual exploration; larger geography may improve discovery. Precise time/list comparison remains available. This experiment does not approve a map-first default for every task. | Compare discovery and precise-answer tasks with the first round; global density and mobile evidence |
| D12 | N08/N14/N17 | Projection and mobile view changes preserve query, selection, pins and cursor; shared physical legs offer dated journey choices | Camera choice must not alter the query, and a geographic line can represent multiple journeys | Pointer/keyboard checks plus user understanding of leg vs complete journey |
| D13 | N03/N04/N15 | Date choices show query-specific loaded counts and coverage; an arrival deadline is interpreted in the destination zone | Flexible dates must not imply daily service, fare or exhaustive results. A fixed deadline is a different operation from scrubbing a schedule. | Date/arrival slices; DST/date-line and user interpretation remain outstanding |
| D14 | N07/N15/N16 | Keep excluded selected evidence/legs visibly, with explicit undo or constraint repair | A filter must not silently substitute a dated flight or erase construction intent | Implemented in breadth slices; earlier map fallback and live revision semantics still need repair |
| D15 | N18 | Compare matched query cohorts and show differences only when both coverage bases support them | Missing or partial data cannot establish route removal; unlike weekdays do not establish seasonality | Breadth fixture checks plus real provider/cohort/customer methodology review |
| D16 | N01–N20 | Continue divergence across twelve families; defer default selection until task evidence and owner review | Previous layouts explored only a subset. More screens or browser checks do not prove a universally best design. | Exploration program and benchmark coverage; representative sessions pending |
| D17 | N01/N03/N04/N08 | Test “explore freely, then get precise”: an inviting route/day brief with progressive constraints and linked factual consequences | Airlight, Journey canvas and Orbit studio emphasize different starting questions on the same fixture. Editorial space and novice/expert needs may conflict. | R04 author QA/critique plus representative task observations and owner review |
| D18 | N05/N14/N16 | Keep one selected and one compared dated journey across visual directions; use different schedule marker shapes and shared-scale flights/waits | Identical geography can conceal a meaningful three-hour arrival difference. Shared lines offer dated choices and both identities remain visible. | Shared-route/clock/retained-selection checks; users must distinguish illustration from observed flight position |
| D19 | N17 | Use Map, Options and Time mobile views with a persistent selected local-day summary | Reduces the long stacked prototype while keeping exact dated evidence and equivalent native controls available | Narrow/coarse-pointer checks; real-phone discoverability, screen-reader and task-effort evidence pending |
| D20 | N14/N17 | Reuse static geography during schedule scrubbing; interpolate marker changes only after user input and honor reduced motion | Expensive country-path reconstruction adds no new spatial evidence to a time-only edit | R04 retained-node and bounded local timing checks; dense/camera/low-end performance remains open |
| D21 | N01–N20 | Refine Airlight, Journey canvas and Orbit studio before choosing a direction | Owner explicitly chose this strategy on 13 October; keep task evidence and visual preference separate across all three audiences | Refinement strategy accepted; final UI/default remains unchosen |
| D22 | N04/N08/N14/N16 | Pair both journeys' local dates/flight identities, allow exact role swapping, and provide selected-departure/event actions | A shared spatial line or UTC bar alone does not explain different local-day consequences; roles/cursor must not silently change the query or wrap from arrival to departure | R05 dated-identity/query/focus and final-arrival boundary checks; date interpretation and phone visual effort need participant observation |
| D23 | N14/N17 | Explain each off-view schedule position and offer explicit Face/Fit recovery; fit current loaded/retained geometry | Geographic camera movement must not imply that a dated journey disappeared; no automatic camera following | R05 keyboard/touch recovery checks and captures; density, real devices and customer benefit remain open |
| D24 | N17/N25 | Use standard task headings and concise geeky styling | Owner explicitly rejected promotional/AI-like headings; functional labels reduce interpretation work | Wording/style direction accepted; final palette/layout and customer interpretation remain open |
| D25 | N07/N21/N22 | Retain exact earlier legs and rebuild the entire later suffix after a next-flight/window edit, with Undo | Follows the requested dependency behavior; explicit locks are a competing future mechanism | R06 browser/Python checks; density and customer effort remain open |
| D26 | N09/N23 | Keep exact airport vertices, city membership and opt-in directed transfer allowances | Merging airports hides ground travel; city equivalence cannot establish transfer time | SQLite metadata/rollback/FK, direction/minimum and preview checks; real transfer evidence pending |
| D27 | N14/N24 | Show leg frames the sphere and positions its departure cursor; retain flat map and native controls | Connects a geographic action to the dated route being edited | R06 pointer/keyboard/touch checks; no final globe-default approval inferred |

Progressive disclosure is informed by [NN/g's guidance](https://www.nngroup.com/articles/progressive-disclosure/), not proof of a particular screen's usability. Accessibility targets draw on W3C guidance: [reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html), [target sizing](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html), and [reduced motion](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html). Aim for 44px essential touch targets; WCAG 2.2 AA minimum is 24px with exceptions. No conformance claim is made by this exploration.

## Visual and interaction direction

Editorial typography, generous whitespace, a quiet mineral/ocean palette, and one amber selected-route accent. Flight lines, time lanes, and cards share selected identity. Avoid rainbow routes, ornamental animation, airline logo dependence, or a cockpit full of simultaneous controls. Direct labels and line patterns supplement color.

That paragraph describes R01–R03. The [refreshing direction campaign](refresh-direction.md) deliberately reopens the visual system: luminous blue/coral Airlight, warm botanical Journey canvas and atmospheric Orbit studio. R04 adds selected/compared shape identity and richer geographic depth. No palette, typography, default entry mode or final combined layout has been accepted. Earlier sources and audits remain intact.

The owner chose to [refine all three](refinement-round.md) before choosing. R05 keeps their different geographic, date/time and Earth emphases while improving paired local-day evidence and camera recovery. The earlier author's leading-candidate proposal does not settle this comparison.

R06 implements the owner's plain-heading/geeky direction, while keeping the three placements open. The Globe first preview opens on the requested sphere topic; that presentation order does not select the product's final default. Same-city transfer and suffix-rebuild semantics are working experiments with the explicit assumptions and objections recorded in [connections](connections-round.md).

The opening experience should communicate exploration, not an OTA checkout. Do not add cabin, passenger count, price sorting, checkout or fake availability. The first round places the query and readable results first on mobile. The [map revision](map-workspace.md) tests a compact applied-query summary with Map, Options, Time, and Details views; Map opens first in that experiment. A selected route carries across views. Names and local times remain readable; shrinking a desktop screenshot is not a mobile design.

Polish must be earned through responsive inspection, keyboard/focus tests, meaningful empty states, consistent dates and selection, legible color contrast, and slow-device performance. “Top-tier” is an acceptance aspiration, not a quality badge we can assert from one prototype.

## Staged scope and review gates

1. **Simple shared slice:** one journey query + timeline for travelers, origin/date routes for enthusiasts, and dated flight evidence/coverage for researchers; shared airport resolution, constraints, and selection. Gate: observe representative users completing precise journey tasks; backend must return honest completeness.
2. **Exploration workspace:** linked 2D map, flexible dates/service-day calendar, nearby airports, reverse search, shortlist comparison and restorable query. Gate: discovery improves without worsening precise decisions; date/airport-group semantics verified.
3. **Globe and journey construction:** optional sphere + time cursor, required/avoided hubs, next-leg builder, stopovers and multi-city constraints. Gate: spatial benefit and mobile/frame-time evidence; timing constraints cannot imply bookability.
4. **Research modes:** historical operational evidence, period comparison, network changes and exports. Gate: provenance, licensed coverage, stable methodology and representative researcher validation.

Do not equate staged scope with mandatory future features. Each gate can reject or revise the feature. Frontend build begins after audience and first-stage behavior are agreed; do not implement the entire backlog as a single interface.
