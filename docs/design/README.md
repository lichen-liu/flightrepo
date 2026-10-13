# Flyji frontend exploration

Status: proposed; owner chose to refine all three alternatives before choosing. Updated: October 13, 2026.

The aim is to help people understand where and when they can travel on real commercial flights. The implementation remains a local Python prototype. Customer needs determine the serving contract; existing endpoints do not define the product scope.

This is an exploration, not an approved final UI or a validated customer study. No customer interviews have been conducted. The owner confirmed that all three audiences matter: flexible travelers, aviation enthusiasts, and route researchers. No one audience is assumed to dominate. Proposed task modes share the same query and evidence model.

## Review package

- [Connections and globe exploration](connections-round.md): plain task headings, three geeky layouts, editable next flights, overnight windows, exact same-city airport changes, Show leg on a sphere, SQLite metadata and a read-only Python planner; final 190-assertion run, eleven captures and 31 Python tests. Earlier revisions and the 136-assertion run's capture-state limitation are retained.
- [Refine all three alternatives](refinement-round.md): paired local-day identities, exact-journey swapping, selected-departure positioning, compact phone dates and explicit camera recovery in `flyji-refined.html`; retained 208/233/238-check passes and measured layout tradeoffs.
- [Refreshing UI and UX philosophy](refresh-direction.md): Airlight, Journey canvas and Orbit studio; shared dated selection/comparison, richer geography and schedule graphics, retained 121/153/165-check revisions and visual critique in `flyji-refresh.html`.
- [Deep exploration program](exploration-program.md): the repeated research/design/critique/QA/consensus loop and its evidence gates.
- [Twelve-family design-space register](design-space.md), [42 benchmark scenarios](benchmark-scenarios.md), and [iteration ledger](iteration-ledger.md).
- [Breadth-round experiments and audit](breadth-round.md): date-first, arrival-first, construction-first and evidence-first working slices in `flyji-design-laboratory.html`.
- [Map-first revision: interactions, defense, and audit](map-workspace.md)
- `flyji-map-workspace.html`: map interaction exploration, with linked Atlas/Earth views, airport actions, route picking, camera controls, and a schedule cursor. Earlier rounds remain preserved for comparison.
- [Customer questions and interaction inventory](customer-needs.md)
- [Design alternatives, staged scope, and decision register](design-decisions.md)
- [Customer-driven serving contract and current gaps](server-support.md)
- [QA evidence and research protocol](qa-audit.md)
- `flyji-exploration.html`: offline interactive source with three interface alternatives and synthetic schedules. This is an exploration, not the production frontend.
- `airport-reference.json`: sourced airport coordinates used by the exploration.

Try three tasks in every design: find a direct flight; compare a shorter journey with a longer layover; move the time cursor and explain whether the selected journey is flying, waiting, or arrived. Then change the date and try a date outside demo coverage. Flights, airlines, and timings are fictional. Geographic geometry and airport coordinates are sourced.

For the map revision, select an airport, try using it as a destination or excluding a connecting hub, then remove that constraint. Switch Map/Earth without losing the selected journey. In Discover routes, select a shared leg and choose the complete dated journey. On mobile, compare the Map, Options, Time, and Details views.

For the refreshed directions, compare the two nonstops, then compare Montréal's 75-minute wait with London's 5h30 wait. Change the next-day Paris arrival deadline or avoid Montréal; confirm that excluded selections stay visible and Undo restores the constraint. Select a shared geographic line and choose its exact dated journey. On phones, use Map, Options and Time; the selected local-day summary stays visible.

For R05, inspect both local-day summaries and Swap journeys without changing the query. In Time, use Selected departure and Next selected event; stepping stops at final arrival, with Selected departure as the explicit restart. On Earth, rotate east and south until a schedule position is behind the hemisphere, then Face that specific identity. On a phone, compare all three with the paired summaries and two-row departure calendar.

For R06, open Connections on a phone or use the editor beside the map. Allow airport changes, select Any later flight, then choose LGW and its next-day Amsterdam departure. Confirm that the final Paris leg changes and Undo restores it. Show leg opens that leg on the globe. Compare the same behavior in Globe first, Map first and Connections first.

No customer ranking or final layout has been approved. The owner explicitly requested plain/geeky copy and editable connections. R06 implements airport lookup and read-only plan calculation in the existing Python/SQLite modules; its local visual fixture is not connected to a provider or the API.
