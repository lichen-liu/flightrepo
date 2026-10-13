# Breadth round: different ways to frame a flight question

Date: October 10, 2026. Round R03. Status: proposed exploration; no final selection, customer sessions or independent review.

The owner requested a deep iterative process. This round moves beyond rearranging the original query flow. Four new working slices test date-first, arrival-first, construction-first and evidence-first models. Together with the earlier board, Atlas and Earth, seven interaction families now have working slices. Five further families remain explicitly registered in the [design space](design-space.md). They have not been implemented or rejected.

The repeated process and decision gates are in the [exploration program](exploration-program.md); its twelve dimensions and [36 benchmark scenarios](benchmark-scenarios.md) define coverage to pursue. The design space is open-ended. This round is progress toward systematic coverage, not a claim to have exhausted every possible interface.

## Four distinct experiments

| Family | Critical interaction implemented | Strongest defense | Strongest objection / next test |
|---|---|---|---|
| Date explorer, F04 | Select a departure day with query-specific loaded counts; filter nonstop/avoid; distinguish complete, partial and unknown coverage; inspect a dated option | People with flexible days can compare evidence without repeatedly retyping dates. Counts and shortest loaded duration use the actual filters. | Seven dates and duration are only one date representation. Test month/range views, nearby-airport combinations, and whether users misread counts as frequency or fares. |
| Arrival planner, F05 | Apply a destination-local deadline; inspect departure-versus-arrival points and readable matching/later alternatives | Begins with a real fixed constraint, such as a meeting, and makes the later-departure/earlier-arrival tradeoff visible. An early nonstop gives the graph a real choice rather than a universally dominating dot. | The chart requires axis literacy, mixed endpoint zones and a common-date assumption. Compare its task effort with a plain sorted list; test overnight/date-line/DST inputs before generalizing. |
| Journey builder, F06 | Choose exact dated departure, see timed next legs, adjust gap, retain a dead-end sequence, undo, and inspect elapsed flight/wait time | Makes a required hub or custom leg choice explicit and reversible. A failed continuation explains the gap without replacing the first flight. | One connection is a small slice. Multi-city, airport changes, overnight stops, loops and live schedule revisions may make construction costly or unsafe to interpret. |
| Evidence workspace, F07 | Compare journey-pattern counts under identical constraints; inspect dated supporting records from either cohort; block difference claims for unknown/partial coverage | The method and evidence basis stay visible, and an absent record is not automatically called a removed route. | These are two departure-day cohorts, not a seasonality/frequency analysis. Researchers may need operating movement counts, matched weekdays, wider periods, aircraft and export. Those are still missing. |

`flyji-design-laboratory.html` is the offline source for these experiments. It uses the existing Flyji visual language to isolate differences in interaction model. Multiple aesthetic systems are a later campaign; this round does not claim that four differently structured screens exhaust visual design.

## Inputs, state and truth

The route is fixed to Toronto–Paris to isolate time, construction and evidence questions. The date picker covers 17–23 October 2026. Deadline input is limited to 18–24 October in Europe/Paris; it is a bounded prototype, not a general production time-zone resolver. Airport zone labels are explicit. UTC instants drive durations and deadline comparisons.

All flights are fictional. The final fixture has fourteen loaded complete itinerary options across its seven departure-day states: five on the 17th, two on the 18th, a covered zero on the 19th, unknown coverage on the 20th, three on the 21st, one loaded option under partial coverage on the 22nd, and three on the 23rd. “Complete” describes this authored query fixture, not real-world commercial coverage. The 17th and 23rd add an earlier nonstop that was absent from the first passing version and earlier map fixtures; those versions cannot be compared as identical task datasets without aligning the fixture first.

The final 17 October fixture has an earlier 14:00 Toronto nonstop arriving 03:00 Paris the next day, and a later 17:00 Toronto nonstop arriving 06:00. At a 07:00 Paris deadline, three of five loaded options match. At 05:00, only the earlier nonstop matches; at 02:00, none match. Later options stay readable and labeled rather than quietly changing the date. None of these options asserts commercial availability.

Selection is an exact dated identity. Date/arrival filters can exclude it while its evidence remains visible with an outside-query flag. The builder retains chosen IDs when a gap, date or avoided hub makes the sequence inconsistent, and offers explicit undo/constraint repair. This improves the breadth slices; the older map prototype's fallback selection and real provider revision semantics remain unresolved.

The arrival plot uses filled circles for deadline matches, hollow diamonds for later arrivals and an amber selected identity. A dashed deadline line has its label outside the plot. Fine-pointer point controls are 32px; coarse-pointer controls are 44px where there is space. In a narrow coarse-pointer chart, marks are a visual counterpart and native readable options provide selection. No hover fact is needed to finish the task.

The evidence metric is complete dated journeys, grouped by airport sequence. Two distinct nonstops count as two journey instances under one pattern. This does not deduplicate operating movements for an aircraft/service-frequency study; that remains B16/F11 work. The date cohorts share the same origin, destination, one-connection and 45m–6h timing rules. Missing/partial coverage blocks a full difference assertion even when some records are present.

## Actual iteration and critique

| Finding | Evidence | Revision | Disposition |
|---|---|---|---|
| Startup template expression failed | Syntax and browser startup failure | Corrected the expression before usable review; syntax/startup then passed | Repaired; not counted as a customer observation |
| Focus was lost after date redraw; list selection could jump to a chart twin | Actual keyboard checks plus render/state inspection | Capture semantic control identity inside every render, including resize/state updates; restore the same control kind | Retested date/list/pattern focus |
| Original arrival fixture made the later direct journey dominate the simple comparison | Author critique of the first passing screen/fixture | Added an earlier nonstop and a 05:00 deadline scenario | Earlier-departure tradeoff verified; customer comprehension pending |
| Deadline annotation overlapped a point on a narrow screen | Author screenshot inspection; generic clipping checks had passed | Moved the annotation into the legend; checked plot-button overlap at all stated widths; narrow touch uses readable selection | Repaired in the final captures |
| Completed builder left a large vacant column | Author desktop screenshot inspection | Completed sequence uses a full-width time/evidence panel with a stacked mobile counterpart | Reinspected; user effort still unmeasured |
| Pattern count named two supporting dates but showed only one record | Author evidence inspection | Added explicit dated-record choices for the cohort evidence; chosen record remains identifiable | Second-cohort source ID verified |
| Date weekday and numeral ran together | Author visual inspection | Separate the weekday and date numeral | Reinspected |

The initial passing version and its **83-check** result are preserved as [R03a source](qa/revisions/lab-r03a.html), [check script](qa/revisions/lab-r03a-check.cjs), [result](qa/lab-initial-results.json) and screenshots in `qa/lab-initial-screenshots/`. The final reviewed version has its own audit below. Different check counts and fixtures do not constitute a measured usability improvement.

## Final bounded audit

[Final browser evidence](qa/lab-results.json) records **88 passed checks**, zero observed JavaScript runtime errors, source/script identity, browser environment and screenshot hashes. [The check script](qa/lab-check.cjs) drives the actual controls. Widths are 320, 390, 736 and 1,024px; captures use a tall viewport to avoid wrapper clipping. Native coarse-pointer checks use 390px.

| Family | Desktop capture | Mobile capture |
|---|---|---|
| Date explorer | [Desktop](qa/lab-screenshots/dates-desktop.png) | [Mobile](qa/lab-screenshots/dates-mobile.png) |
| Arrival planner | [Desktop](qa/lab-screenshots/arrival-desktop.png) | [Mobile](qa/lab-screenshots/arrival-mobile.png) |
| Journey builder | [Completed desktop sequence](qa/lab-screenshots/builder-desktop.png) | [Mobile departure choices](qa/lab-screenshots/builder-mobile.png) |
| Evidence workspace | [Desktop](qa/lab-screenshots/evidence-desktop.png) | [Mobile](qa/lab-screenshots/evidence-mobile.png) |

The [dark arrival capture](qa/lab-screenshots/arrival-dark.png) uses reduced-motion settings. Author visual review covers the captures. Text/controls fit the checked widths; fine-pointer plot targets do not overlap. Sampled headings, labels, buttons, paragraphs and table text meet 4.5:1 contrast and minimum 11px size in both appearances. Visible coarse-pointer native controls meet the 44px aspiration. These are bounded samples, not full accessibility conformance or physical-device testing.

The source is below 1 MB, uses embedded D3 7.9.0 with its retained ISC notice and makes no product network requests. No production Python/SQLite/FastAPI code or deployment dependencies changed. Node/Playwright/Chrome are design QA tools. The exact source shown matches the repository copy.

```sh
python3 "$VISUALIZE_SKILL/scripts/render.py" "$PWD/docs/design/flyji-design-laboratory.html" /tmp/flyji-lab-preview.html --force
FLYJI_QA_PREVIEW=/tmp/flyji-lab-preview.html FLYJI_QA_OUTPUT=/path/to/writable/qa-output node docs/design/qa/lab-check.cjs
```

Make Playwright available to Node (`NODE_PATH` can point to the bundled dependency directory). Chrome is the default isolated headless channel. For R03a reproduction, use the preserved source/script and an aligned preview in an isolated directory with that source at `docs/design/flyji-design-laboratory.html`. The retained script resolves source metadata from that relative path; running it against the latest source would misattribute the hash. No customer task study or independent reviewer was involved.

## What remains and what follows

No universal winner is declared. The next breadth questions are guided brief versus compact form (F08/F01), topology versus geographic entry (F09/F02), and persistent comparison versus transient pins (F10). Then exercise dense results, network/timetable small multiples and failure/stale-provider states. Space-time extrusion remains registered until a task requires it; it should compete with simpler representations.

All four new slices still have long mobile reading paths and a small fixed-airport dataset. Calendar range/month semantics, inbound/return/multi-city inputs, general DST/date-line handling, 100/1,000-option density, measured performance, screen readers, real devices/browsers, offline/loading/errors, commercial suitability and actual provider changes remain outstanding. Each has benchmark/campaign coverage rather than a hidden assumption of support.

Representative task sessions and owner review are needed to accept/reject/recombine mechanisms. The current work can continue without choosing a winner; final product decisions require that evidence and agreement. Customer needs continue to determine the server contract, including date coverage, arrival normalization, stable next-leg queries and reproducible cohort metrics in [server support](server-support.md).
