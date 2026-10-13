# Flyji

Explore dated flight options, inspect their routes and schedules, and change a connection to see how the rest of the journey changes. Flyji runs locally with Python, FastAPI and SQLite. Its intended domain is **fly.boboji.fyi**.

This preview uses imported schedules. It does not establish seat availability, fares, booking protection or whether a transfer is suitable for a traveler. The demo data is synthetic; coverage is unknown.

## Try the demo

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/run_demo.py --port 8013
```

Open **http://127.0.0.1:8013/**. Select a route, inspect its legs on the globe or flat map, and scrub the schedule. Choose **Change connection**, widen the time window, allow an airport change, and apply a flight. Later legs are recalculated; **Undo** restores the previous journey. See the [demo walkthrough](docs/releases/local-preview/demo.md).

The demo uses `var/connections-demo.db`, separate from the normal `var/flights.db`. Both files and their SQLite sidecars are ignored by Git. Stop the server with Ctrl+C. If the port is busy, choose another with `--port`.

## What works

| Capability | Current behavior |
|---|---|
| Search | Typed airport codes with suggestions, a required via airport, local or UTC dates, arrival deadline, connection bounds, avoided airports and deterministic ranking |
| Explore | Linked route list, globe/flat map, leg details, local clocks and a schedule cursor |
| Edit | Keep a selected prefix, type the next airport, choose a later flight, rebuild subsequent legs, show dead ends and undo |
| Airport changes | Opt-in, same-city changes with an explicit directed allowance in SQLite |
| Inspect | Coverage/refresh evidence, bounded search diagnostics, exact exports and offline routing replay |
| Manage data | Internal shell, validated JSON/CSV imports, exports, ingestion and guarded reset |

The [research register](docs/research/README.md) maps the wider exploration needs and proposed experiments. Multiple pinned comparisons, protected commitments and further interaction designs are proposals. Customer sessions and selection of a final visual direction remain open; three directions and their reviews are retained in the [design archive](docs/design/README.md).

## Architecture

```text
provider / CSV → ingestion + updater → SQLite ← reader ← FastAPI + web app
                              ↑                   ↑
                              └── internal admin ─┘
```

`src/core` holds shared models and the routing library. `src/ingestion` owns fetching and mutation. `src/server` owns read-only queries, the API and bundled web assets. `src/admin` integrates both modules for local management. Boundary tests enforce this separation. Trip Dollar keeps Flask; Flyji keeps FastAPI with Uvicorn.

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

## Verify and review

```sh
python -m pytest -q
ruff check src tests scripts --exclude scripts/verify_research.py
ruff format --check src tests scripts --exclude scripts/verify_research.py
python scripts/verify_research.py --require-reviews
```

The [release audit](docs/releases/local-preview/README.md) records current source identities, test evidence and review scope. Historical review bundles remain unchanged. GitHub uses label-triggered PR checks, automatic checks on `main`, a required current check and linear branch history; see the [CI policy](.github/README.md).

This milestone is a local preview. Final customer design approval, real-data validation, accessibility certification and public deployment controls remain future work.
