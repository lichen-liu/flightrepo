# Customer-value review

13 October 2026 · Round 1 · reviewer: `/root/customer_advocate`

Status: desk research and design hypotheses. No Flyji customer interviews, observed customer tasks, recruitment, usability sessions or customer approval occurred in this review. Public sources establish the limited facts below; they do not establish which Flyji features people want most. The owner's instruction is to serve travelers, enthusiasts and researchers, retain concise geeky language, and explore all three visual alternatives before choosing.

Inputs read: [customer needs](../../design/customer-needs.md), [exploration program](../../design/exploration-program.md), and [R08 implementation](../../product/README.md). Existing N01–N26 remain valid hypotheses. The new V IDs below expand them rather than replacing their meanings. This review does not authorize the implementation of every candidate feature.

Input SHA-256 at this review:

```text
customer-needs.md     bb2d72413e40f28e8262f2663b8dc5fc268017dfdd7da421976dee7ee82d91fc
exploration-program.md 53c616a5366f7845fd3c76c4b396b596ea01a51b6b6a29bc3645bc81893e023a
product/README.md     1bed41f7cc40332dddfb9d0edf59aa379538daec4cde84efed86a120e7f4838f
```

## What deserves the strongest challenge

A person may want a journey they can afford, buy and comfortably complete. Flyji currently answers dated schedule and route questions, while price, inventory, protected ticketing, baggage, border entry, actual transfer procedure and accessible assistance remain unknown. That boundary can defeat traveler value even when routing is mathematically correct. Testing whether people find meaningful value before handing off to another product is a first-order product question. Treat “they liked the map” as insufficient evidence of value or repeat use.

For enthusiasts, an engaging geography and schedule workspace may be valuable without fares. For researchers, reproducible, documented queries may be valuable without booking. Those are defensible hypotheses, not demonstrated demand. A visually rich view needs an identifiable task or an explicitly tested exploration/learning benefit; novelty alone cannot justify making it mandatory.

## Primary-source ledger

All sources below were searched or opened on 13 October 2026. Descriptions are deliberately narrow. They are evidence of public product behavior, operational distinctions, standards or published methods; only A02 contains passenger survey findings. Its population is not Flyji's future users. No full paid survey report was acquired.

| ID | Source and verified fact | Legitimate use / limit |
|---|---|---|
| A01 | [Google Flights help](https://support.google.com/travel/answer/2475306?hl=en) documents cities/airports, one-way/return/multi-city, calendar, stops/carrier/time filters, sorts, alternative airports and separate tickets. It describes missing offers and commercial baggage/transfer qualifications. | Shows existing search conventions and a commercial decision context. Does not prove familiarity, customer preference, or Flyji demand. |
| A02 | [IATA 2025 GPS release](https://www.iata.org/en/pressroom/2025-releases/2025-11-05-02/) reports more than 10,000 responses across more than 200 countries, smartphone usage and regional differences in convenience, loyalty, border complexity and privacy. | Supports researching multiple contexts and mobile use. The public summary is industry-authored; it does not support one universal priority order, a Flyji persona estimate, or an adoption prediction. |
| A03 | [Heathrow connections](https://www.heathrow.com/connecting-flights) distinguishes single-ticket connections from separate flights, baggage/immigration/check-in/security tasks and route-specific timing. | Demonstrates that generic scheduled gaps cannot establish passenger feasibility. It is airport-specific guidance, not a universal transfer model or advice for an individual traveler. |
| A04 | [UK transit overview](https://www.gov.uk/transit-visa) distinguishes border-control circumstances and directs people to a case-specific visa check. | Demonstrates an omitted eligibility dimension. Flyji must not give a universal visa answer based on a city or wait alone. |
| A05 | [US DOT disability travel guidance](https://www.transportation.gov/individuals/aviation-consumer-protection/general-travel-tips-persons-disabilities) describes connecting assistance, seating and assistive-device planning with airlines. | Real-world accessibility is distinct from accessible software. Scope is US guidance; no global rules or guarantees are inferred. |
| A06 | [W3C dragging movements](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements) explains a single-pointer alternative to dragging, separately from keyboard operation; a directional-button map is an example. | Inspect both keyboard and non-drag pointer paths. Reading guidance or passing one test is not WCAG conformance certification. |
| A07 | [OpenSky FAQ](https://opensky-network.org/about/faq) distinguishes measured tracks and derived endpoints from commercial schedules, cancellations, delays and passenger counts; it notes uneven geography and historical limits. | Data type and observation coverage need explicit semantics. Its limitations are not evidence of Flyji provider coverage or current data availability. |
| A08 | [OpenSky 2019–2020 dataset paper](https://essd.copernicus.org/articles/13/357/2021/) describes crowdsourced data and uses in aviation, environmental and socioeconomic research, with dataset limitations. | Establishes research task examples, not demand for Flyji or validity of an analysis on its records. Historical dataset quantities are not current coverage estimates. |
| A09 | [BTS carrier performance](https://www.bts.gov/topics/airlines-and-airports/airline-arrival-performance-carrier) offers date/airport/pair/carrier breakdowns and distinguishes marketing from operating/reporting carriers. | Supports testing analytical units and identity distinctions. A performance page does not establish reliability of an individual future itinerary. |
| A10 | [Google emissions methodology](https://support.google.com/travel/answer/11116147?hl=en) distinguishes estimates, contributing factors, sources and unknown emissions. | Demonstrates a possible customer dimension and the need for methodology. Flyji has no verified emissions model; distance alone cannot support a green badge. |
| A11 | [Brehmer and Munzner task typology](https://www.cs.ubc.ca/labs/imager/tr/2013/MultiLevelTaskTypology/) separates why a visualization task is performed from how, inputs and outputs. | Use a task description before picking a representation. It supplies a method, not proof that a particular flight interface works. |
| A12 | [FlightConnections](https://www.flightconnections.com/) presents route maps, airport/airline discovery, schedules and direct/connecting options. | Establishes an existing spatial exploration strategy. Promotional completeness claims are not independently audited here; no endorsement or demand estimate follows. |
| A13 | [Lam and Munzner interface synthesis](https://www.cs.ubc.ca/tr/2010/tr-2010-11) discusses matching display/data levels, task-relevant overviews and simultaneous views for multi-level answers. | Generates linked-view hypotheses. It does not decide Flyji's phone layout or guarantee that more simultaneous panels are better. |

The parent review separately verifies the CAA Wave 14 passenger survey. Its quantitative findings belong in the shared evidence ledger; this document does not duplicate them or convert investment priorities into flight-choice rankings.

## Contexts that the current audience names hide

Do not ask people to declare a persona. Ask what they are trying to find or explain. The same person can switch tasks in a session.

| Context | Starting knowledge | Meaningful outcome | Plausible reason to leave |
|---|---|---|---|
| Fixed event, work or family obligation | Destination and required arrival; dates may cross zones | A schedule candidate meeting the real destination deadline with understandable uncertainty | Meeting is outside the airport, ticket price or transfer assurance decides the choice |
| Flexible break or destination discovery | Home region, duration, season, budget | A few destinations worth further checking | Empty inventory, unknown cost or broad coverage makes exploration inconclusive |
| Complex self-built trip | One booked leg, a preferred stopover, several permissible airports | Edit one part, see every downstream consequence, keep fixed parts | Flyji silently moves a booked leg or calls a geographic transfer safe |
| Family, group, mobility or assistance needs | Shared dates and differing comfort/access constraints | A candidate that makes the group's unresolved requirements visible | Shortest elapsed route is unusable for one person |
| Enthusiast investigation or learning | Carrier, airport, aircraft interest, known flight or geography question | Inspect dated evidence, compare patterns, retain an interesting slice | A decorative map cannot reveal operating identity or actual observations |
| Research, journalism or network analysis | Question, time period, cohort, unit and evidence threshold | A reproducible slice with scope, provenance and defensible denominators | Unknown coverage or double counting makes the claim unpublishable |
| Urgent interruption or reschedule | Existing choice, current disruption, deadline | Know what changed and which current evidence remains valid | A stale schedule is mistaken for an operational alternative |
| Casual phone exploration or limited input | Short attention, touch or assistive input, low bandwidth | Get one useful answer without manipulating a globe | Controls hide, density grows, motion or download cost obstructs the task |

These contexts are research sampling dimensions, not proven market segments. Trip purpose, frequency, geographic region, experience, language, disabilities and device may change priorities more than the three audience labels do.

## Expanded needs and interaction range

Every V row is a candidate need or risk to investigate. “Response” describes a truthful design target, not an implemented promise. Sources are rationale only where listed; unsourced rows are explicit design hypotheses. The current R08 scope remains narrower.

| ID / existing N | Customer concern | Input and response to explore | Evidence or validation question |
|---|---|---|---|
| V01 · N04,N09,N19 | Can I reach the actual meeting/home/hotel on time? | Distinguish airport arrival from destination arrival; permit explicit user allowance | Hypothesis: ask for the actual endpoint and observe whether airport-only results mislead |
| V02 · N03,N05 | Can I avoid lost sleep or unusable time? | Local departure/arrival ranges, overnight preference, length of a stop in local days | Hypothesis: identify a real sleep/work constraint; do not equate “overnight” with one calendar rule |
| V03 · N05,N19 | Can my group tolerate this? | Separate hard requirements from preferences; reveal conflicting constraints | A05; test family and assistance contexts rather than assuming one comfort score |
| V04 · N01,N09 | I know a city/region, not an airport code | Search by names, ambiguity resolution, city versus exact-airport scope, clear included airports | A01; test ambiguous names and airports outside city boundaries |
| V05 · N01,N10 | Destination or origin is open | Geographic or named-region scope; dated reachable list with explicit coverage | Hypothesis: test which fixed endpoint people actually know |
| V06 · N03 | I know a holiday duration, not exact dates | Departure window plus stay length, weekend/leave constraints, departure-versus-arrival dates | Hypothesis: compare a date grid, form and constrained interval; avoid pretending recurring service is dated availability |
| V07 · N11 | Return need differs from outbound | Open-jaw/return/multi-city groups, separate local dates and retained shortlists | A01; current single-direction candidate must not imply a ticket bundle |
| V08 · N06,N07,N21 | Some legs are already booked or non-negotiable | Explicit fixed leg/airport/stay constraints; preview consequences of edits | Hypothesis: preserving prefix alone is insufficient if a downstream leg is fixed |
| V09 · N07,N22 | I want a stopover rather than the earliest possible onward flight | Not-before, local stay date, optional max wait, destination change; name rebuilt legs | Hypothesis: exact departure choice and requested stay are different actions |
| V10 · N05,N09,N23 | What am I responsible for during the connection? | Exact airports/terminals where known, ticket context unknown state, transfer steps and assumptions | A03,A04; never infer protection, baggage through-check or entry eligibility from elapsed minutes |
| V11 · N19,N23 | Does changing airports add money and work? | Separate airport-change segment, user allowance and known/unknown transport facts | Hypothesis: ask what would make the person reject a same-city option |
| V12 · N05,N19 | How much margin do I want? | User-chosen timing buffer distinct from published/commercial minimum and reliability evidence | A03; no “safe connection” label from an arbitrary margin |
| V13 · N06,N19 | Can I avoid a border or country I cannot enter? | Avoid transit location as a geographic filter; route-specific eligibility remains externally checked | A04; no inferred personal visa status or collection of passport data by default |
| V14 · N19 | What is the total trip cost? | Price/inventory/taxes/bags/ground/hotel unknown boundary, later verified handoff if supported | A01; traveler value gate, not a deferred decoration |
| V15 · N19,N20 | Does this fit baggage, cabin, assistance, children or equipment needs? | Separate flight facts from requirements to confirm with every operator | A05; current data cannot substantiate a successful accommodation |
| V16 · N06,N20 | Which airline actually operates it, and what does my preference mean? | Marketing/operating identities; carrier versus alliance/loyalty preference | A09; test whether people count codeshares as different options |
| V17 · N13,N26 | Is this still scheduled, cancelled, delayed or actually flown? | Scheduled/estimated/actual distinctions, observation timestamps and changed-selection comparison | A07; a stale record cannot establish a current operational option |
| V18 · N05,N13 | Is a route likely to disrupt my plan? | Historical operational evidence with scope and sample; never a schedule-based guarantee | A09; future reliability inference needs its own model and validation |
| V19 · N15,N26 | Why did this disappear or change? | Old versus refreshed selection, exact changed fields, explicit reevaluate and undo | Hypothesis: test whether saved old evidence is mistaken for current validity |
| V20 · N08 | What am I giving up if I choose this option? | Common-scale time comparison, stops and airport changes, visible tradeoffs; pin candidates | Hypothesis: compare explicit sorts with a Pareto-style shortlist; avoid unexplained weighted “best” |
| V21 · N03,N06,N15 | What single change would create a candidate? | Computed counterfactual: date/stops/avoidance; distinguish unknown data from exclusion | Hypothesis: any suggested recovery must actually be computed under named scope |
| V22 · N14,N24 | Where is this journey on Earth? | Exact airport focus, optional globe, flat map and named list; visible date/selection | A11,A12; measure spatial interpretation, not just enjoyment |
| V23 · N14 | Is this aircraft really here? | Explicit schedule illustration versus observed track; separate geometry/time cursor/search state | A07; ask people to explain what the moving dot represents without prompting |
| V24 · N12,N18 | How dense/frequent is service, and what changed? | Cohort/table/matrix/topology or map; clear flight/route/movement units and comparable windows | A08,A09; missing observations cannot be counted as route removal |
| V25 · N12,N18,N20 | Do these counts double-count codeshares? | Physical movement identity, marketed records and unresolved duplicates shown separately | A09; unknown deduplication cannot be concealed by a clean count |
| V26 · N15,N18 | What is the denominator and missingness? | Covered period/cohort/source, deliberate non-fetch, errors and partial search states | A07,A08; counts in an observed slice are not market share without supporting population |
| V27 · N13,N18 | Can I compare schedule and operation? | Separate planned and observed cohorts, timestamps, status evidence and matched periods | A07,A09; avoid combining incompatible datasets in one implied complete network |
| V28 · N16,N18 | Can someone reproduce and audit my claim? | Exact query, snapshot, version, source rights, IDs, method and exclusions in export | A08; current replay proves engine reproducibility on retained facts, not real-world validity |
| V29 · N20 | Can I inspect a known flight number, aircraft or service pattern? | Flight instance versus recurring number; operating carrier, equipment where sourced | Hypothesis: missing aircraft facts must remain unknown rather than fabricated from a carrier |
| V30 · N08,N19 | Does the environmental impact matter to me? | Verified estimate and methodology or unknown state; possible non-flight comparison | A10; do not infer emission savings from globe arc length |
| V31 · N16 | Can I discuss a choice with someone else? | Restorable query/selection and readable summary; annotations with explicit old-snapshot state | Hypothesis: copied URL alone may not preserve the same facts or satisfy a group decision |
| V32 · N16,N26 | Can I revisit this later or notice a change? | Local saved queries/shortlist and explicit refresh; consider notifications only after need is observed | Hypothesis: no account, tracking or notification service is justified by this review |
| V33 · N17 | Can I complete it with keyboard, screen reader or limited dexterity? | Equivalent named controls, status announcements, non-drag pointer paths, focus continuity | A06; actual assistive-tech tasks are required before compliance claims |
| V34 · N17 | Can I do it on a phone or weak device/network? | Task-sized views, readable density, recoverable switching, low-motion/list alternative | A02; neither desktop precision nor touch-only globe operation represents everyone |
| V35 · N04,N17,N25 | Can I read the dates and names in my language? | Full local dates, explicit zone, familiar date/time presentation, names alongside codes | Hypothesis: local clocks remain difficult around DST/date-line crossings; test interpretation |
| V36 · N16,N17 | What private information is retained? | Explicit local retention/delete/export; no assumed location or personal travel history | A02; full link/share payloads may disclose plans; no passport collection is needed for schedule exploration |
| V37 · N17,N25 | Can I recover from a wrong action? | Distinct draft/applied query, removable constraints, undo, retained prior facts during failure | Hypothesis: explore state change comprehension, not just button operation |
| V38 · N14,N25 | Is exploration fun enough to return to? | Playful leg focus, event steps and discoverable facts with motion under user control | Hypothesis: delight is legitimate but must coexist with accurate interpretation and task access |
| V39 · N15,N16 | What can I actually do next? | Truthful export/copy/verify-with-provider action; no inactive booking affordance | Hypothesis: measure a downstream decision or useful retained artifact, not screen engagement alone |

The table is deliberately broad. Breadth is a research checklist, not a command to add thirty-nine controls. A few failed tasks can justify simplifying the product or revisiting scope. Research should preserve concerns about commercial, accessibility or border needs even when Flyji cannot implement an answer locally.

## Alternatives to defend and challenge

| Strategy | Strongest customer-value defense | Strongest objection / falsification |
|---|---|---|
| Compact journey query + ranked list and timing | Fast entry for a known trip; directly exposes dated waits and arrival constraints | Could be an inferior travel-search tool without commercial answers; real tasks end elsewhere before a useful decision |
| Date/deadline-first grid or lanes | Makes availability and time tradeoffs visible together; starts from a fixed event or flexible leave | Dense grids can hide exact date meanings and coverage; novice cannot tell whether a cell proves availability |
| Linked flat map/globe + timeline | Makes geography explorable while a separate temporal view carries dates/waits; enthusiast orientation and learning | Hemisphere occlusion/camera motion hide alternatives; moving dot is mistaken for actual tracking; phone cost outweighs value |
| Exact next-leg builder | Gives agency for stopovers and complex itineraries; explains changed suffix rather than forcing restart | Requires too much work for a simple trip; rebuild violates a later fixed leg or users fail to see downstream changes |
| Evidence-first table/topology/matrix | Units, filters and missingness support researcher/enthusiast questions at network scale | Expert vocabulary alienates newcomers; tidy aggregates imply false completeness or physical movement identity |
| Task entry points in a shared workspace | Limits initial controls while preserving transitions across journey/network/service tasks | “Explore network” versus “Inspect service” is unclear; mode choice is extra work and traps the user in a categorization |

These strategies can coexist in a bounded experiment but do not need one universal default. Keep the three visual alternatives comparable on the same dated facts and tasks. A single aesthetic winner is not a substitute for task-specific assessment.

## Debate response: globe, modes and repeat value

The skeptic's first-round objections are accepted: mode-first navigation is not established; a globe is not established as a default; comprehensive needs do not justify an overloaded interface. Test a compact universal query with discoverable actions against task-first entry, retaining facts and selections across transitions.

A concrete globe hypothesis is geographic orientation of a dated journey crossing the antimeridian or near a pole, with exact airport locations and a leg focus. This may reduce confusion caused by a flat map seam and improve playful learning. It does not uniquely require a globe; compare against a well-designed flat map, topology and named leg list. The arc remains illustrative geometry, not evidence of a flight's actual path. For deadline and connection comparisons, directly readable dates/lists/lanes have the stronger initial rationale. No preference or performance result has been observed.

Schedule-only repeat value is plausible for constructing unusual journeys, studying networks and exporting reproducible slices. For ordinary travelers it is especially uncertain. A valid test must ask what decision the schedule view changed and whether the saved artifact is reused, rather than soliciting a promise to return. Participants can choose another tool or conclude the product is unnecessary; that result must survive the review.

## Round-two exchange and scoped agreement

The following records the actual two-way messages exchanged with `/root/evidence_skeptic` on 13 October 2026, in paraphrase. It is a design critique record, not a simulated customer conversation. The [skeptic review](skeptic.md) records that reviewer's perspective. No claim that a final product is in consensus follows from this agreement on what to investigate.

| Issue | Skeptic challenge | Advocate response and reversal evidence | Actual resolution |
|---|---|---|---|
| Entry structure | Mode choice can become navigation baggage; test universal entry and crossing tasks | Compare three task/action entries, compact universal query and contextual actions. Drop mode-first when a real task is misclassified, coaching is necessary, or intent must be re-entered after switching | Both consent to the comparison plan. Final entry/default remains open |
| Globe versus readable list | Antimeridian orientation may be rare and cannot justify a universal globe default | Agree; strongest dissent is that universal list-first is also unproven. An enthusiast may benefit from immediate optional geography. Reverse that stance if useful spatial insight is absent, tracking misbelief persists, camera/occlusion fails or phone cost dominates | Skeptic explicitly accepted the objection to universal list-first; its initial list/time rationale applies to known-trip decisions. Both retain task-specific comparison; no default signed off |
| Uncertainty at a choice | Details-only transfer/commercial unknowns are too late | Agree visibility must precede selection. Repeated full warnings can become unreadable; compare shared concise schedule-only scope plus leg-specific transfer assumptions with per-card scope markers. Failure is inferred purchase/protection, not warning recall | Both consent to comparing marker placement with the interpretation gate. No claim of comprehension before observing people |
| Breadth versus implementation | Large inventory can create control bloat | Keep all needs in research; test a few high-risk/value questions per round. A missing commercial answer may change positioning rather than require immediate feature addition | Both consent to risk-focused iteration. No blanket implementation authorization |
| Sampling and outcomes | Nine initial sessions cannot cover dozens of tasks or access needs | Nine is a historical practical starter, not an exit gate. Use per-question inclusive rounds and explicit task/context coverage; differing task winners should yield task-specific representations | Both consent to revised sampling and reject one combined satisfaction winner |

The advocate's current task-specific starting hypotheses are compact query/list plus time lanes for a known trip; dated reachable destinations and selectable geography for open-destination/network exploration; and explicit cohort/table/matrix with coverage, units and export for service research. Each must be compared against alternatives. The skeptic accepted this scope of agreement and retained dissent from declaring any final default, traveler repeat value or commercially feasible connection established.

Changed view: I no longer defend a mode-first or globe-first general entry; I defend testing useful task-specific geography. The skeptic likewise narrowed a list-first rationale to known-trip decisions. Both shifted from an audience-count starter to per-question inclusive sampling. This is convergence on research decisions and semantic gates, with product decisions pending.

## Falsifiable first experiments

Use recent real examples from participants before assigning synthetic tasks. The following are proposed experiments, all unrun. Each experiment needs a named fixture/truth, alternative order, unprompted attempt, observed outcome, prompting record and explicit interpretation check. Record per task and context; do not combine error-prone commercial planning with pleasurable exploration into one satisfaction score.

| Test | Alternative/task | Evidence that would change the design |
|---|---|---|
| T-A01 · traveler value | Reconstruct a recent trip decision; offer Flyji schedule exploration and the participant's usual method | No independent useful decision before cost/bookability checking weakens traveler positioning; useful rejected-route discovery supports a narrower claim |
| T-A02 · input and modes | Express a known trip, then ask an exploratory service question; compact query versus task-first entry | Incorrect mode choice, re-entry or lost context rejects the entry structure for that task; do not coach the labels |
| T-A03 · dates and comparison | Arrive before a real event, compare overnight/date-line candidates; list/lanes versus grid | Wrong destination date, wait or deadline interpretation is a semantic failure despite eventual completion |
| T-A04 · editing | Retain an earlier leg and a fixed later leg; insert an overnight stop and choose a departure from another city airport | Any silent fixed-leg replacement or overlooked changed leg rejects the mechanism; compare builder with whole-itinerary rerun |
| T-A05 · globe | Locate both airports and explain antimeridian/polar geometry; globe/flat/topology/list | Count orientation errors, hidden options and camera recovery; ask what the line/dot proves. Better appearance alone does not select the default |
| T-A06 · transfer understanding | Explain a same-airport separate-ticket case and a same-city airport-change case, with unknown eligibility | Believing the schedule guarantees bags/protection/entry is a failure; generic warning recall alone does not establish understanding |
| T-A07 · evidence | Compare two periods where one contains deliberate missing source windows and duplicate marketed codes | Calling missing observations route removals or duplicates more physical flights rejects the display and underlying count semantics |
| T-A08 · input/device parity | Complete search, selection, editing and recovery using keyboard, assistive tech, touch without dragging and a phone | Unreachable controls, lost focus/state or necessary hover/globe manipulation fail the relevant task; actual environments must be recorded |
| T-A09 · retained value | Export/share a useful slice; later reopen it after a source change | Mistaking saved evidence for current validity rejects retention semantics. A later useful use supports repeat value, without extrapolating population adoption |

Before running sessions, agree task-specific review criteria. Do not invent success percentages or sample-size certainty. Start qualitative rounds with coverage across the three audiences and relevant input contexts; recruit further when a failure pattern, disagreement or missing context remains. Counterbalance alternatives and avoid asking which screen people like before observing tasks.

## Round-one review attestation

I, agent `/root/customer_advocate`, attest that this document records the inputs I read, primary public sources I accessed, proposed needs, explicit limits and unrun validation tasks. I defend retaining commercial and real-world eligibility questions in research, while preserving schedule-only product scope as an unvalidated value hypothesis. I have accepted the skeptic's objections to claiming a mode or globe default and have not signed off customer demand, accessibility conformance, a final layout, production readiness or owner approval.

This is author review, not independent audit. A subsequent reviewer must identify the exact file revision/hash in their attestation. Any meaningful edit after that review requires re-review. Customer observation and owner decisions remain separate pending gates.
