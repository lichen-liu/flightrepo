# On-demand itinerary storage and disk quota

The backend downloads normalized flight-leg records, including aircraft type,
from an itinerary catalogue. It never downloads ADS-B position data for serving.
The browser calls the usual search, date, itinerary and rebuild APIs.

The source catalogue persists route/date coverage and airport metadata locally.
Flight payloads are immutable SQLite chunk files beside the main database, under
`flights.db.itineraries/`. A missing or retired chunk is downloaded inside the
backend when a query needs it. Search loads onward origin/window chunks as it
explores the same graph as local search; connections and ranking remain in core.

## Live admin controls

Initialize or upgrade the selected database once with `maintenance init`, then:

```text
flyji> cache source https://your-itinerary-host.example/catalog.json
flyji> cache quota 20GiB
flyji> cache status
flyji> cache quota 5GiB
flyji> cache quota
flyji> cache quota off
```

The default quota is 20 GiB. Sizes accept bytes, KiB, MiB, GiB or TiB, including
fractional values. `off` disables the ceiling; `0` requests no retained flight
payloads. `cache source --off` disables downloading. `cache refresh` reloads
source metadata on the next data access; source metadata otherwise persists
through restarts and does not require a network request on every search.

Policy is stored in the selected SQLite database. Another admin process can
change it while the server is running:

```sh
python -m admin --db /absolute/path/flights.db cache quota 5GiB
```

Changing the quota updates only policy. It does not synchronously sweep the
cache. The next normal database IO or cache access checks the current policy and
retires the least recently used eligible chunks. `cache status` also performs
that check. Raising the quota or disabling it takes effect without restarting.

An API deployment may instead supply `FLIGHT_ITINERARY_CATALOG_URL` as its initial
source URL. The persisted admin source setting takes precedence, including an
explicit `--off`. The quota is deliberately not read from a cached environment
setting: the database value is consulted on each storage boundary.

## Accounting and retirement

Quota usage includes the main SQLite file, its WAL/SHM files, downloaded SQLite
chunks and temporary chunk files, measured by file length. Source archives,
user exports, research files and unrelated files outside this cache are excluded.
One immutable chunk is one LRU unit. A transactional increasing access sequence
provides ordering across processes and avoids wall-clock changes or ties.

Below the threshold, misses download freely. Files currently read or built are
protected by cross-process file locks, and the same chunk is downloaded once
when requests overlap. Retirement skips busy chunks; any remaining excess is
checked again on later IO. Active operations can temporarily exceed the quota.
Results already copied into a routing snapshot remain valid after retirement.

Deleting a whole chunk actually releases its file space. Checkpointing can also
release metadata WAL space; a reader holding WAL frames can defer that release
until subsequent IO. There is no background sweeper or full-database VACUUM.

The persistent source index, flight reference locators, deletion markers, manual
records, edited/imported flight facts, airports and transfers are protected. They
are needed to preserve lookup, routing, local editing and catalogue coverage.
If they or active readers alone exceed the configured threshold, eviction cannot
meet it without losing data. `cache status` reports the remaining
`over_quota_bytes`; it never deletes those facts silently. A zero quota therefore
means zero retained payloads, not a zero-byte SQLite database.

Manual edits to downloaded flights are promoted to durable local records.
Deletes leave a durable marker so a later download cannot resurrect the flight.
An explicit import restores a deleted identity. Optimistic edit checks include
the source catalogue version. `maintenance clear-data --yes` disables the source
and clears the cache registry so the next read does not refill the cleared DB.

Calendar dates come from source metadata even when all payloads are retired.
Local edits/deletes or changed timezone metadata are reconciled against actual
records when necessary. A source error returns HTTP 503 instead of an exhaustive
empty result. Cached valid chunks remain usable during a source outage, until
they are retired. An evicted chunk needs the upstream source to be available.

## Source contract

The downloader consumes `ItineraryCatalog` from `core/itinerary_catalog.py`:

- `format_version: 1`, airport metadata and directed transfers.
- Chunk URLs, SHA-256 and encoded byte lengths.
- Provider/origin and disjoint UTC departure windows for each chunk.
- Destinations, counts, cancellation counts, refresh evidence and UTC/local dates.
- Each JSON or gzip payload contains `{ "flights": [FlightRecord, ...] }`.

These records preserve flight identity, airline/codeshares, scheduled and actual
times, status, refresh timestamp and optional `aircraft_type_code`. Downloaded
bytes, identities, scope and advertised coverage are validated before a chunk
becomes visible. Only normalized flight columns are stored, with query indexes;
source JSON and its compressed payload are not retained as duplicate files.

To build a source from existing normalized records and an airport catalogue:

```sh
PYTHONPATH=src python scripts/build_itinerary_catalog.py flights.json \
  --airports airports.json --output var/itinerary-source \
  --base-url https://your-itinerary-host.example
```

Host the generated `catalog.json` and content-addressed `.json.gz` files over
HTTP(S), then configure their catalogue URL. The builder validates identities and
groups records by provider, origin and UTC month, splitting busy months into
disjoint windows of at most 20,000 records. Monthly grouping keeps a global
year's metadata smaller than an airport/day catalogue. The format requires scheduled
flight facts; it does not invent schedules from ADSB.lol observations.

## Verified behavior and remaining source work

Tests compare downloaded itineraries with the same facts stored wholly locally,
including connections, aircraft types and rebuilding after eviction. They cover
lazy quota changes in a separate process, LRU order, active-file protection,
concurrent cache hits, corrupt/missing sources, local edits/deletes, calendar
coverage, streamed export and reset behavior.

A real HTTP test used the synthetic demo catalogue, not a real 2025 feed. Seven
chunks remained immediately after a separate admin process set the quota to zero.
The next date query retired all seven, while the departure dates remained
available. Searching again downloaded the required itinerary chunks and returned
the same six itineraries with aircraft type B738. No payload files remained
after that zero-retention search; the protected database occupied 122,880 bytes.

The [offline ingestion pipeline](ingestion.md) extracts observed ADSB.lol legs
separately. It needs an observed-time serving contract before it can feed search;
these records cannot be inserted into this scheduled catalogue format as-is.
