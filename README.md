# Flight Routes Engine

A provider-neutral engine for ingesting real commercial flight movements and exploring legitimate routes. It intentionally excludes fares, availability, booking, and OTA concerns.

## Architecture

```text
licensed flight API / bulk feed -> provider adapter -> normalized flight store -> public query API
                                                ^
                                      admin-only ingestion API
```

The fetching layer (`flight_engine/providers`) depends on upstream provider formats. The serving layer (`flight_engine/api.py`) only depends on normalized records in `FlightStore`. This lets a hosted deployment replace AeroDataBox with OAG, Cirium, or another licensed feed without changing public API contracts.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn flight_engine.api:app --reload
```

Set `FLIGHT_PROVIDER_API_KEY` to an AeroDataBox RapidAPI key. Swagger UI is at `http://localhost:8000/docs`.

Ingest a user-defined range (admin-only):

```bash
curl -X POST http://localhost:8000/v1/admin/ingestions \
  -H 'content-type: application/json' \
  -H 'x-admin-token: change-me' \
  -d '{"airports":["YYZ","YUL"],"start":"2026-10-01","end":"2026-10-07"}'
```

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
- Put ingestion on a worker/scheduler, not public request paths; the admin endpoint is intended for controlled jobs.
- Replace SQLite with a PostgreSQL repository for multi-instance deployment. The `FlightStore` interface is the boundary.
- Refresh near-term scheduled flights often, and immutable historical flights less often. Keep provider-specific retention rules in the ingestion worker.
- Add API authentication, per-tenant quotas, request tracing, and a cache before exposing the service publicly.
- Airport-local timestamps are normalized from provider UTC when available. Clients should send offset-aware ISO 8601 timestamps.

## Test

```bash
pytest
```
