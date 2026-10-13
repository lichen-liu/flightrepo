# Refine all three Flyji alternatives

R05 · 13 October 2026 · the owner chose to refine all three alternatives before choosing a direction.

This round preserves Airlight, Journey canvas and Orbit studio as competing working alternatives. The [R04 source and audit](refresh-direction.md) remain unchanged. All three audiences still matter; no default or audience ranking is selected here.

## Questions this round addresses

| Need / scenario | Change shared by the alternatives | Defense | Strongest objection / remaining evidence |
|---|---|---|---|
| N04/N08/N14, B02/B04/B17: understand which local day each journey arrives | Selected and compared journeys each show local departure/arrival clocks, full dates, zones and exact flight identities | The physical route and a shared UTC lane alone cannot identify which dated nonstop is being discussed | Repeated local clocks add visual weight. Compare date interpretation and visual effort with actual users. |
| N16/N17, B19/B20/B21: change comparison roles without rebuilding a query | Swap exact dated identities while retaining constraints and the schedule instant; clear comparison from the always-available summary | Reversible role changes make an alternative inspectable without silently choosing a new match | Selected versus compared must remain unambiguous, including when both are excluded by a later query. |
| N14, B17/B18: inspect a selected flight on another day | Selected departure positions the shared cursor at its exact first-flight departure; Next selected event follows leg boundaries and stops at final arrival | The cursor remains an independent presentation instant, with explicit orientation and restart actions | This must not be confused with an arrival filter or live movement. |
| N17, B20/B21: use a phone with ordinary taps | Shorter invitation/query, paired local-day summary before the active view, compact date rows and stable visible focus after Clear | People can identify both dated journeys without switching to Time first | This places information before geography. Measure its effect on map-led exploration separately from date accuracy. |
| N14/N17, B20/B21/B34: recover a schedule marker outside the camera | Explain whether each marker is on the far hemisphere or outside the view; provide Face/Fit actions for the specific identity | Camera movement should not erase the user's understanding of a selected schedule | Recovery can leave the other identity off-view. Each status remains separately visible; no automatic following is inferred. |

## A distinct improvement to each alternative

**Airlight** keeps its large flat atlas, clear cool palette and geographic emphasis. The paired local-day summaries make the two nonstops' different arrival dates/times inspectable without inferring identity from one physical line. Atlas fitting now uses currently displayed edges, including retained selections, rather than a fixed unfiltered template.

**Journey canvas** keeps the departure-day invitation and common-clock emphasis. Phone dates use two rows of four and three rather than leaving the seventh day alone on a third row. Counts still apply the exact active query and preserve complete, partial and unknown coverage. The desktop summary compares two sets of local endpoints across its two columns.

The visible “Depart Toronto · local dates” caption also names the departure calendar's accessible group. Its dates are distinct from the destination-local arrival deadline above it.

**Orbit studio** keeps its geographic orthographic Earth, atmospheric treatment and native camera controls. Fit routes derives a geographic center and bounded zoom from the loaded/retained edges; Atlantic view remains a fixed orientation. Rotating away from either scheduled position produces a specific status and recovery action. This is an illustrated schedule on published geography, not observed aircraft telemetry or actual daylight.

## Author iteration and verification

The final author run passes **238 browser checks, with zero JavaScript errors** recorded across both the primary and fresh touch page contexts. All nine final captures were inspected: three desktop directions, three phone directions, dark Earth, fitted Earth and Earth with both scheduled positions beyond the visible hemisphere. Verification used headless Chrome **155.0.8059.40** on Darwin 27.0.0, widths 320/390/736/1024px, 1100px desktop captures, and a fresh 390px coarse-pointer/touch context.

| Version | Source and script | Results / captures | Checks | Source SHA-256 |
|---|---|---|---:|---|
| R05a: first complete passing refinement | `qa/revisions/refined-r05a.html`, `refined-r05a-check.cjs` | [Initial results](qa/refined-initial-results.json), `qa/refined-initial-screenshots/` | 208 | `6d6259c7948fffdd08d03dc78275b698af9725877b225cde9b63789f897611f6` |
| R05b: caption/summary refinement and added long-name checks | [Archived source](qa/revisions/refined-r05b.html), [script](qa/revisions/refined-r05b-check.cjs) | [Interim results](qa/refined-interim-results.json), `qa/refined-interim-screenshots/` | 233 | `a7b8238f132498924c93ba1d9e273871dc833bf6b6a4513865736edc17c77c27` |
| R05c: explicit selected-event scope and final-arrival boundary repair | [Current source](flyji-refined.html), [script](qa/refined-check.cjs) | [Final results](qa/refined-results.json), `qa/refined-screenshots/` | 238 | `e3bf59dc45db7d8fb5caa0860b700e7cf488252c901d3cff22049ba243c1ca4c` |

The final fragment is **801,064 bytes**; the final check script SHA-256 is `07b23939132edb8088856f2f585977cecb8a76eda989eb0db470abd72d9f881d`. Each result file records its environment and script/capture hashes. Archived R04 evidence remains intact.

The first preflight preserved at `qa/revisions/refined-preflight.html` and `refined-preflight-check.cjs` stopped on a faulty test assumption: eight longitude steps did not necessarily hide a northern great-circle route because it remained visible over the pole. The corrected scenario changes latitude as well as longitude. This is a harness finding, not an observed product defect or a participant report.

A later harness audit found that the page-error listener covered the primary page but omitted the fresh touch context. Its [earlier 233-check record](qa/refined-primary-errors-results.json) and script at `qa/revisions/refined-primary-errors-check.cjs` are retained. R05b was rerun with both listeners before R05c; the nine R05b captures remained byte-identical. The final error scope is bounded to those two tested contexts.

| Actual author finding | Revision / defense | Verification |
|---|---|---|
| R04 exposed the compared journey's local endpoints primarily through selection or the common UTC graphic | Give both dated identities local clocks/dates, flight numbers and independent exclusion labels; add Swap | Exact role/query/cursor checks pass, including both retained identities outside a 05:00 deadline |
| A new departure day could leave the independent cursor before that journey | Provide explicit Selected departure without making selection a hidden cursor/filter change | Exact first-flight instant and subsequent connection wait checked |
| Event stepping inherited a silent wrap from final arrival to the first departure | Name the action Next selected event, disable it at/after final arrival, and use Selected departure for explicit restart | Final-arrival stop, native focus transfer to the cursor, after-arrival preservation and re-enabling after earlier scrubbing checked |
| Fit routes and Atlantic view were equivalent in R04 Earth; moving the camera could hide a schedule marker without a specific explanation | Fit loaded/retained geography, preserve Atlantic preset, explain each off-view marker and provide explicit recovery | Distinct camera paths, hemisphere/off-view states, stable native focus, keyboard and physical touch recovery checked |
| R05a's phone paired summary was still tall | Combine each role and route/duration heading while retaining full dates, zones and flight numbers | Final captures inspected; long Montréal/London headings and marker clearance checked at 320/390px |
| The calendar sat beside a Paris deadline without a visible origin-date caption | Add visible/accessible Depart Toronto local-date group label | Accessible group, exact dates/coverage, native keyboard selection and two-row phone layout checked |

Final checks also retain R04's dated shared-line choices, proportional waiting, phase/local-clock math, coverage distinctions, exclusions/recovery, state echo/restoration, geography reuse, responsive fit/label separation, no autoplay and reduced motion. Sampled text contrast minima remain above 4.5:1 in both schemes across all directions; minimum supporting text is 11px and tested visible coarse controls reach about 44px. This is bounded author QA, not a complete accessibility or device certification.

### Measured phone layout tradeoff

[Layout evidence](qa/refined-layout-results.json) and its [script](qa/refined-layout.cjs) re-render R04c, R05a, R05b and R05c in the same current Chrome runtime. There are twenty-four cases: four versions × two viewport widths × three directions. The table below rounds CSS pixel distances for the 390px preview, whose actual application width is 358px. Defaults are Map for Airlight/Orbit and Time for canvas.

| Direction | Body content bottom, R04c → R05c | First graphic top, R04c → R05c | Local-day summary top, R04c → R05c |
|---|---:|---:|---:|
| Airlight | 1777 → 1592px | 781 → 1043px | 1329 → 483px |
| Journey canvas | 2028 → 1712px | 1053 → 1113px | 1569 → 602px |
| Orbit studio | 1778 → 1572px | 802 → 1043px | 1330 → 483px |

The summary now contains both identities, whereas R04's summary contained the selected one. R05b shortens R05a's summary by 46px at both tested widths, retained in R05c; the explicit canvas date caption adds space back for clarity. R05c's clearer selected-event label adds 46px to the 390px canvas body when its control wraps. The overall default body is shorter, but geography/time begins farther down because paired local-day information precedes it. Neither layout is declared better from these distances. Test map immediacy and date accuracy separately before accepting this placement. The shared carousel stage still leaves whitespace beneath shorter alternatives/views.

These are author layout distances, not observed scrolling, task effort, participant preference or frame-time measurements. They exclude the footer/remaining stage whitespace from body content bottom. No new performance claim is made; the previous timing evidence belongs to its archived R04 versions.

### Reproduce checks

From the project root, render the fragment and use the installed Chrome/Playwright runtime. Keep fresh output separate from retained audit directories:

```sh
python3 /Users/lichen/.codex/plugins/cache/openai-bundled/visualize/1.0.49/skills/visualize/scripts/render.py docs/design/flyji-refined.html /tmp/flyji-refined-preview.html --force
NODE_PATH=/Users/lichen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules FLYJI_QA_OUTPUT=/tmp/flyji-refined-recheck node docs/design/qa/refined-check.cjs
```

For layout replay, also render `flyji-refresh.html` to `/tmp/flyji-refined-r04-preview.html`, `qa/revisions/refined-r05a.html` to `/tmp/flyji-refined-r05a-preview.html` and `qa/revisions/refined-r05b.html` to `/tmp/flyji-refined-r05b-preview.html`, then run `refined-layout.cjs` with `FLYJI_LAYOUT_OUTPUT` set to a new JSON path and the same `NODE_PATH`.

## Decision status and next evidence

The owner has accepted **continued refinement of all three**, not an implementation default. Visual preference, answer accuracy, recovery, phone effort and enthusiasm for Earth must be recorded independently before comparing audiences or accepting a direction. D17–D20 remain proposed mechanisms; D21 records the refinement strategy.

Independent screen-reader/accessibility review, actual mobile devices/Safari, high route/date density, stale provider snapshots, DST/date-line cases and fares/booking value remain outstanding. No participant sessions, independent review or performance guarantee are implied by author browser checks.

## Server support

This round operates on the same 21 synthetic flight records and fourteen complete journey options as R03/R04, with the same fixed Toronto–Paris scope. It changes no endpoint or Python module. Comparison roles, camera recovery and cursor positioning are local. The reader contract still needs stable dated IDs and snapshots, airport zones/coordinates, truthful query-specific coverage/counts and validated destination-local constraints, as recorded in [server support](server-support.md).
