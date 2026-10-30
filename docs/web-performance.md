# Route explorer UX and performance

The flight explorer keeps all fonts and map geometry on the same origin.
The main font is preloaded from the same content-versioned release as the CSS.

## Results-first workflow

The primary search is airports, departure date and Search. The editable 7-day
connection limit stays visible. Via airports, stop limits, avoided airports and
other route preferences live in More options; data basis and input clocks have a
separate disclosure there. Sorting sits with the results and updates them directly.
It reorders the applied search, retaining a selected or edited journey, undo
history, Earth camera and time cursor while the dataset revision is unchanged.
Unsaved form edits remain drafts; a visible notice explains that the displayed
journeys still use the previous search. Sorting pins the dataset revision so it
cannot silently combine different snapshots.

Journey cards put departure, arrival, elapsed time and stops in one row. Their bars
share one minute-to-pixel scale across the loaded results, separating flight time
from connection time. Badges describe the shortest/fewest-stop journeys shown,
without claiming that a partial page proves a global optimum.

The selected route canvas fits airports and sampled great-circle arcs, including
airport transfers. Pacific routes center near the date line instead of spanning a
whole-world projection. The visible Route / Earth switch preserves the interactive
global sphere. Projection and camera controls, timeline scrubbing and JSON exports
remain available behind disclosures. On mobile, choosing a journey focuses its
heading rather than an export action.
The map stays available before a search and when no routes match, including the
mobile Map tab. The chosen projection is remembered locally. Empty results offer
date and preference actions without silently relaxing constraints.

Selected flights pair each airport with its own local clock, date and UTC offset.
Layovers are explicit pauses with duration, minimum and an edit action; airport
changes use a distinct transfer label. Routine aircraft and status metadata lives
in Flight details, while warnings and observed-time uncertainty stay visible.
Observed comparison cards also mark approximate clocks and elapsed ranges;
connection labels describe guaranteed minimum waits instead of exact waits.
Their accessible chart labels distinguish flight-duration upper bounds from
connection-duration lower bounds.

Replacement searches cancel connection work and temporarily disable editing and
undo until the new query succeeds or fails. This prevents an old connection plan
from being applied to a different search. Date availability alone does not enable
the Journey tab. Invalid custom preferences close the popover so their error is
visible. On phones, airport suggestions occupy space within their field instead
of covering the next airport input.

## Date formatting

Journey comparisons reuse up to 48 Intl.DateTimeFormat instances, keyed by locale
and formatting options (including timezone). The least recently used entry is
evicted when the bound is reached. DST offsets and observed intervals retain the
same output; invalid timezone and instant errors remain unchanged.
Clocks, dates and offsets are read with `formatToParts`, so Safari's date/time
separators cannot break card rendering. Display parts share the cached formatter
with complete timestamps.

A local five-round benchmark formatting 2,000 labels across six zones measured
median time of 60.110 ms before caching and 2.530 ms with it. This is a formatter
microbenchmark, not an end-to-end rendering or search latency measurement.

## Globe rendering

Rotation, zoom, resize, journey changes and timeline scrubbing share one pending
animation frame. Multiple updates before that frame render only their final state.
The existing static-scene key still prevents rebuilding geography during timeline
scrubbing. All countries are rendered in one SVG path instead of one DOM element
per country; country boundaries remain visible.

The journey timeline retains its SVG, flight bars and UTC tick axis while the
cursor moves. A regression applying 200 cursor updates observes one static scene
build instead of 201. Width or timestamp changes rebuild it; clearing or hiding
the container cannot leave a stale scene. Antipodal airports and zero-duration
flights retain finite Earth rotations and cursor coordinates.

The frontend regression applies 100 rotation updates and 100 cursor updates
before a frame, then verifies a single draw of the latest state and a working
subsequent frame. Browser checks cover flat/globe switching and desktop/mobile
layout. The compact airport fields and independently sized globe keep the route
visible without stretching it to the search form's full height.
Normal wheel gestures scroll the page across the globe. Hold Ctrl or Command
while scrolling to zoom, or use the map buttons; dragging still rotates it.

## Immutable asset serving

Previously, every versioned asset request walked and stat-ed the entire web
directory before returning cached bytes. Known releases now use the memory cache
directly. Page requests still discover edits and retain the current and previous
releases. A worker receiving an unfamiliar version scans once to discover it;
missing or evicted versions still return 404. Conditional GETs and HEAD requests
retain their existing behavior.

A local FastAPI TestClient comparison over 200 warm stylesheet requests measured
median request time of 0.576 ms with the former scan and 0.182 ms without it.
Directory scans fell from 200 to zero; p95 was 0.643 ms versus 0.203 ms. These are
local asset-serving measurements, not production latency or flight-search timing.
Regression tests also verify cache hits without filesystem scans, cold-worker
discovery, immutable bytes, conditional responses and bounded release history.

## Backend response construction

Local searches now retain already-validated route models instead of serializing
and reconstructing every leg before building the public response. Public flight
conversion reuses shared record objects only within one response. Distinct
objects retain their own facts even when their identifiers match, and subsequent
responses always use fresh data.

A paired 61-round local benchmark with 100 itineraries, 200 leg occurrences and
101 distinct hydrated legs measured combined response construction at 5.454 ms
before and 2.380 ms after (median); p95 was 5.957 ms versus 2.749 ms. Public JSON
was identical. This includes SQLite identity resolution but excludes route
calculation and HTTP transport. It is not a whole-search latency claim.
