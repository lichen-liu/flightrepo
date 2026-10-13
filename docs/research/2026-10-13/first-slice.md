# Explore and compare flight options

R09 · first proposed experiment · owner focus: exploring, selecting, analyzing and playing with each option. This is a reviewable specification, not a newly implemented UI or observed customer result.

## Intended loop

Start from an airport, dated flight or loosely defined trip. Find something worth inspecting, understand its geography and timing, compare it with alternatives, try a change and see the consequences. Recover easily and retain a useful insight. A person may investigate a single flight or a whole journey; those selection scopes must be explicit.

| Step | Interaction choices to compare | Required truth and recovery |
|---|---|---|
| Browse | Compact query/list, linked map or contextual actions from a seed flight | Dated loaded scope and unknown coverage visible; no global-service claim from a line |
| Select | Flight row, itinerary card, named map leg with dated choices | Highlight and details identify the exact flight/journey; a shared geographic edge never silently chooses a date |
| Analyze | Endpoint facts plus lanes, progressive detail or departure/arrival tradeoff | Local days, elapsed waits, stops and identities agree; illustration differs from actual operation/track |
| Compare | Reference plus multiple pins, shared lanes or matrix | Faster/more stops, earlier/longer wait and dominated options expose tradeoffs without an invented best score |
| Play | Change a date, wait, airport or next leg; preview/apply versus direct reversible edit | Current, hypothetical and saved states distinguishable; changed suffix identities/waits/deadline consequences visible |
| Protect intent | Exact prefix, local stay and optional downstream commitment | No silent unlock; impossible combination explained; locks/per-gap rules remain experimental |
| Recover | Cancel preview, Undo, backtrack or computed relaxation | Exact former evidence restored and dated; a recovery suggestion must be computed or marked unavailable/incomplete |
| Retain | Pin/export or copy query with clear scope | Query link and frozen evidence distinguished; old selections don't become current facts on reopen |

Standard headings for comparison: Flights, Compare, Connections, Schedule, Map and Evidence. Concise names plus codes should remain understandable without airport-code knowledge. The three existing visual treatments stay candidates; these labels do not approve an information architecture or palette.

## Alternatives and defenses

**Options and time first** can make timing differences direct and allow fast scanning. It may hide interesting spatial alternatives or feel repetitive. **Linked map and options** can connect geography with explicit dated choices; density and shared edges can confuse selection. **Optional Earth with linked detail** can make global geometry and exploration enjoyable; occlusion, camera work and phone cost can outweigh that benefit. Use equivalent facts across all three. Do not give the favored visual richer data or easier tasks.

A separate comparison varies universal query, action/task entries and contextual actions. Another varies instant reversible previews versus an explicit Apply. A third varies shared scope/candidate-specific markers versus brief per-card disclosure. Compare one mechanism at a time where possible; combined flows need a later whole-task retest.

## Unprompted customer task and functional QA

Begin with the participant's recent flight question and current tools. Invite them to explore the supplied dated options, select something worth understanding, compare alternatives and try a change they care about. Do not name a control, prescribe globe rotation or teach the expected interpretation before observing the attempt. If a hint becomes necessary, record it. Follow with probes about selected scope, local days, changed legs, unknown data and what useful insight they obtained. Enjoyment, time/actions, prompting, comprehension and a retained insight are separate observations.

Functional QA then deliberately exercises select/swap, Face/Fit, non-drag pointer controls, event stepping, preview/cancel/apply, exact Undo, stale facts, failure and latest-response races. Those scripted operations verify mechanics; they are not unprompted usability success. T21–T24 define the main comparison; T03/T04/T07/T10 are semantic/access gates; T25 adds stays and conflicts.

## Available support and fixture boundary

The current R08 web app already provides exact-airport/date search, selected itineraries, leg focus, flat/sphere maps, schedule cursor, flexible connection rebuild and Undo. Earlier alternatives provide comparison experiments on synthetic facts. Current R08 does not implement the whole proposed loop: single-flight discovery, multiple persistent pins, Pareto matrices, downstream locks, per-gap stay policies, history and general computed recovery remain experimental requirements.

Use [the existing synthetic connection fixture](../../../fixtures/connections-demo.json) as the initial bounded truth source: twelve dated flights, eight airports and one explicitly assumed LHR→LGW allowance. Its exact hash is in the manifest. It can test prefix/suffix editing and map/time linkage. It is not adequate by itself for dominated options, flight-first density, reverse discovery, polar/date-line/DST cases, matched historical cohorts, source failures or personal transfer suitability. Prepare and freeze those additional truth fixtures before their experiments; that gate is pending. No example asserts actual commercial service.

## What would reject this slice

No useful difference discovered; confusion between flight and whole journey; camera/control effort preventing analysis; changed or lost commitments; unrecoverable exploration; wrong local day/wait; illustration mistaken for live tracking; missing data read as no service; timing fit inferred as protected/feasible travel; or an inaccessible critical path. Repair and retest relevant failures. A participant using another tool or finding no value is evidence to retain, not an outcome to explain away. Final default, owner acceptance and customer usability signoff remain pending.
