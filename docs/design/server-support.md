# Serving support derived from customer needs

The broader contract remains proposed. R06 implements the airport catalog and read-only suffix planner described below. The backend remains Python/FastAPI with a read-only reader and local SQLite; ingestion remains a separate internal package. No database server, queue, account system, or additional service is required for this design exploration.

R07 additionally implements [shared schedule routing, explicit ranking, scoped diagnostics and replay](../engine/routing.md). `POST /v1/itineraries/search` supports UTC departure/arrival bounds, airport avoidance, directed airport changes and bounded traces. The legacy list endpoint delegates to this engine and refuses incomplete rankings with 409. Local-zone input, provider coverage and a connected customer webapp still require further work.

[R08 implements the connected customer webapp and general IANA/DST local-date boundary](../product/README.md), including query-snapshot airport metadata, stale-zone preconditions, connection avoidance, recovery states and package verification. Historical gap descriptions below remain part of the exploration record; provider coverage, source-backed transfer suitability and customer validation remain unresolved.

## R06 implemented increment

[Connections and globe exploration](connections-round.md#sqlite-and-module-support-implemented) records the additive `airports` and directed `airport_transfers` tables, exact flight references and continuation calculation. `GET /v1/airports` reads city/name/code metadata; `POST /v1/plans/rebuild` calculates options and a rebuilt suffix without saving a plan. The reader loads flights and metadata in one read transaction; the updater owns metadata imports, validation and reset. Same-city membership alone grants no transfer. No changes were made to the legacy itinerary search.

Any-later connections are bounded by an explicit horizon, with row/search truncation flags. Complete means reaches destination, not exhaustive coverage or commercial feasibility. The inline browser experiment uses fixed synthetic data and remains separate from the live API. R07 adds content-addressed input snapshot IDs and offline replay; externally retrievable historical snapshots, provider coverage, stale UI, protected tickets and source-backed airport MCT/ground travel are still gaps.

## Start with a shared query and selection model

The frontend owns camera/projection, view choice, time cursor, display clock, and expanded details. The server owns airport resolution, time-zone interpretation, dated flight truth, route derivation, constraint evaluation, ranking, and honest coverage/completeness. Selection refers to stable flight-instance and itinerary IDs; no UI should reconstruct commercial legitimacy from airport coordinates.

A canonical query names its intent (`journey`, `network`, `service`) and distinguishes initial-departure window from final-arrival deadline. Dates and clocks are supplied with an IANA zone; UTC is internal and retained in responses. Resolve a city group into exact airports before querying. Nearby-airport and transfer permissions are explicit. Future complex search can use a typed POST body; simple read queries can remain GET. Decide endpoint naming once the first interaction contract is accepted.

Current routes use `/v1/*`. The previously discussed hosted `/api/v1/*` prefix is a future serving decision, not a route this prototype exposes. Do not silently claim it is implemented or break the existing demo to match a mockup.

## Need-to-contract mapping

| Needs | Serving capability | First incremental change |
|---|---|---|
| N01/N02/N09 | Airport catalog: ID, IATA, full name, city group, coordinates, IANA zone | Add read-only airport lookup/autocomplete. Reference dataset managed by ingestion. |
| N03/N04 | Exact local departure day, flexible range, final arrival deadline | Normalize input clocks; define DST ambiguity errors; return actual date-specific options. |
| N05/N06 | Max stops, min/max waits, max elapsed duration, allow/avoid hubs/carriers | Typed constraints; ranked results include the constraints evaluated and remaining unknowns. |
| N08 | Stable itinerary IDs and deterministic sort by duration/stops/arrival | Include complete legs and elapsed waits; advertise ranking basis and exhaustive/truncated status. |
| N12/N13 | Service-day summaries and scheduled/observed basis | Derive counts from individual movements, separate cancelled and unknown statuses, retain actual times. |
| N14 | Time-highlight of already returned flights | No endpoint needed for every cursor tick; use UTC timestamps plus airport coordinates. No “live position” claim. |
| N15 | Query-scoped coverage, freshness, snapshot, errors, limits | Persist ingestion coverage separately from flight rows; return an envelope instead of bare arrays. |
| N16 | Canonical shareable query and export | Deterministic query serialization + stable IDs; local shortlist can be frontend storage first. |
| N18 | Paired period comparison | Normalize both periods and publish coverage for each before computing additions/removals. |
| N19 | Commercial suitability beyond route timing | No fare/inventory/booking capability in current scope; mark those facts unknown. Revisit the product scope if customer research makes them essential. |
| N20 | Operating/marketing identity and optional aircraft cohort | Preserve source-supported operating carrier, codeshare and aircraft fields; distinguish unknown from missing service. |

## Minimal response envelope

```json
{
  "query": {"origin_airports": ["YYZ"], "departure_zone": "America/Toronto"},
  "snapshot_id": "opaque-read-snapshot",
  "coverage": {
    "state": "partial",
    "basis": "scheduled",
    "missing_windows": [{"airport": "YUL", "start": "...", "end": "..."}],
    "last_successful_refresh": "..."
  },
  "completeness": {"exhaustive": false, "reason": "search_limit", "next_cursor": null},
  "items": [],
  "exclusions": {"basis": "evaluated_candidates", "minimum_layover": 2}
}
```

This example is schematic, not valid production data. Counts must state whether they describe all eligible data or only evaluated candidates. An empty item list with partial coverage is “no matching flights in loaded data,” not proof of no commercial service.

An itinerary item should carry `itinerary_id`, UTC departure/arrival, elapsed duration, complete operating legs, optional marketing codeshares, waiting intervals, and `connection_assessment`. That assessment can report `schedule_gap_minutes` and `matches_user_constraints`; airport MCT, baggage transfer, protection, immigration and inventory remain unknown unless independently sourced. Include source and fetched-at per flight. Differing marketing codeshares must not multiply one physical movement into several apparent services.

The route-list/map summary and timetable must be derived from the same query/snapshot. A small prototype can return all three projections from one read transaction; alternatively, pass snapshot IDs and reject stale combinations. Avoid independent requests producing a map from one snapshot and cards from another.

## Map interaction requirements

The map revision adds concrete selection semantics. A visible airport pair can belong to several dated flight instances and complete itineraries. Return stable airport IDs, flight-instance IDs, and itinerary IDs with explicit membership. The client can build an edge index from returned legs for a small result set; a dense summary should supply those relationships from the same query snapshot. Selecting YYZ–LHR must distinguish the direct London journey from the Toronto–London–Paris journey rather than silently choosing one. Coordinates alone cannot identify the dated service.

An airport's “Use as destination” and “Avoid this connection” actions edit the canonical query and invoke the read-only query path. They do not write to SQLite. Applied constraints must return in the query envelope so the client can show and remove them even when its search editor is closed. A count shown on an airport must name its basis: matching dated records, journeys, or distinct destinations are different quantities.

Camera fitting, zoom, pan, projection switching, mobile panel choice, and schedule scrubbing remain local presentation state. No server call is needed for each camera movement or cursor tick. UTC timestamps, airport zones and sourced coordinates are enough to illustrate schedule progress. A measured aircraft position would require a separate, explicitly identified data source; this exploration has none.

The R04 [refreshing visual directions](refresh-direction.md) require a selected and a compared dated journey to keep their identities/snapshot when later excluded by the query. Resolve retained records explicitly rather than substituting a first match. A shared spatial edge can carry both identities and multiple eligible dated choices; query counts and physical edge counts remain distinct. The existing small read transaction can supply this without a new service.

Departure-date summaries must use the same active nonstop, avoided-hub and destination-local deadline constraints as the dated option query. R04's next-day Paris deadlines deliberately cover only fixed October fixture offsets; the real reader must implement the IANA-zone validation and DST semantics already described above. Neither Earth illumination styling nor a richer visual language adds a server dependency. Cursor ticks update already returned schedules locally.

R05's [refinement of all three alternatives](refinement-round.md) adds exact role swapping, paired local-day summaries and explicit camera recovery. Swapping a selected and compared journey must preserve their dated IDs and snapshot; it does not revise the query or re-fetch a new first match. Positioning the cursor at a selected departure and facing an off-view scheduled point use returned timestamps/coordinates locally. No new endpoint or service follows from these controls.

## Current implementation audit

The breadth experiments additionally require date-specific count envelopes, destination-zone deadline normalization, stable next-leg queries and reproducible paired-cohort comparisons. Their browser fixture logic has not been moved into the reader or API.

- A date summary must apply the same constraints as its option query and retain complete/partial/unknown state per date. A minimum duration is the shortest loaded matching option unless exhaustive ranking is established. It is not a fare or availability signal.
- The arrival query supplies destination-local date/time plus an IANA zone; the HTTP boundary resolves or rejects invalid/ambiguous local input. Return the normalized instant and evaluated constraints. The new fixture limits deadline input to 18–24 October; it does not validate general DST behavior.
- A next-leg query supplies the exact selected flight instance, its snapshot, user gap bounds and destination/avoid constraints. A reader response returns dated candidates and exclusion reasons. Revising a selected flight invalidates the sequence visibly; never replace it with the first candidate.
- Period comparison uses the same canonical query, metric definition and evidence basis for both cohorts. Count complete journeys separately from physical movements. Missing coverage prevents a removal/frequency claim; compare matching weekdays/ranges for broader pattern claims.
- The client owns retained excluded selection and explicit repair/undo. The reader supplies stable identities and changed-evidence status; no updater write is needed to explore a route.

| Existing capability | Gap against the proposed UX |
|---|---|
| `/v1/flights`: UTC timestamps, origin/destination, cancelled flag, limit | No airport names/zones, local-day parsing, airline/status filters beyond cancellation, pagination or coverage envelope. |
| `/v1/routes`: counts and carrier list from non-cancelled records | No service-day calendar; unknown/active/scheduled grouped together; incomplete storage indistinguishable from no service. |
| Legacy `/v1/itineraries` and rich POST search share R07 engine | Explicit UTC arrival horizon, POST avoidance/rankings/stable IDs and explained limits implemented. Destination-local wall clocks, required via airports and commercial transfer verification remain gaps. |
| SQLite WAL, separate reader/updater | Airport/transfer metadata is persisted; new planner reads one transaction snapshot. Coverage records and externally addressable snapshot IDs remain missing. |
| Provider provenance + fetched-at | Individual record freshness does not describe successful coverage of missing data. Need ingestion-job/coverage records. |
| R07 loads reachable-origin rows and examines candidates under explicit budgets | Removes the old global 5,000-row/result-count traversal cutoffs; POST labels partial calculations, legacy list refuses unproven ranking. Provider-wide coverage is still not established. |
| R07 uses an inclusive arrival horizon independent of connection minutes | Fixes the old minutes-as-hours fetch coupling. Long-flight, initial departure, connection and final arrival boundaries have regression/oracle checks. |
| R07 HTTP queries reject naive/mixed-offset-invalid timestamps with 422 | General destination-local IANA/DST input remains a future boundary contract. |
| Service route construction tests are synthetic | Provider normalization, schedule revisions, codeshare identities, coverage and real-connection behavior need separate validation. |

R06/R07 implement the metadata and routing increments above. Remaining local-date/coverage/provider/commercial-assessment gaps continue to inform the customer-led backlog; implementation of the engine does not choose the frontend experience.

## Implementation order after consensus

1. Agree audience task modes, first-stage queries, and trust wording with the owner; run the first qualitative task round.
2. Extend the implemented airport metadata/planner with general local-time boundary validation and coverage tracking. Define the response envelope and stable IDs.
3. Extend the corrected engine's UTC horizon/caps/arrival bounds with general local-date interpretation, coverage evidence and provider revisions. Preserve reader/updater separation and oracle/replay checks.
4. Build one real linked query/cards/timeline flow for each audience, served by the existing FastAPI process. Static HTML/CSS/JS is sufficient; no Next.js server needed.
5. Add calendar/2D map once shared selection and responsive/a11y behavior are reliable. Globe remains an optional tested projection.
6. Introduce accounts, workers, caching services, or PostgreSQL only if a measured need warrants them. Frontend UX is not a justification to add unrelated infrastructure.
