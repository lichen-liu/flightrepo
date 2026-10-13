# Design-space register

Updated: October 13, 2026. All evaluations below are author hypotheses. Working slices are not validated final interfaces. “Registered” means described for exploration, not implemented.

| Family | Starting model / interaction | Strongest use | Principal risk / falsification | Evidence state |
|---|---|---|---|---|
| F01 Journey board | Form → ranked options → shared time lanes | Precise travel comparison, readable keyboard baseline | People cannot express constraints or distinguish local clocks; cards become repetitive | First-round working slice, 91-check aggregate audit |
| F02 Atlas workspace | Airport/map exploration → linked option/time/evidence | Spatial discovery, alternate hubs, network orientation | Dense curves or labels hide choices; map clicks confuse a leg with an itinerary | Revised working slice, 117-check aggregate audit |
| F03 Earth studio | Globe + selected schedule cursor and list equivalent | Global geometry and journey phase | Occlusion/camera effort slows common tasks; schedule dots mistaken for live aircraft | R02/R04 slices plus R05 current-route fitting and keyboard/touch recovery; actual devices and customer validation pending |
| F04 Date explorer | Coverage-aware calendar → date-specific journey options | Flexible departures and service-day discovery | Unknown looks like zero; minimum duration looks like fare; broad calendar implies daily service | New breadth-round working slice |
| F05 Arrival planner | Destination-local deadline → departure/arrival tradeoff and readable options | Reach a meeting, trade later departure against earlier arrival | Endpoint zones misread; chart requires too much literacy; no-match recovery silently changes date | New breadth-round working slice |
| F06 Journey builder | Select dated leg → next possible departure → inspect/undo | Required hubs, stopover exploration, enthusiast construction | Dead ends, uncertain transfers and stale leg changes; choice explosion | Breadth slice plus R06 flexible window, city transfer and suffix rebuilding in browser/Python; live data/density/locks pending |
| F07 Evidence workspace | Date/cohort table → provenance and coverage → paired comparison | Research, reproducible service inspection | Dense table hides methodology; partial coverage mistaken for route removal | New breadth-round working slice |
| F08 Guided brief | One question at a time → editable structured query → results | Novices with an incomplete mental model | Slower for repeat use; hidden interpretation and poor backtracking | Registered; test against equivalent compact form, no parser/LLM assumed |
| F09 Network topology | Hub/edge graph or adjacency matrix + geographic counterpart | Connectivity, bottlenecks, carrier/network structure | Topology mistaken for distance or temporal feasibility | Registered; use explicit schematic labels and dated edges |
| F10 Comparison workbench | Shortlist as persistent columns + shared clock, coverage and differences | Deliberate tradeoff decisions and stakeholder discussion | Wide layout fails on phones; stale pins produce misleading side-by-side facts | R04/R05 selected + one compared journey, paired local dates, exact role swaps, retained exclusions and shared state; full multi-option workbench and live revisions registered |
| F11 Temporal small multiples | Repeating timetable, calendar/cohort facets, paired geographic views | Weekly patterns, seasonality, network changes | Unlike weekday/coverage cohorts create false differences | Registered; first evidence/date slices cover only dated subsets |
| F12 Space-time studio | 3D time extrusion, event graph or synchronized geographic/time camera | Expert investigation of temporal network structure | Occlusion, altitude semantics and cognitive/device cost overwhelm benefit | Registered/deferred pending a task that simpler time lanes cannot answer |

## Important combinations to compare

- F01 + F02: shared workspace versus separate map/list task pages. Test context retention and how novice users find the map.
- F04 + F05: flexible departure calendar with a fixed arrival deadline. Preserve the distinction between query dates and display cursor; expose which dates have coverage.
- F06 + F02: dated next-leg construction with geographic orientation. Map topology must not imply a valid chronological connection.
- F07 + F11: reproducible period comparison with matched cohorts and coverage. Compare direct movement counts separately from connecting journey counts.
- F08 + F01: guided intent versus direct form. Both must produce the same visible canonical query and permit correction before search.
- F10 across F01–F07: shortlist as shared persistent state, not a separate dataset. Revised/cancelled/missing pins stay visible as evidence, never silently substitute.
- F03/F12 versus F01/F02: immersive mechanisms must demonstrate a task advantage and have equivalent non-spatial access.

These combinations are the exploration backlog, not an instruction to fit all mechanisms into one screen.

The [R04 visual-direction campaign](refresh-direction.md) applies materially different emphases to the same dated journey fixture: spatial Airlight, time-first Journey canvas and immersive Orbit studio. It partially combines F01–F05 with F10. It does not count three visual treatments as three new interaction families, and does not implement the outstanding guided, topology, recurring-time or 3D-time models.

The owner chose to [refine all three before choosing](refinement-round.md). R05 preserves their different emphases while improving comparison identities, phone dates and camera recovery. The measured phone tradeoff is explicit: shorter default body content and earlier paired local-day evidence, with the first map/time graphic farther down. Customer observation must decide whether that placement helps each task.

R06's [connection/globe experiment](connections-round.md) combines F06 with F02/F03/F10. Globe first, Map first and Connections first compare placement on the same data and plain geeky styling. These are layout alternatives, not three new interaction families or approved default choices. Phone task views make map/editor access earlier while moving local endpoint comparison into Schedule; that tradeoff needs observation.

## Product-pattern research

Primary sources reviewed October 10, 2026:

- [Google Flights help](https://support.google.com/travel/answer/2475306?hl=en) documents date/calendar exploration and stop/airline filters. [Google's product article](https://blog.google/products-and-platforms/products/search/how-to-save-money-google-travel-2024/) describes map exploration and flexible-date comparison. We infer that date and map entry patterns may be familiar; Flyji's time/coverage metrics do not have fare semantics.
- [FlightConnections](https://www.flightconnections.com/) presents airport-driven geographic discovery of direct and connecting options. It provides a comparator for spatial entry, not proof that Flyji should use its default or claims.
- [Flightradar24's playback article](https://www.flightradar24.com/blog/inside-flightradar24/playback-is-now-available-in-the-flightradar24-app/) documents historical playback. It motivates testing temporal exploration; Flyji's interpolated synthetic schedule is a different evidence basis and must say so.
- [NN/g's design-method article](https://www.nngroup.com/articles/parallel-and-iterative-design/) supports combining divergent candidates, revisions and comparative testing. It does not validate our hypotheses or replace customer sessions.

No competitor task study, private customer data, recruitment or external communication has been performed. Public patterns are research inputs. Future competitive task observations should use the same benchmark questions, record coverage/scope differences, and avoid comparing booking capability with an unpriced route explorer as though they were interchangeable.
