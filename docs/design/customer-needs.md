# Customer needs and interaction inventory

Status: hypotheses for validation, not established customer findings.

## Candidate audiences

1. Flexible traveler: discover destinations, compare departure days, understand a connection, retain a shortlist. Needs comprehensible local times and confidence about what is actually known.
2. Enthusiast: inspect airline networks, follow a schedule through space and time, discover hubs, explore historical service. Needs dense browsing and provenance more than journey optimization.
3. Researcher: compare service periods, carrier coverage, observed versus scheduled activity, and export reproducible results. Needs coverage, methodology, stable identifiers, and completeness.

The owner confirmed on October 10 that the product must serve all three. Use task modes—Find a journey, Explore a network, Inspect service—on a shared workspace, without asking users to identify as a persona. Placing every audience’s controls on the initial screen would make the simple experience harder. This mode structure is a proposal, not yet approved.

## Jobs, input, response, and validation

| ID | Customer question | Input interaction | Response that answers it | Starting scope / validation |
|---|---|---|---|---|
| N01 | Where can I go from here? | Airport or city, optional region, departure date/window, direct/stops | Reachable destinations on map AND readable list; date-specific evidence | First stage. Can a newcomer find a destination without knowing its airport code? |
| N02 | How can I reach this place? | Origin, destination, dates; optional arrival deadline | Ranked complete itineraries and aligned elapsed-time lanes | First stage. Can they explain why two journeys differ? |
| N03 | What changes if I leave a day earlier? | Exact date, +/- days, weekend or broad month; explicit departure/arrival meaning | Calendar availability + results grouped by departure day; preserve filters and shortlist | Stage two. Count real dated options rather than display a route as daily service. |
| N04 | Can I arrive before a meeting? | Destination-local arrival date/time and departure window | Feasible arrivals, +day labels, elapsed durations, explanation of exclusions | First stage specification; not fully prototyped. Test overnight and date-line cases. |
| N05 | Which connection is comfortable for me? | Min/max layover, max journey duration, direct/one/two stops, avoid airport changes | Flight and waiting segments; user-defined timing fit; transfer uncertainty | First stage. Timing fit must not be mistaken for a guaranteed connection. |
| N06 | Can I route through or avoid a place? | Required/avoided hub, ordered stopover, carrier preferences | Constrained alternatives, explainable differences and no-match recovery | Avoid filter in exploration; required via and ordered stopovers in stage three. |
| N07 | Can I construct my own journey? | Select a dated leg, then choose a next departure after its arrival; undo | Sequential builder, candidate next legs, waiting-time feedback, invalid-step explanation | Stage three. Keep selected leg when constraints change; never silently replace it. |
| N08 | What is better for my situation? | Explicit sort: least elapsed time / fewest stops / earliest arrival; pin 2–3 options | Shared-scale comparison, local dates/times, waits, carriers; transparent sort | First stage. No invented weighted “best” score. |
| N09 | What if I use another nearby airport? | City group versus exact airport; consent to airport changes | Alternatives with airport names, locations, and explicit ground-transfer assumptions | Stage two. Ground transfer is a separate uncertain segment, not a flight connection. |
| N10 | Where can I depart to reach a fixed destination? | Destination + arrival window, origin open or region-limited | Inbound reachable origins, departure dates, complete journey timing | Stage two. Reverse discovery requires adequate inbound coverage. |
| N11 | How do I return or visit several places? | Return date or ordered cities and stay lengths; one constraint per leg | Outbound/return or multi-city sequences, date consistency, separate shortlists | Stage three. Do not imply that separate legs form one purchasable ticket. |
| N12 | Does a route exist throughout this period? | Past/future range, airline, status and service days | Calendar/service evidence, dated instances, explicit coverage gaps | Stage two; research stage four for period comparisons. |
| N13 | Was this actually flown? | Historical flight/date or airport pair; status filters | Scheduled versus actual timestamps and operational evidence; source/fetched-at | Stage two. A scheduled record is not proof of operation. |
| N14 | What was happening at this time? | Time cursor or event stepping; UTC/common local reference | Globe/map dots tied to timetable phases: waiting, flying, arrived | Globe experiment. Interpolated positions are schedule illustrations, not live tracks. |
| N15 | Can I trust an empty result? | No extra burden on the user | Distinguish no covered matches, restrictive filters, missing/partial data, errors, truncation | Required in first real frontend. “No results” alone is inadequate. |
| N16 | Can I save or share this exploration? | Save local shortlist, copy a view link; optional export | Restorable query, selected dated IDs, snapshot/freshness, readable export | Local storage first. No account required until cross-device value is confirmed. |
| N17 | Can I use it without a globe or a mouse? | Keyboard, screen reader, small touch screen, reduced motion | Full list/timeline equivalent; native controls; named selection, no hover-only facts | Required from the first stage; do not call it compliant before audit. |
| N18 | How did a network change? | Two time periods, same airport/carrier constraints | Matched route additions/removals, frequency and coverage deltas | Stage four. Unknown data cannot count as a removed route. |
| N19 | Can I afford and actually book this journey, with my baggage/access requirements? | Budget, inventory, cabin, baggage, accessibility and ticket/transfer requirements | Verified commercial facts or an explicit unavailable/unknown boundary; never a fabricated fare or purchasable itinerary | Unmet in the agreed route-exploration scope. Test whether travelers value the exploration without these facts; revisit scope if this dominates their real task. |
| N20 | Which carrier or aircraft operates this pattern? | Operating carrier versus marketing code, aircraft type, dated period | Dated evidence and cohort filters; codeshares do not inflate physical movements | Advanced enthusiast/research need. Requires provider support and normalization before promising aircraft or fleet views. |
| N21 | Can I change a middle flight without starting again? | Select a connection and exact next flight | Retained earlier identities, rebuilt later flights, visible changes and Undo | R06 synthetic preview and Python planner; downstream locks/history remain exploratory. |
| N22 | Can I connect soon, stay overnight or leave after a particular time? | Per-connection short/no-cap/not-before window in local time | Dated onward choices and actual elapsed waits | R06 bounded horizon; no-cap does not mean unlimited loaded data. |
| N23 | Can I leave from another airport in the same city? | Explicit airport-change permission and departure-airport choice | Exact airports, directed allowance, ground-change link and timing assessment | R06 LHR/LGW assumption; actual transfer travel/processing and commercial feasibility remain unknown. |
| N24 | Can I orient the map to one leg while editing? | Show leg; flat/sphere switch and equivalent list | Camera and scheduled departure correspond to that exact flight instance | R06 local action; actual-device/discoverability evidence pending. |
| N25 | Can I understand the controls quickly without promotional wording? | Standard task labels and concise geeky hierarchy | Route map, Connections, Flight schedule; readable airport/flight identities | Owner direction implemented in R06; interpretation/density need customer observation. |
| N26 | What happens if a selected flight or transfer rule changes? | Stale-snapshot comparison and explicit reevaluation | Retain old evidence; identify cancellation/reschedule; do not silently replace | Python rejects missing/cancelled choices; connected preview/stale-data UI still pending. |

## The input grammar

Start with a task, not a toolbox: “Find a journey,” “Explore a network,” and “Inspect service.” Origin may be a city or exact airport; destination may be open. Airports must resolve to canonical IDs, full names, city, and local time zone. Do not make IATA codes mandatory knowledge.

Ask for an exact departure date first, with a discoverable flexible-date option. In the first real version, use plain dates and local clock fields; do not ask users to enter UTC ISO strings. Clearly label whether an input constrains initial departure or final arrival. Display the relevant time zone next to each deadline.

Expose direct/maximum stops and sort next. Layover durations, avoid/via airport, carriers, and overnight preference belong in a single expanded constraints area. Every applied constraint appears in a removable summary. Changing a filter preserves origin/destination, map view, and comparison pins, but flags pinned journeys that no longer match.

Different time semantics must not be conflated: departure-window search, arrival-deadline search, historical range, and a time cursor highlighting an already loaded schedule are four different operations. Moving the cursor does not quietly change search results.

## States and recovery

- Initial: sensible example, coverage disclosure, no fabricated personalized origin. Offer use-location only after an explicit action.
- Editing: query draft distinct from applied results; Search submits changes as a unit. Lightweight presentation filters can be immediate and announce their result count.
- Loading: retain last results labeled as previous; cancel superseded searches; do not mix new map edges with old cards.
- Partial coverage: name missing dates/airports and the basis of counts; offer narrowing to covered dates.
- No covered matches: show query summary and relevant recovery action. Offer relax stops or shift date only if evidence supports it.
- Constraint exclusion: explain which restriction eliminated the last candidate; never promise a match without computing it.
- Error: retry without losing input or shortlist; source failures cannot masquerade as no service.
- Selected: card, map legs, and time lane refer to the same dated itinerary ID.
- Stale selection: preserve the old selection visibly, explain its change, and request reevaluation rather than silently substituting a flight.
- Unknown: transfer feasibility, visa/baggage/ticket protection, inventory, and price remain unknown. Do not rank or certify on fields the product does not have.

## Evidence and limits

This inventory is intentionally broader than the available backend. The agreed no-fares/no-booking scope leaves N19 unanswered; that is a product-value question to test, not a reason to remove the customer's need from research. The same principle applies to aircraft evidence and historical coverage. A route explorer cannot claim to complete an entire travel-purchase decision.

Public product patterns are clues, not customer validation. [Google Flights help](https://support.google.com/travel/answer/2475306?hl=en) documents stops/airline filters, sorting, and date exploration. We infer that comparable input patterns may be familiar; fares and booking are outside Flyji's scope. [FlightConnections](https://www.flightconnections.com/) positions route maps and direct/connecting discovery as its principal interface. That demonstrates an existing interaction model, not proof that a globe improves users' decisions.

Run initial qualitative sessions with at least three people per audience before freezing the design. Ask about a recent real planning/research task and constraints, then observe a task rather than ask whether a beautiful globe is liked. Include at least one keyboard-only participant and two primarily mobile users. These initial nine sessions are a practical qualitative starting point, not statistical proof or a universal optimal sample.
