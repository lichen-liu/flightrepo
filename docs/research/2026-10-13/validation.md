# Customer validation plan

Status: prepared, not recruited or run. No customer, interview, recording, success rate or usability signoff has been invented. Contacting prospective participants is outside this turn; the packet is ready for owner review.

## Sampling and sequence

Start with recent-task discovery across all three audiences, then compare small working slices. The earlier nine-person proposal (three per audience) is a starting discovery batch, not saturation or a release threshold. Include disabled/access-needs participants and varied devices in each relevant round; keyboard-only use is not a substitute for screen-reader participation. Recruit by recent activity, starting knowledge, constraints and device/access context, avoiding stereotypes about age or technical skill. Expand recruitment when an important context has no evidence.

1. Ask about the last real task, current tools, fixed/flexible constraints, consequences of a wrong answer, and what made the decision hard. Avoid introducing maps/globes until the task is understood.
2. Establish the task facts and evidence scope, then test two or three equivalent interactions. Use counterbalanced ordering and matched task/data difficulty; learning and order effects must be logged.
3. Observe a defensible answer and have the participant explain it. Log errors, prompting, recovery, abandoned work, unresolved facts and reasons for non-use. Visual preference is a separate observation.
4. Repair critical interpretation/access errors and retest the affected flow with fresh participants. Reopen the requirement if the task itself was wrong.
5. Continue rounds until important task/context cells have evidence, dissent has a disposition and owner choices can be reviewed. Do not treat a fixed pass count or agent unanimity as a customer result.

GOV.UK [research-round guidance](https://www.gov.uk/service-manual/user-research/plan-round-of-user-research) supports objectives and preparation; [service research planning](https://www.gov.uk/service-manual/user-research/plan-user-research-for-your-service) supports iterative rounds. The specific sequence, sample coverage and decisions here are Flyji proposals.

## Task cards

### T01 · Value without fares

Fixture/context: Recent real traveler task; current booking tools available

Customer outcome prompt: Describe a recent flight decision and try to advance it using the available facts and your usual tools.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Ask for a recent decision before showing Flyji. Observe whether route/timing changes a decision, what remains unresolved, and return to existing tool.

Truth and observation: Record standalone value, useful complement or non-use; do not count a click or aesthetic preference as solved travel planning.

### T02 · Open destination, city and inbound discovery

Fixture/context: Names including ambiguous cities, an open destination and an open origin

Customer outcome prompt: Find a dated destination or origin that fits your own exploration question, using the place names you know.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Compare compact form, linked geographic workspace and guided entry using identical dated evidence.

Truth and observation: Participant identifies correct airport/dated reachability and explains absent/unknown coverage; inbound/open input is an experiment, not current implementation.

### T03 · Deadline, local dates and difficult clocks

Fixture/context: Overnight/date-line journeys; skipped and repeated local clocks

Customer outcome prompt: Find a journey arriving before this event and explain its local days and waiting time.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Set arrival before an event; compare endpoint cards, lanes and optional chart. Explain query vs cursor.

Truth and observation: Resolved instants match independent expected UTC facts; participant correctly explains local days and elapsed time without prompting.

### T04 · Transfer and commercial inference

Fixture/context: Same-airport, directed same-city and reverse-direction disallowed cases; unknown ticket/baggage context

Customer outcome prompt: Explain what you would need to confirm before accepting these two connection options.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Compare concise adjacent transfer disclosure, expandable detail and explicit transfer checklist.

Truth and observation: Participant distinguishes elapsed gap/geographic eligibility from protected or personally feasible transfer; no universal safety inference.

### T05 · Edit a journey

Fixture/context: Exact prefix; immediate/overnight/not-before next choices; dead end; future lock conflict

Customer outcome prompt: Try a different onward flight while keeping the part of this journey you care about; explain what changed and recover your former choice.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Change next leg, inspect changed suffix, Undo; compare editor placements and explicit later-lock concept.

Truth and observation: Exact unchanged prefix and explicitly chosen next flight retained; later replacements disclosed; Undo restores old facts; lock conflict explicit; task evidence decides effort/discoverability.

### T06 · Compare and retain context

Fixture/context: Three identical-route/different-day journeys; meaningful wait/arrival differences

Customer outcome prompt: Compare these journeys and explain a tradeoff that matters to you; retain a reference while exploring.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Compare list/time, linked 2D and optional globe layouts; pin/swap and change presentation.

Truth and observation: Explain sort, local dates and tradeoff; presentation changes retain IDs/cursor/query; explicit role swap changes only roles; query changes label excluded pins and preserve old evidence; record interpretation errors and needless camera actions.

### T07 · Empty and incomplete evidence

Fixture/context: No matches, missing metadata, unknown coverage, partial search and source error

Customer outcome prompt: Explain what these empty or incomplete results establish and what you would do next.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Ask what each result establishes and how to recover.

Truth and observation: No participant claim of nonexistent service from missing data; UI states remain distinct; any critical false inference triggers repair/retest.

### T08 · Flexible dates and recurring service

Fixture/context: Some dated flights, an intentionally missing day and a known covered no-match day

Customer outcome prompt: Explore different departure days and explain what the available evidence says about service.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Use calendar/table alternatives and date range; explain daily-service inference.

Truth and observation: Unknown is distinct from zero; selected dates correctly constrain initial departure; recurrence is not inferred from sparse rows.

### T09 · Globe/flat map on phone

Fixture/context: Occluded long-haul route, city transfer, touch and non-drag pointer input

Customer outcome prompt: Understand where this route goes and when its legs occur; find a leg that is hard to see.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Locate a leg, Face/Fit, switch flat/sphere and time cursor; compare task effort with equivalent list.

Truth and observation: Correct dated choice and phase; schedule marker not interpreted as measured live position; pointer alternatives work; camera cost recorded.

### T10 · Inclusive critical flow

Fixture/context: Keyboard, assistive technology, zoom/reflow and reduced motion; participant access needs

Customer outcome prompt: Complete your search, comparison and change task using your usual input/access tools.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Search, compare, edit, undo and explain limitations without map manipulation.

Truth and observation: Focus/status and complete task reviewed manually; automated checks are supporting evidence, not full WCAG conformance.

### T11 · Network and historical inference

Fixture/context: Matched/unmatched periods, duplicate codeshares, cancellations, scheduled-only and actual evidence

Customer outcome prompt: Investigate this service-change question and explain the basis for your counts and conclusion.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Compare evidence table, small multiples and topology; explain counts/additions/removals.

Truth and observation: Unknown coverage cannot establish removal; normalized movement basis and comparable denominator visible; aircraft facts remain unknown unless sourced.

### T12 · Export and reproducibility

Fixture/context: Canonical query, exact records/metadata and snapshot; recipient without prior context

Customer outcome prompt: Retain a result so another person can understand and reproduce your explanation.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Export, restore/replay where available and explain method from artifact alone.

Truth and observation: Dated identities, method, engine/snapshot and scope survive; query-only link is distinguished from a frozen reproducible snapshot.

### T13 · Density and operating limits

Fixture/context: 1/10/100/1000 options and representative dense networks; slow response

Customer outcome prompt: Find and inspect the options you need in this crowded or slow-response case.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Compare list paging, selected-context map and overview/detail; test loading/truncation and low-power fallback.

Truth and observation: No inconsistent selection or false total; CPU/memory/latency measured on named devices; any proposed budget labeled unvalidated until measured.

### T14 · Stale saved selection

Fixture/context: Saved flight rescheduled/cancelled, metadata revised and restored old snapshot

Customer outcome prompt: Revisit this saved choice after new facts arrive; explain what remains valid and what changed.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Reopen, reevaluate, compare old/new and recover.

Truth and observation: Old evidence visible; stale identities not silently substituted; explicit decision required before applying a change.

### T15 · Constraints and failure recovery

Fixture/context: Via/avoid conflict, invalid clock, network error and superseded request

Customer outcome prompt: Correct a problem in this exploration without losing the useful work you have done.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Correct the query, relax explicitly, retry, undo and preserve comparison.

Truth and observation: Draft/applied query distinction clear; no silent relaxation; latest state coherent; logs identify actual failure and recovery.

### T16 · Personal and access circumstances

Fixture/context: Real voluntarily supplied companion/accessibility/assistance context

Customer outcome prompt: Explain how these options fit the requirements of your party and what still needs confirmation; include conflicting needs.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Ask what must be known before accepting a route; avoid collecting irrelevant sensitive details.

Truth and observation: Capture unmet requirements and authoritative confirmation path; accessible app does not imply accessible journey.

### T17 · Door-to-door and environmental tradeoffs

Fixture/context: Alternative airports/rail/ground assumptions with unknown costs/times/impact

Customer outcome prompt: Compare getting to the actual destination using these alternative airports or transport assumptions.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Compare component breakdown and unknown indicators against airport-only ranking.

Truth and observation: Cannot rank door-to-door time or emissions without supported values/method; establish whether missing components prevent useful decisions.

### T18 · Ticket/booking scope

Fixture/context: Separate vs through-ticket, baggage/cabin/inventory/terms unknown

Customer outcome prompt: Explain what would still need confirmation before purchasing or using this candidate.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Ask what an itinerary proves; attempt handoff or confirmation with existing source.

Truth and observation: No purchasability or protection inference; evaluate whether contextual links help without claiming inventory reconciliation.

### T19 · Enthusiast learning and return interest

Fixture/context: Recent real enthusiast activity; self-directed time after tasks

Customer outcome prompt: Investigate a flight question you find interesting and retain something useful from it.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Compare globe, timetable and network discovery; ask what they would use again and observe chosen task.

Truth and observation: Separate stated enjoyment/novelty from task value and future actual return; schedule illustration/track distinction retained.

### T20 · Locale and privacy

Fixture/context: Ambiguous numeric dates, translated names, local save/share and account-free use

Customer outcome prompt: Interpret these places/dates and explain what is retained or disclosed when saving or sharing.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Explain labels/time conventions and what becomes visible when saving/sharing.

Truth and observation: Correct date/place interpretation, explicit share boundaries; privacy preferences gathered without generalizing from biometrics survey.

### T21 · Explore, select and compare flight options

Fixture/context: Owner-directed exploration task; identical dated choices including a shared edge, different local days and an interesting seed flight

Customer outcome prompt: Explore these dated options, choose something worth understanding, compare alternatives and keep a useful insight.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Let people browse and choose their own question, then compare list/time, linked map and globe placements. Select an individual flight and a whole journey; pin alternatives and return to a reference.

Truth and observation: Useful question/answer stated by the participant; exact flight/journey scope understood; comparison preserves IDs and reference; no claim of actual customer success yet.

### T22 · Play with alternatives reversibly

Fixture/context: Date/gap/hub variations, retained prefix and later commitment; invalid/dead-end option

Customer outcome prompt: Try a change to a date, wait or next leg that interests you; explain the consequences and return to the original when desired.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Compare preview/apply with direct reversible edits. Change one constraint, explain each changed leg, cancel/undo, and try a computed recovery.

Truth and observation: Visible applied/draft scope and changed identities; no silent commitment replacement; recovery actually computed under stated bounds; observe surprise and exploration effort.

### T23 · Analyze an interesting flight cohort

Fixture/context: Selected flight with alternative departure/arrival times, marketed duplicates and incomplete carrier/date coverage

Customer outcome prompt: Analyze a meaningful difference among these options and explain which facts or constraints account for it.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Compare aligned lanes, tradeoff chart/matrix, selected/context geography and progressive evidence. Explain what a highlighted filter changes.

Truth and observation: Correct units, local dates, selection and cohort/query scope; no hidden best score, doubled movement or unsupported completeness. Count useful comparisons and interpretation errors separately.

### T24 · Keep exploration fluid across views

Fixture/context: Long-haul/polar option, occluded map item, phone, keyboard, non-drag input and exploration history

Customer outcome prompt: Start an exploration of your own, learn what can be changed and move between useful details without losing your place.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Move between option, leg detail, schedule, map/globe and evidence; use next/previous/event controls and backtrack. Compare progressive analytical detail.

Truth and observation: State and focus remain predictable; view/camera do not mutate query; facts/history remain dated; actual device/access effort and delight are observed separately.

### T25 · Stay lengths and per-gap intent

Fixture/context: Ordered multi-city/return case; local-night versus elapsed stay; later fixed leg and different rules per gap

Customer outcome prompt: Construct a sequence matching these local stays, return date and fixed commitment; explain any conflict.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Express leave/stay constraints, edit one gap and compare sequence/builder inputs; distinguish hard rules from preferences.

Truth and observation: Whole sequence is date-consistent; local nights not silently treated as elapsed24hours; exact locks honored or conflict disclosed; infeasibility distinct from unknown coverage; current engine does not promise all these constraints.

### T26 · Historical operational reliability

Fixture/context: Matched flight/operator/time cohort with actual delay/cancellation evidence and missing observations

Customer outcome prompt: Explain what these schedule and historical operational facts establish about disruption, including what remains unknown.

Method: Unprompted outcome task first using customer_prompt. Log hints and interpretation probes separately; qa_actions are later prescribed functional checks, not evidence of discoverability.

Functional QA actions: Inspect a route reliability question and compare schedule-only facts with operational summary if sourced.

Truth and observation: Future reliability not inferred from a timetable; sample basis/period/missingness and scheduled-vs-actual truth clear; no unvalidated probability or guarantee.

## Session record and decision discipline

Record consent and anonymized session ID, date, recent-task context, participant/device/access criteria, candidate revision/hash, input facts and query, ordering, observed actions, prompting, exact interpretation, error severity, recovery, unmet requirements, preference separately, and affected U/Q/T IDs. Record a quotation only with permission. Keep recordings/identifying facts outside Git under ignored project storage with a stated retention/removal plan.

A critical wrong inference about local day, missing service, actual operation, live position, guaranteed connection or a silently changed leg requires repair and retest before the affected slice advances. Unknown commercial facts must stay unknown. Completion, explanation and access are assessed separately; do not average a critical error away with preference scores. Quantitative prevalence, performance comparisons and A/B effects require a defined population/design/sample, not nine qualitative participants.

Customer evidence is pending. Owner approval is pending for each final layout/interaction choice. Existing owner requirements and historical tests stay valid only within their documented scope.
