# Flyji design exploration program

Owner instruction, October 10, 2026: pursue a deep, iterative, comprehensive exploration. Travelers, enthusiasts and researchers all remain in scope. No final interaction model has been approved.

## What comprehensive means here

The design space is open-ended. We cannot substantiate “all possible designs” by generating many screens. We can systematically cover distinct customer tasks, interaction families, representations and failure conditions, retain what remains unexplored, and revisit decisions when evidence changes. A recolored or rearranged screen is not a new interaction family.

The first rounds covered a journey board, linked Atlas and optional Earth, mostly with the same query grammar and a small Atlantic fixture. Their browser checks establish bounded behavior, not customer usefulness. The breadth round tested date-first, arrival-first, construction-first and evidence-first interaction slices. R04/R05 refine three visual directions on one shared dated fixture. Their differences remain visible while customer validation and the outstanding families stay open.

On October 13, the owner chose to refine Airlight, Journey canvas and Orbit studio before choosing a direction. The [latest refinement](refinement-round.md) retains passing revisions, explicit visual critique and phone layout tradeoffs. This accepts the refinement strategy, not a final default or an audience ranking.

The [customer inventory](customer-needs.md) remains a set of hypotheses. The [design-space register](design-space.md) describes twelve families and their open questions. The [benchmark scenarios](benchmark-scenarios.md) give repeatable tasks and truth conditions. The [iteration ledger](iteration-ledger.md) records changes and evidence. Customer research is still outstanding.

R06 expands the owner's connection/globe seed into editable prefixes/suffixes, directional city-airport transfers, overnight/not-before choices, local clock validation and leg-linked map orientation. It adds six benchmark scenarios (42 total) and a tested Python/SQLite planner. Its plain geeky copy follows explicit owner direction; three working layouts remain open for comparison.

## Explore along independent dimensions

| Dimension | Alternatives to cover | Question that can change a decision |
|---|---|---|
| Starting knowledge | Exact cities; open destination; open origin; known flight/carrier; open dates | What does the person know before arriving? |
| Input grammar | Compact form; guided questions; editable sentence; direct map selection; next-leg builder; expert query | Which helps them express the constraint without hidden interpretation? |
| Search direction | Forward reachability; backward arrival deadline; two-way; ordered multi-city | Is arrival, departure or a sequence the actual fixed point? |
| Temporal scope | One local day; flexible days; repeating timetable; historical range; paired periods | Does a line represent a dated flight, repeated service or aggregate evidence? |
| Spatial representation | Names/list; 2D geography; Earth; topology diagram; airport matrix; small multiples | Which relationships must remain visible together? |
| Temporal representation | Local endpoint clocks; shared lanes; date grid; departure/arrival tradeoff; timetable; event stepping; 3D time | Does the visual answer a decision or merely look dynamic? |
| Selection model | Whole itinerary; individual leg; airport; cohort; multiple pinned options | What is selected and what must remain stable after edits? |
| Comparison model | Explicit sort; shared-scale shortlist; side-by-side detail; cohort difference; Pareto choices | What tradeoff can be explained without an invented “best” score? |
| Information architecture | One adaptive workspace; separate task pages; overview/detail; guided sequence | Can every audience find the right task without declaring a persona? |
| Trust and recovery | Complete/partial/unknown; exclusion explanation; stale evidence; retry/undo | Can the person explain what the result proves and does not prove? |
| Device and input | Wide desktop; narrow touch; keyboard; screen reader; reduced motion; low-power | Is the same task possible when the primary visualization is unavailable? |
| Visual language | Editorial quiet; compact analytical; spatial immersive; guided calm | Does visual emphasis improve a task without reducing clarity or accessibility? |

We will sample combinations deliberately, not claim the full cross-product has been tested. Start with distinct representatives, then cover high-risk pairs: map + keyboard, flexible dates + unknown coverage, arrival deadline + local time, builder + stale leg, dense network + small screen, historical comparison + partial evidence. Every new feature adds an explicit coverage row and an unresolved-risk row.

## Repeated loop

1. **Frame a customer task.** Describe recent context, starting knowledge, constraints, and the answer/action needed. Attach N/B IDs. Do not derive the task from an endpoint or aesthetic preference.
2. **Diverge.** Produce at least three materially different approaches to that task. Write the strongest defense of each and the condition under which it would fail. A visual family may serve several tasks, but that must be demonstrated.
3. **Make the critical interaction executable.** Use shared fixtures and truth conditions. Show local dates, actual elapsed waits, coverage and selected identities. Mock only what is named as mocked; no inert buttons that imply support.
4. **Inspect and challenge.** Walk through novice, expert, mobile and keyboard paths. Check interpretation risks, recovery, density, clocks, state continuity and visual hierarchy. Record author critique separately from independent findings.
5. **Verify behavior and appearance.** Run meaningful interaction checks; inspect captures at the same widths and appearances; fix failures and retain their cause/retest. A high pass count cannot substitute for task evidence.
6. **Observe people.** Use realistic tasks and new participants across rounds. Counterbalance alternatives and avoid rehearsing the answer. Record misinterpretations and prompting. Do not let visual preference override task errors.
7. **Revise or recombine.** Keep useful mechanisms, remove costly ones, and test the combined flow again. Improvements in an isolated screen do not guarantee a good combined product.
8. **Decide with the owner.** Present task evidence, defense, cost, uncertainty, dissent and reversible scope. Record accept/revise/reject/defer per decision. Approval applies to the stated decision and version, not the entire backlog.
9. **Derive server support.** Only after interaction semantics are clear, define airport/date/identity/coverage contracts and reader changes. Camera, cursor and presentation state stay local unless customer value requires otherwise.

This combines diverse alternatives with revision and comparative task evaluation, following [NN/g's discussion of iterative, parallel and competitive design](https://www.nngroup.com/articles/parallel-and-iterative-design/). “Parallel” here means alternative concepts; no additional agents or external reviewers were engaged. The method is guidance, not evidence that Flyji is usable.

## Campaigns and concrete deliverables

| Campaign | Design question | Deliverable | Exit condition |
|---|---|---|---|
| Foundations | What do all three audiences actually need? | Task inventory, recent-task interviews, benchmark fixtures | Repeated tasks/constraints understood; unmet commercial needs remain explicit |
| Breadth | Which interaction families explain different tasks? | Twelve-family register; distinct working slices; rejection conditions | Every family is represented or explicitly deferred with a reason |
| Time and construction | How do dates, arrival deadlines and next legs work? | Date, arrival and builder experiments with temporal truth | No silent clock/leg replacement; unknown data distinct from no matches |
| Density and scale | How does each design behave with 1, 10, 100, 1,000 options? | Synthetic stress fixtures; query/list/map strategies | Counts, limits and selection remain honest; measured performance within an agreed budget |
| Resilience | What happens during failure or data changes? | Partial, error, superseded, revised and stale-selection states | Recoverable without losing intent; no false certainty |
| Accessibility and mobile | Can people complete the same task with different inputs? | Real-device and independent accessibility review; repairs | Critical tasks work in the agreed device/browser/input matrix |
| Visual direction | Which visual treatment helps hierarchy and orientation? | [R04: three treatments](refresh-direction.md) applied to the same working dated-query flow; approval remains pending | Comparable readability, contrast, density and task evidence |
| Synthesis | Which mechanisms should coexist? | A small number of combined candidates; comparative task sessions | Owner accepts documented decisions and unresolved scope |
| Real-data slice | Does the design survive actual provider behavior? | Customer-driven reader contracts, one live flow, fresh audit | Provenance, coverage, identities, clocks and limits remain correct |

Campaigns can loop backward. The owner requested depth, not a predetermined deadline. No invented session results, research-derived scores, “top-tier” badge or final approval will be used to hurry convergence.

## Decision rules

Reject or repair any design that loses a selected dated leg silently, calls unknown data “no service,” confuses clock labels with elapsed time, implies a protected/bookable connection from a generic gap, or makes a critical task depend on mouse/globe manipulation. Keep these as semantic gates, not points that can be offset by beauty.

Among candidates passing those gates, compare task completion, interpretation, recovery effort, prompting, retained context, discoverability and measured performance. Record observed values only after an actual session or test. Use qualitative tradeoffs while evidence is incomplete; do not invent a numerical ranking.

We may pause a family when two successive substantive revisions fail its task while another performs better, or when its necessary data cannot support a truthful answer. This is a review trigger, not an automatic rejection or statistical rule. Reopen it if customer evidence changes.

## Scope and infrastructure

Continue locally with Python, SQLite WAL, separate ingestion/reader/admin modules and FastAPI. The explorations use static browser interactions and synthetic data. No additional deployment service, AI query service, account system, terrain engine, fares or booking is authorized by visual exploration alone. Customer needs outside those constraints remain visible and can prompt a later product discussion.
