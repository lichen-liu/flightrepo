# Customer questions and interaction range

R09 · 13 October 2026 · 84 candidate questions across 21 areas. These are hypotheses, not a surveyed preference ranking. Every response below is a candidate product requirement; it is not an implementation claim. Existing N01–N26 remain traceable in the [structured register](register.json).

`semantic_gate` means an interpretation/access requirement must be checked in every relevant slice. `validate_value` means an unanswered value assumption could change product scope. `explore` means compare alternatives after the foundational risks. These are planning categories, not measured popularity.

Source references in the register supply context only, not proof of the need, priority or proposed response. Where the narrow source does not relate directly, references are empty: the row is an owner-directed author hypothesis. Public evidence records are in [evidence](evidence.md); tasks T01–T26 are defined in [validation](validation.md).

## Trip intent

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U01 | What can I reach without choosing a destination first? | City/airport, dates, optional region | Reachable dated destinations as list/map | T02 |
| U02 | Which complete journey reaches my destination? | Exact places and departure window | Ranked dated options with elapsed time | T06 |
| U03 | Can I arrive before a fixed event? | Destination-local deadline, date flexibility | Feasible arrivals and visible day/zone consequences | T03 |
| U04 | How do I return or visit several places? | Ordered places and stays, return date | Separate dated sequences and ticket boundaries | T25 |

## Places and ground access

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U05 | Can I search without knowing an airport code? | Name, city, region, aliases | Disambiguated airport identity and location | T02 |
| U06 | Which airports belong to this city? | City selection and exact-airport override | Named airport alternatives without merging vertices | T04 |
| U07 | How much time does getting to/from the airport add? | Starting/final place and transport assumptions | Door-to-door components with unknown ground time | T17 |
| U08 | Would rail or another nearby airport make more sense? | Alternative city/airport/mode | Comparable alternatives without invented transport facts | T17 |

## Dates and clocks

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U09 | What changes on another departure day? | Exact day or flexible inclusive range | Date-specific counts with coverage state | T08 |
| U10 | Which local day do I leave and arrive? | Local dates with airport zone | Both endpoint dates/offsets and elapsed duration | T03 |
| U11 | How do repeated/skipped clocks or date-line crossings work? | Local clock with explicit ambiguity choice | Resolved instant or explanation requiring correction | T03 |
| U12 | Is this a repeating timetable or one dated instance? | Service period and evidence basis | Separate recurrence claims from loaded instances | T08 |

## Connection requirements

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U13 | Can I connect at the same airport? | Personal minimum/maximum gap | Elapsed wait and unverified operational requirements | T04 |
| U14 | Can I change airports within a city? | Opt-in exact arrival/departure airports | Directed ground segment with assumption source | T04 |
| U15 | Can I wait overnight or until a specific time? | Per-gap not-before, local stay date or no maximum within horizon | Dated onward choices, exact waits and bounded horizon | T05 |
| U16 | What baggage, border, terminal or check-in steps apply? | Ticket/terminal/baggage context if known | Known requirements separately from unknowns | T04 |

## Journey construction

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U17 | Can I select each next leg myself? | Dated choice after current arrival | Candidate continuations and visible dead ends | T05 |
| U18 | Can I change the middle without losing the beginning? | Exact prefix and selected next flight | Preserved prefix, updated suffix, changes and Undo | T05 |
| U19 | Can I keep a later flight fixed while editing? | Optional explicit downstream lock | Feasibility result or explained conflict, never silent unlock | T05 |
| U20 | Can I visit or avoid a particular hub? | Required/ordered via and avoided airports | Constrained choices with exclusion/recovery reasons | T15 |

## Tradeoffs and comparison

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U21 | Which option spends least time traveling or waiting? | Explicit duration/arrival/stops sort | Transparent order and proportional time lanes | T06 |
| U22 | What do I gain by accepting another stop? | Pin comparable dated journeys | Aligned differences in arrival, wait, travel and unknown facts | T06 |
| U23 | Can I avoid exhausting times or long airport waits? | Personal flight-time/wait constraints | Day/night consequences; no invented comfort score | T06 |
| U24 | Why is this recommended and what was excluded? | Visible query and ranking rule | Deterministic explanation, ties, limits and repair options | T15 |

## Commercial decision

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U25 | Can I afford the total trip including extras? | Budget, baggage, party and currency | Verified total cost or explicit unavailable status | T01 |
| U26 | Can I actually buy these flights together? | Inventory/date/party/ticket context | Verified availability and protection or unknown boundary | T18 |
| U27 | What happens if I cancel, change or miss a leg? | Ticket/airline terms and disruption context | Source-backed options or direct authoritative handoff | T18 |
| U28 | Which cabin, baggage and loyalty rules fit me? | Carrier/cabin/allowance preferences | Dated commercial facts without guessing from flight number | T18 |

## Personal circumstances

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U29 | Will this work for children or companions? | Group needs and acceptable pacing | Readable tradeoffs and facts needing confirmation | T16 |
| U30 | Can I travel with mobility or other assistance needs? | Voluntary relevant requirements | Accessible interface and verified assistance facts separately | T16 |
| U31 | What if I need medication, equipment or pet arrangements? | Relevant needs without unnecessary sensitive details | Airline/airport confirmation paths, explicit unknowns | T16 |
| U32 | Can I reduce stress or get help when uncertain? | Optional explanations and human-source links | Plain factual guidance without unsupported reassurance | T16 |

## Trust and revisions

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U33 | Does no result mean there is no service? | No additional input burden | No loaded matches vs missing/partial/error distinctions | T07 |
| U34 | How old and complete is this information? | Source/time/coverage detail on demand | Observed refresh separate from provider coverage | T07 |
| U35 | What if my selected flight changes or disappears? | Explicit recheck of saved dated identities | Old/new facts and reevaluation without substitution | T14 |
| U36 | Was this scheduled, cancelled or actually flown? | Status and evidence-basis filters | Scheduled/actual evidence, provenance and unknowns | T11 |

## Geography and playful exploration

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U37 | Where does this journey go geographically? | Linked leg/card selection | Exact airport locations and illustrative great-circle lines | T09 |
| U38 | Can I stay oriented while exploring a global route? | Sphere/flat/topology/list, Face/Fit and leg focus | Stable selected identities and explicit off-view recovery | T09 |
| U39 | Can I tell a flight line from a ground transfer? | Select either segment | Distinct segment shape, name, timing and assumption | T04 |
| U40 | Can exploration be enjoyable enough to revisit? | Optional event stepping, discoveries and saved views | Playful factual consequences with no automatic motion | T19 |

## Time visualization

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U41 | When is each leg flying, waiting or completed? | Schedule cursor or event step | Synchronized phases explicitly illustrating a schedule | T09 |
| U42 | Can I compare elapsed timing and local dates across journeys? | Shared axis with local endpoint dates or equivalent facts | Scaled flight/wait lanes and readable alternatives | T06 |
| U43 | What departure/arrival tradeoffs fit a deadline? | Time bounds and optional brush | Chart/list equivalent with query semantics distinct from cursor | T03 |
| U44 | How do patterns vary by day or period? | Week/day/cohort selection | Calendar/timetable/small multiples with coverage denominators | T11 |

## Network and enthusiast inquiry

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U45 | Which hubs or destinations connect this network? | Airport/carrier/date/region | Map, schematic or adjacency view with dated edges | T02 |
| U46 | Which carrier or aircraft operates a movement? | Operating/marketing carrier and aircraft criteria | Source-backed identity without codeshare double counting | T11 |
| U47 | Can I search inbound to a fixed destination? | Open origin/region and arrival window | Reachable origins with complete dated timing | T02 |
| U48 | Can I discover unusual routes or reconstruct a flight? | Known flight/airport/time and filters | Matching evidence and uncertainty rather than invented track | T19 |

## Research and reproducibility

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U49 | Has service grown, moved or disappeared? | Matched periods/carriers/weekdays | Comparable cohorts with unknowns separated from removal | T11 |
| U50 | Can I reproduce or export this result? | Export query, IDs, snapshot and methodology | Readable artifact and exact evidence provenance | T12 |
| U51 | Are duplicate/codeshare records inflating counts? | Movement basis and normalization rule | Raw vs normalized counts and documented identity | T11 |
| U52 | What limits make the conclusion incomplete? | Coverage/loading/search/trace bounds | Distinct truncation dimensions, no false exhaustive ranking | T13 |

## Workflow and recovery

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U53 | Can I save and return to an exploration? | Local shortlist/query link/export | Restored intent with stale evidence labeled | T14 |
| U54 | Can someone else understand what I found? | Readable share/export and query summary | Context, source, time basis and limitations retained | T12 |
| U55 | Can I undo, retry or correct input without starting over? | Undo/retry/edit and explicit submit | Retained draft/results, latest-response state and clear change | T15 |
| U56 | Can I compare alternatives without repeated reentry? | Pins and presentation switching | Stable query, selection, cursor and camera | T06 |

## Access and device

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U57 | Can I complete the task using keyboard or screen reader? | Native controls and equivalent route/schedule detail | Named focus/state changes and no map-only information | T10 |
| U58 | Can I use touch without precision dragging? | Single-pointer controls and mobile views | Discoverable selection/rotation alternatives and usable targets | T09 |
| U59 | Does it work with zoom, low vision or reduced motion? | Text zoom, appearance, reduced motion | Reflow, contrast, patterns and controlled movement | T10 |
| U60 | Does it remain usable on slow or low-power devices? | Small/dense data and degraded-network fixtures | Responsive feedback, bounded work and useful fallback | T13 |

## Language, privacy and wider impact

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U61 | Can I understand the terminology and dates in my locale? | Names, dates and optional unit/clock preferences | Plain standard labels, unambiguous local date/zone | T20 |
| U62 | Can I explore without an account or sharing sensitive plans? | Local save and explicit share action | Clear storage/sharing boundary and removal control | T20 |
| U63 | What is the environmental impact and could I travel differently? | Comparable modes and explicit method if available | Source-backed estimates with method/uncertainty or unavailable | T17 |
| U64 | Can I learn the tool without losing my task? | Brief examples, progressive detail and repeat-user shortcuts | Helpful starting points with editable real intent | T24 |

## Exploring and selecting options

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U65 | Can I browse interesting flights before defining a whole trip? | Place/date/flight as an editable starting point | Scoped dated options and reachable neighborhoods with coverage | T21 |
| U66 | Am I selecting one flight, a shared map edge or a complete journey? | Explicit dated flight/journey identity | Clear selection scope and synchronized details | T21 |
| U67 | Can I compare several options while keeping a reference? | Persistent pins and explicit sort/metric | Shared-scale timing, identity and differences without hidden scoring | T21 |
| U68 | Can I return quickly to an interesting option while browsing? | Local pins and browse history | Preserved exact option and stale-status distinction | T21 |

## Reversible what-if play

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U69 | What would change if I move a date, wait or hub? | Editable what-if constraints with visible scope | Preview differences distinct from applied query and saved facts | T22 |
| U70 | Can I keep important legs fixed while trying alternatives? | Explicit prefix/later-leg commitments and cancel/apply | Constraint conflict or preview; no silent replacement/unlock | T22 |
| U71 | Why did an option or later leg change after an edit? | Before/after identities and changed constraint | Leg-by-leg changes, reasons and exact Undo | T22 |
| U72 | Which small change could recover a dead end? | Named candidate date/gap/stops/hub relaxation | Computed alternatives under the same stated coverage/budget | T22 |

## Analytical depth

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U73 | Which flights overlap or offer different departure/arrival tradeoffs? | Flight cohort, shared clock and selected metrics | Aligned schedule/chart and readable facts with local dates | T23 |
| U74 | Can I isolate a carrier, date or reachable network around my choice? | Clearly applied cohort/filter vs local highlighting | Selected/context layers with counts and scope preserved | T23 |
| U75 | Can I inspect the evidence behind an interesting option? | Flight detail, status, source and method expansion | Progressive dated evidence with unknown fields explicit | T23 |
| U76 | Which alternatives improve one metric without hiding another? | Explicit arrival/stops/wait/elapsed comparison | Tradeoff set or matrix, no unverified weighted best | T23 |

## Exploration continuity and control

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U77 | Can I move between local detail and global geography easily? | Leg/airport focus, zoom/context and flat/sphere switch | Stable selected facts; camera changes do not change the query | T24 |
| U78 | Can I move quickly through options without precision gestures? | Named previous/next/event controls and shortcuts | Predictable focus, exact identities and non-drag touch parity | T24 |
| U79 | Can I backtrack through exploration and explain my changes? | History/undo with dated query and edit summaries | Recoverable state and understandable previous-versus-current facts | T24 |
| U80 | Can I reveal more analytical detail as I learn? | Progressive controls and optional compact view | Useful dense detail while plain names/access paths remain | T24 |

## Service reliability and stay constraints

| ID | Customer question | Candidate input | Candidate response | Validation |
|---|---|---|---|---|
| U81 | What does historical disruption evidence say about this option? | Matched route/operator/period and operational evidence | Sample and missingness with no unvalidated future reliability score | T26 |
| U82 | Can I specify nights or local days in an ordered stopover? | Ordered places, local stays and exact fixed flights | Date-consistent sequence and conflicts, separate ticket boundaries | T25 |
| U83 | Can I explore trips within a leave or total-time budget? | Departure/return window, stay length and elapsed cap | Complete bounded sequences and clear infeasible/unknown cases | T25 |
| U84 | Can each connection have its own hard or preferred wait? | Per-gap minimum/not-before/maximum and preferences | Named constraint conflicts and explicit rebuilt suffix choices | T25 |

## Breadth limits

The inventory samples a large space; it cannot establish every possible need. Nationality/visa, terminals, accessibility assistance, commercial terms, emissions, rail/ground service, aircraft and operational performance need appropriate evidence before answering them. A future user task can add a row or challenge a whole category. None of these gaps is permission to guess or create a paid service.
