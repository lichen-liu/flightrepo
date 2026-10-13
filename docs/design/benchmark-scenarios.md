# Benchmark scenarios and truth conditions

These are proposed tests across the design families, not completed customer sessions. There are 42 scenarios after the R06 additions below. Synthetic fixtures exercise semantics; real provider and participant evidence must follow. Attach each observation to a source hash, input/device, scenario and family.

## R06 additions

| ID | Need / task | Truth condition | Current exploration coverage |
|---|---|---|---|
| B37 | N07/N21: change London–Amsterdam departure | Earlier exact flight retained; both changed next flight and dependent Paris flight updated; Undo restores identities/window | R06 browser + Python |
| B38 | N05/N22: stay overnight and leave after a local clock | No six-hour cap in Any later; UTC ordering across dates; finite horizon remains explicit | R06 fixture + offset-aware Python input; general provider/DST campaigns pending |
| B39 | N09/N23: LHR → LGW in London | Separate airport IDs, explicit direction/consent, 180m demo allowance, no reverse inference | SQLite/updater/planner tests + synthetic browser fixture |
| B40 | N15/N26: next flight has no loaded route to destination | Selected prefix preserved; destination not reached; no misleading final-arrival comparison or global no-service claim | R06 no-tail/missing-window/cancelled-ID checks |
| B41 | N14/N24: inspect a selected middle leg | Globe camera and departure cursor refer to the exact flight; flat/list equivalents remain available | R06 pointer/keyboard/touch checks |
| B42 | N17/N25: phone connection editing with concise labels | Standard task headings; native controls fit; adequate touch height; no gesture required | R06 320/390px and fresh touch context; actual devices/accessibility pending |

| ID | Need / task | Truth condition | Current exploration coverage |
|---|---|---|---|
| B01 | N01: open destination from a known airport/day | Dated reachability; direct and connecting counts differentiated | Map/board fixture |
| B02 | N02: exact Toronto–Paris departure day | Complete ordered dated legs, elapsed duration, local endpoints | All journey slices |
| B03 | N03: choose among adjacent departure days | Every count recomputed from that date/constraints; unknown ≠ zero | Date breadth and R04 seven-day constraint-aware slices |
| B04 | N04: arrive Paris by 18 Oct 07:00 local | Deadline normalized to destination zone; all arrival comparisons use actual instants | Arrival breadth and R04 bounded next-day Paris slice; general DST parser pending |
| B05 | N05: compare 1h15 and 5h30 connection waits | Proportional elapsed waiting; no guaranteed-transfer claim | Board/map/builder |
| B06 | N06: avoid Montréal, then undo | Applied constraint visible; query and valid evidence recover consistently | Map/date/arrival/builder slices |
| B07 | N06/N07: require a named hub | Exact selected flight instance retained; next leg departs after arrival plus user's gap | Builder breadth slice |
| B08 | N08: least duration vs earliest arrival | Deterministic, explicit sort; no hidden composite “best” | Board and arrival slices |
| B09 | N09: city group vs exact airport | Exact airport choice/ground-transfer assumptions explicit | Registered; provider metadata needed |
| B10 | N10: fixed destination, open origin | Inbound evidence/window covered; not inverted outbound approximation | Registered |
| B11 | N11: return with a stay length | Separate dated outbound/return truth and minimum stay | Registered |
| B12 | N11: ordered multi-city trip | Every date/airport sequence fits; separate-ticket unknowns remain | Registered |
| B13 | N12: supported day with zero matching loaded options | States covered zero without asserting global commercial absence | Date breadth slice |
| B14 | N15: unsupported day | Coverage unknown; result does not imply route removal/no service | All relevant slices |
| B15 | N13: cancelled flight vs scheduled movement | Status/evidence distinct; scheduled not proof of flown | First-round service fixture |
| B16 | N13/N20: codeshares of one operating movement | Physical count not inflated; marketing identities separately inspectable | Registered |
| B17 | N14: scrub into a connection wait | Hub phase and local clock consistent with UTC; cursor is not a filter | Map/board slices |
| B18 | N14: scrub before departure or after arrival | Not departed/arrived states; no phantom continuation | Map/board slices |
| B19 | N16: restore canonical query and shortlist | Stable dated IDs, coverage/snapshot and changed evidence exposed | R04 restoration and R05 exact role swaps/retained identity checked locally; live stale semantics pending |
| B20 | N17: same task using keyboard only | Native controls and readable equivalents, focus retained | Bounded author browser checks |
| B21 | N17: phone touch without precision map gestures | Legible targets, focused view, same selected identity | Bounded 390px coarse-pointer checks, R05 paired summaries and physical touch camera recovery; actual devices pending |
| B22 | N17: screen reader without map | Complete named dated options; semantic updates and errors | Independent review outstanding |
| B23 | N18: compare two fully covered dates | Matched query/cohort; dated difference not a seasonality claim | Evidence breadth slice |
| B24 | N18: compare covered and missing dates | Missing period cannot count as removals or zero frequency | Evidence breadth slice |
| B25 | N19: ask whether option is purchasable/affordable | Fare, inventory and protection unknown unless sourced; unmet value question retained | Trust wording; scope remains unmet |
| B26 | N20: filter operating aircraft/carrier | Unknown field distinct from excluded aircraft; dated provider-supported fact | Registered |
| B27 | N04/N17: daylight-saving skipped/repeated local time | Explicit validation/disambiguation; common-instant comparison | Fixture campaign outstanding |
| B28 | N04: cross international date line | Dates can move backward/forward; elapsed duration stays positive | Fixture campaign outstanding |
| B29 | N05/N07: dead-end next leg or loop | Explains lack of dated continuation; undo preserves intent | Builder dead-end/gap slice; broader loops pending |
| B30 | N15/N16: selected flight revised/cancelled while reading | Stale selected evidence retained; no silent substitution | Real-data/resilience campaign outstanding |
| B31 | N15: search truncated by engine cap | Not globally shortest/exhaustive claim; limit basis visible | Backend prerequisite outstanding |
| B32 | N15: delayed/error/superseded query | Retain previous labeled results; no mixed snapshots; retry preserves draft | Resilience campaign outstanding |
| B33 | N01/N17: 100/1,000 overlapping routes | Density policy honest; query count separate from visible subset; selection possible | Stress campaign outstanding |
| B34 | N07/N14: same spatial leg belongs to multiple dated journeys | Explicit complete dated choices; physical line does not identify one itinerary | Map/builder selection and R04 shared-leg dated choices with two schedule identities |
| B35 | N16/N18: reproducible export and provenance | Export includes query, snapshot, basis, coverage and stable IDs | Evidence on-screen slice; export registered |
| B36 | N17: reduced motion/slow device/offline | Core task without animation; cached evidence disclosed; measured response times | No-autoplay/reduced-motion author checks and bounded R04a/R04b synchronous update timings; real device/offline pending |

## Use the same task without hiding model differences

Compare F01/F02/F03 on B02, B05 and B17. Compare F01/F04 on B03/B13/B14; F01/F05 on B04/B08; F01/F06 on B07/B29; F07/F11 on B23/B24. Do not force every concept to mimic the same layout. Compare its answer and effort against the same truth condition.

For participant sessions, start from a recent real need, then select relevant benchmarks. Record successful answer, interpretation error, prompting, recovery, actions/undo, elapsed task time, device/input and self-described confidence. Blank measurements remain blank until observed. Critical semantic errors trigger repair and retest, even if the participant likes the visual.
