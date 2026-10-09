# Flyji

Product name: **Flyji**. Intended hosting domain: **fly.boboji.fyi** (subdomain: `fly`). This naming decision was confirmed on October 9, 2026.

A provider-neutral engine for ingesting real commercial flight movements and exploring legitimate routes. It intentionally excludes fares, availability, booking, and OTA concerns.

## Architecture

```text
providers / CSV -> updater + ingestion -> SQLite <- reader <- serving API
                            ^                        ^
                            +-- internal admin CLI --+
```

The fetching layer (`src/ingestion/providers`) depends on upstream provider formats. The serving layer (`src/server/api.py`) depends only on `FlightReader`, which opens read-only SQLite connections. `src/ingestion/updater.py` owns initialization, upserts, deletion, and reset; `src/ingestion/service.py` orchestrates fetching; it is not imported by the serving API. Shared connection/schema helpers live in `src/core/storage.py`. This lets a hosted deployment replace AeroDataBox with OAG, Cirium, or another licensed feed without changing public API contracts.

## Package boundaries

```text
src/
  core/                 # shared library; no server/admin/ingestion imports
    models.py
    config.py
    storage.py          # SQLite schema, connections, timestamp helpers
    contracts.py        # query interface for route algorithms
    itineraries.py
  ingestion/            # internal crawler/updater package
    providers/          # AeroDataBox and CSV adapters
    service.py          # fetching, normalization, deduplication
    updater.py          # database initialization and mutation
  server/               # future webapp backend
    reader.py           # read-only queries
    api.py              # FastAPI serving
  admin/                # internal terminal client using both packages
    client.py
    __main__.py
```

`server` and `ingestion` depend on `core` and do not import each other. `admin` integrates both. All packages ship together for this prototype; they require no additional services. The default data path stays anchored to the repository's `var/` directory.

## Run locally

Development stays local: SQLite data lives at `var/flights.db`; `var/` is ignored by Git, including SQLite WAL sidecar files. Python uses its built-in `sqlite3` module; there is no database server or additional dependency. The default path is anchored to this project even when the client is launched from another directory. `FLIGHT_DATABASE_PATH` can override it.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
python -m admin stats  # initialize the local database
uvicorn server.api:app --reload
```

Set `FLIGHT_PROVIDER_API_KEY` to an AeroDataBox RapidAPI key. Swagger UI is at `http://localhost:8000/docs`.

## Local development client

The internal admin terminal client integrates both modules: `FlightReader` for queries/stats/export and `FlightUpdater` for initialization/import/ingestion/delete/reset. The itinerary engine uses only the reader. A future web app can use those same core modules through the API.

```bash
# Start the interactive client after installing the project
.venv/bin/python -m admin
# After pip install -e '.[dev]', the equivalent command is: flyji
```

Commands work both at the `flyji>` prompt and as arguments to `python -m admin`:

```text
stats
flights --origin YYZ --start 2026-10-10T00:00:00Z --end 2026-10-11T00:00:00Z
routes --origin YYZ --start 2026-10-10T00:00:00Z --end 2026-10-11T00:00:00Z
itineraries --origin YYZ --destination CDG --start 2026-10-10T00:00:00Z --end 2026-10-11T00:00:00Z --max-legs 3
ingest --csv var/feed.csv --airports YYZ YUL --start 2026-10-10 --end 2026-10-11
ingest --airports YYZ --start 2026-10-10 --end 2026-10-11
export-json var/flights-export.json
import-json var/flights-export.json
delete aerodatabox PROVIDER_ID
quit
```

Use `help` or `COMMAND --help` for options. To add or edit records, export JSON, edit it in your editor, then import it. Import validates the whole file before writing and updates records with matching `(provider, provider_id)`; omitted records are retained. CSV inputs use `FlightRecord` column names and must contain licensed commercial flight data. AeroDataBox ingestion requires a configured API key. Keep local imports and exports under `var/` so Git ignores them.

Reset the local data file with:

```bash
.venv/bin/python scripts/reset_local_data.py --yes
# Or inside the client: reset --yes
```

Reset deletes flight records in a transaction, preserving the schema, database file, imports, and exports. It only permits databases under this project's `var/` directory. Pause ingestion before resetting if you want the database to remain empty.

SQLite WAL mode lets separate Python processes share the database on the same machine: client reads use consistent snapshots while ingestion commits updates. Connections wait up to 10 seconds for locks; only one writer operates at a time. Provider requests finish before the ingestion service opens its write transaction. JSON/CSV remain import/export formats; do not edit the SQLite file directly.

To run a separate fetcher now, execute `.venv/bin/python -m admin ingest --airports YYZ --start 2026-10-10 --end 2026-10-11` in another terminal. It uses the same database as the client and API. This is a manual one-shot fetcher; an automatic refresh loop is not implemented yet.

Ingestion is internal and runs through the terminal client. The serving API exposes no ingestion or mutation endpoints. Initialize the database through the internal client before starting the API; the reader never creates a missing database.

Search real flights and aggregate routes:

```bash
curl 'http://localhost:8000/v1/flights?origin=YYZ&start=2026-10-01T00:00:00Z&end=2026-10-08T00:00:00Z'
curl 'http://localhost:8000/v1/routes?origin=YYZ&start=2026-10-01T00:00:00Z&end=2026-10-08T00:00:00Z'
curl 'http://localhost:8000/v1/itineraries?origin=YYZ&destination=CDG&start=2026-10-01T00:00:00Z&end=2026-10-08T00:00:00Z&max_legs=2'
```

## What “legitimate” means

A public route exists only when at least one non-cancelled commercial flight instance from an identified provider exists in the requested period. The service never fabricates airport pairs. Scheduled future flights and operated historical flights share one model; `status`, actual timestamps, provider, and `fetched_at` preserve the distinction and provenance.

## Production notes

- Confirm that the chosen provider contract permits caching and re-serving derived flight data. AeroDataBox coverage and rights depend on plan.
- Ingestion stays internal through the updater module and CLI; add a worker/scheduler when periodic refresh is needed.
- For future multi-instance deployment, consider replacing SQLite with a PostgreSQL repository if multiple hosts are needed. The reader/updater interfaces are the boundary.
- Refresh near-term scheduled flights often, and immutable historical flights less often. Keep provider-specific retention rules in the ingestion worker.
- Add API authentication, per-tenant quotas, request tracing, and a cache before exposing the service publicly.
- Airport-local timestamps are normalized from provider UTC when available. Clients should send offset-aware ISO 8601 timestamps.

## Test

GitHub uses the same branch protections and CI policy as `trip_dollar`: label-triggered PR tests, automatic tests on `main`, required current CI before merging, and linear development branches. See [.github/README.md](.github/README.md).

```bash
pytest
```
