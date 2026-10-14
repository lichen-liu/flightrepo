# Flyji

Explore dated flight options, inspect their routes and schedules, and change a connection to see how the rest of the journey changes. Flyji runs locally with Python, FastAPI and SQLite. Its intended domain is **fly.boboji.fyi**.

Flyji shares boboji.fyi's fonts, colors and branding with Trip Split and AeroRepo. Fonts and map assets are bundled locally.

This preview uses imported schedules. It does not establish seat availability, fares, booking protection or whether a transfer is suitable for a traveler. The demo data is synthetic; coverage is unknown.

## Try the demo

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/run_demo.py --port 8013
```

Open **http://127.0.0.1:8013/**. Select a route, inspect its legs on the globe or flat map, and scrub the schedule. Choose **Change connection**, widen the time window, allow an airport change, and apply a flight. Later legs are recalculated; **Undo** restores the previous journey. See the [demo walkthrough](docs/demo.md).

The demo uses `var/connections-demo.db`, separate from the normal `var/flights.db`. Both files and their SQLite sidecars are ignored by Git. Stop the server with Ctrl+C. If the port is busy, choose another with `--port`.

To host the installed preview at **fly.boboji.fyi**, follow [installation](installation/README.md) and [deployment](deployment/README.md). The first hosted version uses a separate synthetic database under `var/hosted/`; building the real flight database is the next phase.

## What works

| Capability | Current behavior |
|---|---|
| Search | Typed airport codes with suggestions, ordered via airports, local or UTC departure dates, connection bounds, avoided airports and deterministic ranking |
| Explore | Linked route list, globe/flat map, leg details, local clocks and a schedule cursor |
| Edit | Keep a selected prefix, type the next airport, choose a later flight, rebuild subsequent legs, show dead ends and undo |
| Airport changes | Opt-in, same-city changes with an explicit directed allowance in SQLite |
| Inspect | Coverage/refresh evidence, bounded search diagnostics, exact exports and offline routing replay |
| Manage data | Internal shell, validated JSON/CSV imports, exports, ingestion and guarded reset |

## Architecture

```text
provider / CSV → ingestion + updater → SQLite ← reader ← FastAPI + web app
                              ↑                   ↑
                              └── internal admin ─┘
```

`src/core` holds shared models and the routing library. `src/ingestion` owns fetching and mutation. `src/server` owns read-only queries, the API and bundled web assets. `src/admin` integrates both modules for local management. Boundary tests enforce this separation.

SQLite requires no database service. WAL mode permits separate readers and one writer on the same machine; each calculation uses a consistent snapshot. Ingestion completes provider requests before writing. Routing uses bounded searches with explicit partial-result diagnostics; the [engine guide](docs/engine/routing.md) covers ranking, limits and replay.

## Use your local data

```sh
cp .env.example .env
python -m admin stats
uvicorn server.api:app --reload --host 127.0.0.1 --port 8001
```

The app is at `http://127.0.0.1:8001/`, interactive API docs at `/docs`, and the OpenAPI schema at `/openapi.json`. The API exposes queries and calculations; data changes go through the internal client. Use an absolute `FLIGHT_DATABASE_PATH` when running outside an editable checkout.

Start the shell with `python -m admin` or `flyji`. Commands also work directly:

```sh
python -m admin import-json var/flights.json
python -m admin import-airports var/airports.json
python -m admin export-json var/flights-export.json
python -m admin export-airports var/airport-catalog.json
python -m admin ingest --csv var/feed.csv --airports YYZ YUL --start 2026-10-17 --end 2026-10-18
python -m admin flights --origin YYZ --start 2026-10-17T00:00:00Z --end 2026-10-18T00:00:00Z
```

Use `help` in the shell or `COMMAND --help` for options. JSON flight imports are arrays of `FlightRecord` objects; matching `(provider, provider_id)` records are updated and omitted records retained. Airport catalogs contain `airports` and optional directed `transfers`. Imports validate the entire batch before writing. CSV date bounds use UTC departure dates; timestamps require offsets. Keep working files under `var/`.

AeroDataBox ingestion requires `FLIGHT_PROVIDER_API_KEY`. Provider coverage, caching and redistribution rights must be checked for your account. Automatic refresh is not implemented; run `ingest` separately to update the same database while readers remain connected.

```sh
python scripts/reset_local_data.py --yes
```

Reset clears flights, airport metadata and transfer rules transactionally, preserving the database schema and other files. It only permits databases under this project's `var/`. Pause ingestion first if the database must stay empty.

## Development checks

```sh
python -m pytest -q
ruff check src tests scripts installation deployment
ruff format --check src tests scripts installation deployment
node --test tests/test_frontend.mjs
python -m build --wheel --outdir var/wheels
python scripts/verify_package.py var/wheels/*.whl
```

The wheel check installs the built package in a temporary environment and probes its CLI, API and bundled assets from outside the checkout. It reuses the current environment's third-party dependencies.

For browser tests, start the demo in another terminal, then run:

```sh
npm install --prefix var/browser-qa --no-save playwright@1.62.1
var/browser-qa/node_modules/.bin/playwright install chromium
NODE_PATH=var/browser-qa/node_modules node scripts/qa_web.cjs
```

Browser tests check search, editing, undo, error recovery, keyboard access and responsive use against the real API. They compare flight identities and constraints rather than saved screenshots or exact presentation strings. Failed cases save a screenshot under ignored `var/web-qa/`.

Keep generated reports, screenshots, replay files and build outputs under `var/`. GitHub uses label-triggered PR checks and automatic checks on `main`; see the [CI policy](.github/README.md).
