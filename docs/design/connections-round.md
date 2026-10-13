# Connections and globe exploration

R06 · 13 October 2026. Owner direction: plain task labels, a concise geeky style, editable onward flights, same-city airport changes and a useful sphere view. The three layouts remain alternatives; no final layout is selected. R01–R05 sources and evidence are preserved.

This record describes the R06 backend/audit state. The subsequent [R07 routing iteration](../engine/routing.md) supersedes its row/search limits and legacy-algorithm gaps, with shared policies, diagnostics/replay and 109 tests. The R06 visual source, fixtures and browser evidence are unchanged.

## Working behavior

The current [prototype](flyji-connections.html) contains **Globe first**, **Map first** and **Connections first**. These continue the spatial, broad-map and construction/time emphases without the earlier promotional headings. “One clock. Two choices.” becomes “Flight schedule”; the other primary headings are “Route map,” “Flight options” and “Connections.” Monospaced airport/flight identifiers, compact controls and a shared restrained palette support the requested style. These labels describe tasks rather than an attitude toward travel.

The preview opens at a synthetic Toronto–London–Amsterdam–Paris journey. Select a connection, then change its next flight. Earlier flights retain exact dated identities; every later flight is rebuilt. Changed flights are marked Updated. Undo restores the previous route and departure-window settings.

Departure windows are **Within 6 hours**, **Any later flight**, or **Depart after** with an airport-local date/time. “Any later” removes the six-hour wait cap; the finite loaded window remains explicit. The current preview contains departures through 23 October and at most three flight legs. It is not an unbounded or exhaustive commercial search. The local clock converter rejects nonexistent or ambiguous local instants rather than choosing an offset silently; the API accepts explicit offset-aware instants.

Airport changes require opt-in and a configured directed rule. LHR and LGW keep separate identities, names, coordinates and flight endpoints despite sharing `GB:LON`. The synthetic LHR → LGW allowance is **180 minutes**, including arrival processing, ground movement and new check-in. It is an assumption, not a published airport MCT or transfer guarantee. Reverse permission is not inferred. The map uses a distinct dashed link for the airport change; that link describes geographic endpoints, not a verified road/rail route. No exact ground-position interpolation is invented.

Flat map and Globe are available in every layout. Show leg frames that leg on the sphere, opens the map on a phone and moves the schedule cursor to its dated departure. Rotation/zoom/recovery remain native controls; fine-pointer dragging is optional. No autoplay or live-aircraft claim is introduced. Published country geometry and sourced coordinates remain embedded.

When a chosen next flight has no complete loaded suffix, it remains selected with its earlier flights. The destination is explicitly not reached. Final-arrival comparison is withheld rather than comparing a partial arrival in London/Tokyo against a complete arrival in Paris. Total connection time sums every gap, including an airport-change window.

The preserved preset list still uses its bounded date/constraint grammar. Its counts now say **preset routes** rather than imply that all flexible constructed routes are counted. Edited routes excluded by preset arrival/date/nonstop/avoid constraints retain their identity and an exclusion label. The connection preview extends the preset fixture with the twelve-record shared planning fixture; these are synthetic schedules, not provider service claims.

## Reference inspection

The [AeroRepo ETOPS section](https://aero.boboji.fyi/#etops) was inspected in the browser on 13 October: an orthographic sphere, geographic routes/rings, fit/center actions and native camera controls. It informed the geographic representation. Its ETOPS/range calculations are not copied into Flyji. The useful extension here is to connect map orientation directly to a dated flight and an editable chronological sequence, with a flat/list/time equivalent.

New Gatwick/Schiphol coordinates were checked against the published [OurAirports data](https://github.com/davidmegginson/ourairports-data/blob/main/airports.csv); existing sourced airport references and the 177-country geometry are preserved. Sphere lighting is visual styling, not computed daylight.

## Choices, alternatives and objections

| Need | Current mechanism | Alternative considered | Defense and unresolved objection |
|---|---|---|---|
| N07/N21: change a middle flight | Keep prefix; rebuild suffix; exact selection and Undo | Keep later manual choices as locks | Rebuilding follows the owner's requested dependency behavior. It can replace a desirable later flight; Updated and Undo make that visible. Multi-step history and optional locks need a later comparison. |
| N05/N22: immediate connection or longer stay | Short window / no wait cap / exact not-before instant | One global min/max wait for the entire journey | A per-connection editor supports different stays. Only the active connection's maximum/not-before constraint applies; rebuilt later gaps use the minimum allowance. Separate per-gap constraints/stopover lengths remain a candidate, not a hidden promise. |
| N09/N23: change airport within a city | Separate airport records + directed, sourced allowance + consent | Merge city airports into one graph vertex | Merging would hide physical transfers and invalidate time/airport identity. City membership alone establishes neither travel time nor usability. |
| N14/N24: inspect a leg spatially and temporally | Show leg updates globe camera and departure cursor | Automatic camera tracking every cursor tick | Explicit action is controllable and avoids fighting the camera while exploring. Phone view changes still need actual-device/discoverability review. |
| N15/N26: missing or revised data | Incomplete prefix retained; Python rejects missing/cancelled choices; truncation flags | Silently replace a stale selected flight | Replacing would erase intent. The preview uses fixed fixtures; a connected client still needs a visible stale-snapshot flow. |
| N17/N25: concise, understandable workspace | Task headings, native controls, separate phone Connections view | Persistent paired summaries before every phone view | Map and editing are reachable sooner, but changing views can hide local arrival consequences. Test accurate date interpretation and view-switch effort separately. |

## SQLite and module support implemented

`core/connections.py` defines metadata, exact `(provider, provider_id)` references, connection windows and typed planning results. `core/planning.py` calculates continuation options and a suffix without I/O. `ingestion/updater.py` owns metadata initialization/import/reset; `server/reader.py` reads a single transaction snapshot of flights, airports and transfer rules. The serving package still does not import the updater. No service or dependency was added.

| Stored fact | SQLite shape | Purpose / limit |
|---|---|---|
| Exact airport | `airports(iata PRIMARY KEY, name, city_id, timezone, latitude, longitude)` | Stable city grouping and IANA clocks; flight airport codes remain unchanged. An index on `(city_id, iata)` supports grouped choices. |
| Airport change | `airport_transfers(arrival_iata, departure_iata, minimum_minutes, source)` with directed composite key and airport foreign keys | Only explicitly configured pairs are permitted. Minimum includes the entire between-flight allowance. Updater validates same-city membership and rolls back changes that invalidate an existing rule; planner rechecks membership. |
| Dated flight | Existing `flights(provider, provider_id, …)` | Exact occurrence references, UTC scheduled timestamps, cancellation/provenance. Added `(origin_iata, departure_scheduled)` index supports onward selection. |
| Draft/selection/camera | Local interaction state; not another table/service | Exploration is a calculation. It does not write a user's plan into flight facts. |

SQLite foreign keys are enabled for every connection, as required by [SQLite's documentation](https://sqlite.org/foreignkeys.html#fk_enable). Tables are additive when the internal client initializes the DB; existing flight rows do not require airport metadata to remain queryable. Reset clears flights, transfer rules and airport metadata transactionally, preserving schema. All DB/WAL files stay under ignored `var/`.

New read-only operations are `GET /v1/airports` (city/name/code lookup) and `POST /v1/plans/rebuild` (calculation only). Rebuild accepts a prefix, destination, horizon, window, airport-change permission and optional exact `chosen_next`. The first automatic choice is the earliest loaded departure that can complete; its suffix favors earliest final arrival, then fewer legs, then exact ID order. This is not an invented weighted score.

The Python request allows up to four legs and a horizon within 31 days of initial departure. The reader examines at most 2,000 onward rows, reporting `data_truncated`; the suffix search has a 100,000-candidate budget and reports `search_truncated`. `complete` means the returned route reaches the requested destination, not that coverage/search is exhaustive. Only directional rules and schedule timing are evaluated: ticket protection, baggage, immigration, inventory, actual surface travel and airport MCT remain unknown.

The legacy `/v1/itineraries` search is unchanged, including the separately recorded temporal-horizon/cap issues. The new planner is independent of that algorithm. The inline preview replays local fixture logic and does not call the running API. Sharing the Python planner through the serving endpoint prepares the future connected webapp without adding deployment machinery now.

### Try the Python demo

The seed uses a separate DB and does not reset `var/flights.db`:

```sh
.venv/bin/python scripts/seed_connection_demo.py
FLIGHT_DATABASE_PATH=var/connections-demo.db .venv/bin/python -m admin rebuild var/connection-request.json
```

Edit `var/connection-request.json`: add `"chosen_next": {"provider":"demo","provider_id":"lgw-ams-overnight"}` to select the overnight transfer route. The final flight updates to `ams-cdg-next-day`. Set `allow_airport_change` false to inspect same-airport choices. `window.maximum_minutes` may be omitted/null; `window.not_before` is an offset-aware timestamp.

For Swagger, start a separate demo instance:

```sh
FLIGHT_DATABASE_PATH=var/connections-demo.db .venv/bin/uvicorn server.api:app --port 8002
```

Open `http://localhost:8002/docs`. Use the request JSON in `/v1/plans/rebuild` or `GET /v1/airports?city_id=GB:LON`. Existing metadata can be upserted through the internal `import-airports` command; its JSON contains `airports` and `transfers` arrays. There is no public mutation endpoint.

## Audit and actual revisions

The final browser run passes **190 assertions** with zero JavaScript errors in primary and fresh touch contexts. This includes **52 host-state restore assertions**; the count is not 190 distinct customer tasks. It covers 320/390/736/1024px layouts, 1100px desktop captures, native keyboard selection and a fresh 390px touch context. Eleven captures cover three desktop layouts, Map/Connections phone views for each, a dark globe and the overnight airport-change route. Each capture records the actual flight identities, departure window, airport-change permission and projection. **31 Python tests pass**, including the existing module/WAL tests and new metadata rollback, city/direction/minimum rules, suffix replacement, overnight/not-before choices, cancelled/missing choices, capped data, read-only API and reset tests. The existing Starlette/httpx deprecation warning does not affect the results.

| Version | Retained evidence | Actual changes / findings |
|---|---|---|
| Preflight | `qa/revisions/connections-preflight.html`, `connections-preflight-check.cjs`, `connections-preflight.log` | The fresh phone context waited for an intentionally hidden editor instead of activating Connections. Fixed the harness wait. Earlier unretained setup runs corrected a local-time test threshold and a synthetic dead end that actually had a later route in the merged fixture; neither was reported as a passing audit. |
| R06a, 112 checks | [Initial results](qa/connections-initial-results.json), `qa/revisions/connections-r06a.html`, `connections-r06a-check.cjs`, ten initial captures | Working editor, transfer choices and globe switch. Visual/code review found palette specificity differences, inherited comparison spacing and first-gap-only summary math. |
| R06b, 130 checks | [Interim results](qa/connections-interim-results.json), `qa/revisions/connections-r06b.html`, `connections-r06b-check.cjs`, ten interim captures | Unified geeky palette; plain layout names; globe opens first for this experiment; final-leg prefix, Undo-window, retained-exclusion and text-contrast checks added. |
| R06c, 136 assertions; capture-state limitation | [Reviewed results](qa/connections-reviewed-results.json), `qa/revisions/connections-r06c.html`, `connections-r06c-check.cjs`, ten reviewed captures | Sum every connection gap, withhold final-arrival comparison for incomplete routes and separate paired summaries in the construction layout. Visual inspection found that a theme change could re-emit the host's older incomplete route: the harness had restored only the rendered state with a synthetic event. These captures are retained with that limitation, not presented as the final default-state audit. |
| R06d, 190 assertions | [Final results](qa/connections-results.json), [script](qa/connections-check.cjs), `qa/revisions/connections-r06d.html`, `connections-r06d-check.cjs`, `qa/connections-screenshots/` | Product source is unchanged from R06c. Harness restores the real host snapshot, verifies the exact route/settings in all three layouts after every restore, checks appearance-change retention and records capture states. Added an overnight airport-change capture. All eleven captures were visually reviewed, using upper/lower crops for long images. |

Each browser record stores source/script/capture hashes. Earlier revisions and the limited R06c run are retained rather than relabeled as final evidence. Text contrast sampling covers primary/supporting text against the surface in both schemes; it is not a full WCAG audit. Controls and date fields fit the tested widths; active touch controls are about 44px high. Full screen-reader, Safari/actual-device, dense-network, performance, live-provider revision and customer task validation remain outstanding. Shared carousel height leaves whitespace below shorter layouts/views; the compact phone view also trades persistent arrival details for earlier map/editor access. Native next-flight values can truncate at phone widths; the dated flight is also displayed in the route above and the native option list exposes its full identity. The world-scale airport-change line is small; a city inset remains a proposed improvement. No user-effort or preference score is inferred from the captures.

The [Python/CLI verification record](qa/connections-python-results.json) attributes the 31-test result to the backend/fixture hashes and records the actual default and overnight routes returned from the ignored demo DB. Normal `var/flights.db` was not reset.

Rebuild and verify from the project root:

```sh
python3 scripts/build_connection_preview.py
python3 /Users/lichen/.codex/plugins/cache/openai-bundled/visualize/1.0.49/skills/visualize/scripts/render.py docs/design/flyji-connections.html /tmp/flyji-connections-preview.html --force
NODE_PATH=/Users/lichen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules FLYJI_QA_OUTPUT=/tmp/flyji-connections-new-check node docs/design/qa/connections-check.cjs
.venv/bin/python -m pytest -q
```

## Expand from this seed

| Next experiment | Customer question | Comparison / evidence needed |
|---|---|---|
| Stopover lengths and per-gap limits | Can I spend a day here without losing the rest of my route? | Exact not-before vs “stay one night”; retain prefix and explain suffix changes. |
| Multiple undo steps and optional downstream locks | Can I keep a later flight while trying a different earlier one? | Automatic rebuild vs explicit locks; expose conflicts rather than silently overriding a lock. |
| Airport-transfer inset | What does changing airports involve? | Source-backed ground options, processing allowance and uncertainty; geographic line alone is insufficient. |
| Choice density | How can I scan dozens of onward flights? | Native selector vs dated grouped rows vs local timetable; preserve exact identity and keyboard access. |
| Provider revision / stale choice | Did this route change since I picked it? | Snapshot comparison, cancelled/rescheduled flights, changed transfer assumptions, preserve old evidence. |
| Carrier/aircraft overlays | Can I investigate the operating service while editing? | Provider-backed data with unknowns, without turning the normal connection editor into a control wall. |

These remain experiments to validate across all three audiences. They are not approved features, implemented provider capabilities or a final default decision.
